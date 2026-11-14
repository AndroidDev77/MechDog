// smooth_gait.ino - full replacement firmware for the Hiwonder MechDog body.
//
// What's ours:   gait.h (smooth trot + body pose), hardware.h (sensors), ble_link.h (Bluetooth),
//                and the command handling below.
// What's theirs: quad_kinematics (MechDog_Arduino library) for leg IK + angle->pulse mapping;
//                pwm_servo.cpp/.h + Servo.cpp/.h for the servo outputs and your calibration
//                offsets; action.h for the tricks. Copy those four files plus action.h in from
//                any of their Arduino examples (see firmware/README.md). Without action.h the
//                firmware still builds; tricks just report an error.
//
// Safety, always on: stops walking forward if something is closer than 15 cm, stops if the
// computer goes quiet for 1 s (CMD|11 walking) or Bluetooth drops, and refuses to walk when the
// battery is low. Saying stop (CMD|10 or 's') also cancels a trick in progress.
//
// Commands arrive over USB serial (115200) or Bluetooth LE (as MechDog_XX).
//
// Machine frames, the same as dog/main.py so dogbrain works unchanged:
//   CMD|10|$                          stop (also aborts a trick)
//   CMD|11|<mm/s>|<deg/s>|$           walk; + = forward / left. Stops if no frame of any kind
//                                     arrives for 1 s (status polls count as heartbeats)
//   CMD|12|<action_name>|$            trick, by Hiwonder's name (sit_dowm, handshake, ...),
//                                     plus rotation_roll / rotation_pitch (smooth wiggles)
//   CMD|13|<roll>|<pitch>|<height>|$  body pose: degrees, degrees, body height mm
//   CMD|14|$                          default pose
//   CMD|15|$                          status -> CMD|15|<dist_mm>|<roll>|<pitch>|<battery_mv>|<state>|$
//   CMD|16|<r>|<g>|<b>|$              eye LED colour
//   CMD|17|<swing_ms>|<stand_ms>|<step_mm>|$   gait timing
//   CMD|18|$                          ping -> CMD|18|ok|$
//   events: CMD|19|blocked|<dist_mm>|$  CMD|19|watchdog|0|$  CMD|19|done|<name>|$
//           CMD|19|low_battery|<mv>|$  CMD|19|error|<reason>|$
//
// Typed commands on the serial monitor, for tuning:
//   w <mm/s> <deg/s>   walk (auto-stops after 10 s)     s      stop
//   g <swing> <stand> <step>   gait timing             h <mm> body height
//   t <roll> <pitch>   body tilt                       a <mm/s2> acceleration
//   act <name>         trick                           led <r> <g> <b>
//   home               default pose                    p      status

#include "quad_kinematics.h"
#include "pwm_servo.h"
#include "gait.h"
#include "hardware.h"
#include "ble_link.h"
#include <freertos/queue.h>

#if __has_include("action.h")
#include "action.h"
#define HAVE_ACTIONS 1
#else
#define HAVE_ACTIONS 0
#endif

static const uint32_t TICK_MS = (uint32_t)gait::TICK_MS;   // 20 ms, 50 Hz
static const uint32_t WATCHDOG_MS = 1000;                  // CMD|11 must repeat this often
static const uint32_t MANUAL_WALK_MS = 10000;              // typed "w" commands
static const int OBSTACLE_MM = 150;                        // won't walk forward closer than this
static const int OBSTACLE_CLEAR_MM = 220;                  // ...until it's at least this far
static const int LOW_BATTERY_MV = 6800;                    // 2S pack; Hiwonder alarms at 7000
static const int NO_BATTERY_MV = 3000;                     // below this we're on USB power only
static const float BOOT_BODY_Z = 30;                       // tested tall stance

enum Channel : uint8_t { CH_SERIAL = 1, CH_BLE = 2 };

quad_kinematics kin;
gait::Trot trot;

// ---------------------------------------------------------------- shared state
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
volatile float pend_vx = 0, pend_yaw = 0;
volatile bool pend_cmd = false, pend_params = false, pend_home = false;
volatile int pend_action = -1;
gait::Params pend_p;
volatile uint32_t walk_deadline = 0;
volatile bool frame_walk = false;   // walking was started by a CMD|11 frame (watchdog applies)
volatile uint32_t ik_errors = 0;
volatile bool action_active = false, abort_action = false;
volatile bool blocked = false, low_battery = false;
volatile uint32_t user_led = 0x0040FF;

struct Event { char text[48]; };
QueueHandle_t events;            // control task -> loop(), which sends them out

static void post_event(const char* name, const char* detail) {
  Event e;
  snprintf(e.text, sizeof(e.text), "CMD|19|%s|%s|$", name, detail);
  xQueueSend(events, &e, 0);
}
static void post_event_int(const char* name, int v) {
  char d[12];
  snprintf(d, sizeof(d), "%d", v);
  post_event(name, d);
}
static void led_hex(uint32_t rgb) { hw::set_led(rgb >> 16, rgb >> 8, rgb); }
static uint32_t to_byte(long v) { return v < 0 ? 0 : (v > 255 ? 255 : (uint32_t)v); }

// ---------------------------------------------------------------- tricks
// Two built-in tricks that don't need action.h: smooth body wiggles using the gait's pose
// easing (they were built into Hiwonder's MicroPython firmware as rotation_roll/_pitch).
static const int WIGGLE_ROLL = 1000, WIGGLE_PITCH = 1001;
#if HAVE_ACTIONS
static const int NUM_ACTIONS = sizeof(aclist) / sizeof(aclist[0]);
#endif
static int find_action(const char* name) {
  if (strcmp(name, "rotation_roll") == 0) return WIGGLE_ROLL;
  if (strcmp(name, "rotation_pitch") == 0) return WIGGLE_PITCH;
#if HAVE_ACTIONS
  for (int i = 0; i < NUM_ACTIONS; i++)
    if (strcmp(name, aclist[i].name) == 0) return i;
#endif
  return -1;
}

// ---------------------------------------------------------------- legs
// Send the gait's foot targets through Hiwonder's IK.
static void write_feet(uint32_t duration_ms) {
  for (int i = 0; i < 4; i++) {
    mech_point_t pt = {trot.foot[i].x, trot.foot[i].y, trot.foot[i].z};
    float th[3] = {0, 0, 0};
    // leg_set_theta silently skips a servo whose angle is out of range (a twitch). Check
    // first and hold that leg's last pose instead.
    if (kin.leg_inverse_kinematics_offset(i, pt, th) != 0 || th[0] < 0 || th[0] > PI ||
        th[1] > 0 || th[1] < -PI) {
      ik_errors++;
      continue;
    }
    kin.leg_set_theta(i, th, duration_ms);
  }
}

#if HAVE_ACTIONS
// Play one of Hiwonder's keyframed tricks (8 servo pulses per frame), then ease back to the
// gait's standing pose. Returns false if aborted.
static bool play_action(int idx) {
  const ActionsList& a = aclist[idx];
  for (int f = 0; f < a.actionCount; f++) {
    const mech_action& fr = a.actions[f];
    for (int k = 0; k < 8; k++) pwm_servo_set_position(k, fr.Servos[k], fr.Duration);
    for (uint32_t t = 0; t < fr.Duration; t += TICK_MS) {
      if (abort_action) return false;
      vTaskDelay(pdMS_TO_TICKS(TICK_MS));
    }
  }
  return true;
}
#endif

// ---------------------------------------------------------------- the control loop
static void control_task(void*) {
  TickType_t last = xTaskGetTickCount();
  bool was_active = false;
  uint32_t low_since = 0, ok_since = 0;
  int wiggle_axis = 0, wiggle_step = 0;      // 0 = none, 1 = roll, 2 = pitch
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(TICK_MS));
    const uint32_t now = millis();

    portENTER_CRITICAL(&mux);
    bool do_cmd = pend_cmd, do_params = pend_params, do_home = pend_home;
    float vx = pend_vx, yaw = pend_yaw;
    gait::Params np = pend_p;
    int act = pend_action;
    pend_cmd = pend_params = pend_home = false;
    uint32_t deadline = walk_deadline;
    portEXIT_CRITICAL(&mux);

    // ---- battery: low for 5 s -> lock out walking; back above +300 mV for 5 s -> unlock
    const int mv = hw::state.battery_mv;
    if (mv > NO_BATTERY_MV && mv < LOW_BATTERY_MV) {
      ok_since = 0;
      if (!low_since) low_since = now;
      if (!low_battery && now - low_since > 5000) {
        low_battery = true;
        trot.command(0, 0);
        post_event_int("low_battery", mv);
        hw::set_led(255, 100, 0);
        hw::beep(1000, 250, 3);
      }
    } else {
      low_since = 0;
      if (low_battery && (mv <= NO_BATTERY_MV || mv > LOW_BATTERY_MV + 300)) {
        if (!ok_since) ok_since = now;
        if (now - ok_since > 5000) {
          low_battery = false;
          led_hex(user_led);
        }
      }
    }

    // ---- wiggles: step through tilt targets, letting the pose easing do the motion
    if (wiggle_axis && !trot.posing()) {
      static const float seq[] = {10, -10, 10, -10, 0};
      gait::Params p = trot.p;
      if (abort_action || wiggle_step >= 5) {
        p.roll_deg = p.pitch_deg = 0;
        trot.set_params(p);
        if (!abort_action) post_event("done", wiggle_axis == 1 ? "rotation_roll" : "rotation_pitch");
        else post_event("error", "aborted");
        wiggle_axis = 0;
        action_active = false;
      } else {
        (wiggle_axis == 1 ? p.roll_deg : p.pitch_deg) = seq[wiggle_step++];
        p.clamp_tilt();
        trot.set_params(p);
      }
    }

    // ---- tricks: wait until the gait has settled, then play
    if (act == WIGGLE_ROLL || act == WIGGLE_PITCH) {
      trot.command(0, 0);
      if (!trot.running) {
        pend_action = -1;
        action_active = true;
        abort_action = false;
        wiggle_axis = (act == WIGGLE_ROLL) ? 1 : 2;
        wiggle_step = 0;
      }
    } else if (act >= 0) {
      trot.command(0, 0);
      if (!trot.running) {
#if HAVE_ACTIONS
        pend_action = -1;
        action_active = true;
        abort_action = false;
        bool ok = play_action(act);
        trot.reset();                              // gait pose = current targets, feet home
        write_feet(600);                           // ease back to standing
        vTaskDelay(pdMS_TO_TICKS(650));
        action_active = false;
        if (ok) post_event("done", aclist[act].name);
        else post_event("error", "aborted");
        last = xTaskGetTickCount();
        was_active = false;
        continue;
#endif
      }
    }

    // ---- commands
    if (do_params) trot.set_params(np);
    if (do_home) {
      gait::Params p = trot.p;
      p.roll_deg = p.pitch_deg = 0;
      p.body_z_mm = BOOT_BODY_Z;
      trot.set_params(p);
      trot.command(0, 0);
    }
    if (do_cmd) {
      if (low_battery && (vx != 0 || yaw != 0)) post_event("error", "low_battery");
      else trot.command(vx, yaw);
    }
    if ((trot.cmd_vx != 0 || trot.cmd_yaw != 0) && (int32_t)(now - deadline) > 0) {
      trot.command(0, 0);
      post_event("watchdog", "0");
    }

    // ---- obstacle: never walk forward into something; turning and backing up still work
    const int dist = hw::state.distance_mm;
    if (hw::state.sonar_ok && dist < OBSTACLE_MM && trot.cmd_vx > 0) {
      trot.command(0, trot.cmd_yaw);
      if (!blocked || do_cmd) post_event_int("blocked", dist);
      if (!blocked) {
        blocked = true;
        hw::set_led(255, 0, 0);
        hw::beep(2500, 60, 2);
      }
    } else if (blocked && (dist > OBSTACLE_CLEAR_MM || !hw::state.sonar_ok)) {
      blocked = false;
      if (!low_battery) led_hex(user_led);
    }

    // ---- step the gait and move the legs
    trot.step(TICK_MS);
    const bool active = trot.running || trot.posing();
    if (active || was_active) write_feet(TICK_MS);   // one last write after settling
    was_active = active;
  }
}

// ---------------------------------------------------------------- command helpers (loop task)
static void walk(float vx, float yaw_deg, uint32_t timeout_ms) {
  portENTER_CRITICAL(&mux);
  pend_vx = vx;
  pend_yaw = yaw_deg * PI / 180.0f;
  pend_cmd = true;
  walk_deadline = millis() + timeout_ms;
  portEXIT_CRITICAL(&mux);
}
static void stop_all() {
  abort_action = true;
  portENTER_CRITICAL(&mux);
  pend_action = -1;
  portEXIT_CRITICAL(&mux);
  walk(0, 0, 0);
}
static void set_params(const gait::Params& p) {
  portENTER_CRITICAL(&mux);
  pend_p = p;
  pend_params = true;
  portEXIT_CRITICAL(&mux);
}
static bool start_action(const char* name) {
  int idx = find_action(name);
  if (idx < 0) return false;
  abort_action = false;
  portENTER_CRITICAL(&mux);
  pend_action = idx;
  portEXIT_CRITICAL(&mux);
  return true;
}
static const char* state_name() {
  if (action_active || pend_action >= 0) return "action";
  if (blocked) return "blocked";
  if (trot.running) return (trot.cmd_vx == 0 && trot.cmd_yaw == 0) ? "stopping" : "walking";
  return "idle";
}

static void send(uint8_t ch, const char* s) {
  if (ch & CH_SERIAL) { Serial.print(s); Serial.print('\n'); }
  if (ch & CH_BLE) blelink::send(s);
}

static void handle_frame(uint8_t ch, const String& body) {   // body = "11|80|0"
  // Like dog/main.py, ANY frame from the computer counts as a heartbeat for frame-driven
  // walking (dogbrain sends CMD|11 once, then CMD|15 status polls every 0.3 s).
  if (frame_walk) {
    portENTER_CRITICAL(&mux);
    walk_deadline = millis() + WATCHDOG_MS;
    portEXIT_CRITICAL(&mux);
  }
  String f[4];
  int code = body.toInt(), n = 0, start = body.indexOf('|');
  while (start >= 0 && n < 4) {
    int next = body.indexOf('|', start + 1);
    f[n++] = body.substring(start + 1, next < 0 ? body.length() : next);
    start = next;
  }
  char out[96];
  gait::Params p = trot.p;
  switch (code) {
    case 10: stop_all(); break;
    case 11:
      if (action_active || pend_action >= 0) send(ch, "CMD|19|error|busy|$");
      else { frame_walk = true; walk(f[0].toFloat(), f[1].toFloat(), WATCHDOG_MS); }
      break;
    case 12:
      if (low_battery) send(ch, "CMD|19|error|low_battery|$");
      else if (!start_action(f[0].c_str())) send(ch, "CMD|19|error|unknown_action|$");
      break;
    case 13:
      p.roll_deg = f[0].toFloat(); p.pitch_deg = f[1].toFloat(); p.body_z_mm = f[2].toFloat();
      p.clamp();
      set_params(p);
      break;
    case 14: stop_all(); pend_home = true; break;
    case 15:
      snprintf(out, sizeof(out), "CMD|15|%d|%.1f|%.1f|%d|%s|$", (int)hw::state.distance_mm,
               (float)hw::state.roll, (float)hw::state.pitch, (int)hw::state.battery_mv,
               state_name());
      send(ch, out);
      break;
    case 16:
      user_led = (to_byte(f[0].toInt()) << 16) | (to_byte(f[1].toInt()) << 8) | to_byte(f[2].toInt());
      if (!blocked && !low_battery) led_hex(user_led);
      break;
    case 17:
      p.swing_ms = f[0].toFloat(); p.stand_ms = f[1].toFloat(); p.height_mm = f[2].toFloat();
      p.clamp();
      set_params(p);
      break;
    case 18: send(ch, "CMD|18|ok|$"); break;
    default: send(ch, "CMD|19|error|unknown_code|$"); break;
  }
}

static void print_status() {
  Serial.printf("state=%s vx=%.0f yaw=%.2f | swing=%.0f stand=%.0f step=%.0f accel=%.0f | "
                "body_z=%.0f roll=%.1f pitch=%.1f (targets %.0f/%.1f/%.1f) | dist=%dmm%s "
                "imu_roll=%.1f imu_pitch=%.1f%s battery=%dmV%s | ble=%s %s | ik_errors=%u%s\n",
                state_name(), trot.vx, trot.yaw, trot.p.swing_ms, trot.p.stand_ms,
                trot.p.height_mm, trot.p.accel, trot.body_z(), trot.roll(), trot.pitch(),
                trot.p.body_z_mm, trot.p.roll_deg, trot.p.pitch_deg, (int)hw::state.distance_mm,
                hw::state.sonar_ok ? "" : " (sonar not found)", (float)hw::state.roll,
                (float)hw::state.pitch, hw::state.imu_ok ? "" : " (imu not found)",
                (int)hw::state.battery_mv, low_battery ? " LOW" : "",
                blelink::connected ? "connected" : "advertising", blelink::device_name, ik_errors,
                HAVE_ACTIONS ? "" : " | no action.h: tricks disabled");
}

static void handle_line(String line) {
  line.trim();
  if (line.length() == 0) return;
  float a = 0, b = 0, c = 0;
  int sp = line.indexOf(' ');
  String word = sp < 0 ? line : line.substring(0, sp);
  String rest = sp < 0 ? String("") : line.substring(sp + 1);
  int n = sscanf(rest.c_str(), "%f %f %f", &a, &b, &c);
  gait::Params p = trot.p;
  if (word == "home") { stop_all(); pend_home = true; Serial.println("ok home"); }
  else if (word == "w" && n >= 1) {
    frame_walk = false;
    walk(a, n >= 2 ? b : 0, MANUAL_WALK_MS);
    Serial.println("ok walk");
  }
  else if (word == "s") { stop_all(); Serial.println("ok stop"); }
  else if (word == "g" && n == 3) {
    p.swing_ms = a; p.stand_ms = b; p.height_mm = c; p.clamp(); set_params(p);
    Serial.printf("ok gait swing=%.0f stand=%.0f step=%.0f\n", p.swing_ms, p.stand_ms, p.height_mm);
  }
  else if (word == "h" && n == 1) {
    p.body_z_mm = a; p.clamp(); set_params(p);
    Serial.printf("ok body height %.0f\n", p.body_z_mm);
  }
  else if (word == "t" && n == 2) {
    p.roll_deg = a; p.pitch_deg = b; p.clamp(); set_params(p);
    Serial.printf("ok tilt roll %.1f pitch %.1f\n", p.roll_deg, p.pitch_deg);
  }
  else if (word == "a" && n == 1) { p.accel = a; set_params(p); Serial.println("ok accel"); }
  else if (word == "act") {
    Serial.println(start_action(rest.c_str()) ? "ok trick"
                   : HAVE_ACTIONS ? "unknown trick"
                   : "unknown trick (only rotation_roll / rotation_pitch without action.h)");
  }
  else if (word == "led" && n == 3) {
    user_led = (to_byte((long)a) << 16) | (to_byte((long)b) << 8) | to_byte((long)c);
    led_hex(user_led);
    Serial.println("ok led");
  }
  else if (word == "p") print_status();
  else Serial.println("? w <mm/s> <deg/s> | s | g <swing> <stand> <step> | h <mm> | t <roll> <pitch>"
                      " | a <mm/s2> | act <name> | led <r> <g> <b> | home | p");
}

// Frame + line parser, one per channel.
struct Parser {
  String buf;
  void feed(uint8_t ch, char c, bool allow_lines) {
    buf += c;
    int s = buf.indexOf("CMD|");
    int e = (s >= 0) ? buf.indexOf('$', s) : -1;
    if (s >= 0 && e > s) {
      String body = buf.substring(s + 4, e);
      if (body.endsWith("|")) body.remove(body.length() - 1);
      handle_frame(ch, body);
      buf = buf.substring(e + 1);
    } else if (s < 0 && (c == '\n' || c == '\r')) {
      if (allow_lines) handle_line(buf);
      buf = "";
    }
    if (buf.length() > 160) buf = "";
  }
};

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  events = xQueueCreate(16, sizeof(Event));
  pwm_servo_init();                    // attaches servos, loads your calibration offsets
  kin.begin(pwm_servo_set_position);   // their IK + 50 Hz pose timer (idle unless asked)
  hw::start();                         // sonar, IMU, battery, buzzer, LEDs (sensor task)
  delay(300);
  // Boot settings: tall stance, long steps (stride = speed x stand, so 62 mm at the 100 mm/s cap)
  trot.p.body_z_mm = BOOT_BODY_Z;
  trot.p.swing_ms = 300;
  trot.p.stand_ms = 620;
  trot.p.max_speed = 100;
  trot.reset();
  write_feet(1500);                    // stand up gently
  delay(1600);
  xTaskCreatePinnedToCore(control_task, "gait", 6144, nullptr, 5, nullptr, 1);
  blelink::start();
  led_hex(user_led);
  hw::beep(2000, 60, 2);
  Serial.printf("MechDog ready as %s. Type 'p' for status, 'w 60 0' to walk, 's' to stop.\n",
                blelink::device_name);
}

void loop() {
  static Parser serial_parser, ble_parser;
  while (Serial.available()) serial_parser.feed(CH_SERIAL, (char)Serial.read(), true);

  char buf[64];
  size_t n;
  while ((n = blelink::read(buf, sizeof(buf))) > 0)
    for (size_t i = 0; i < n; i++) ble_parser.feed(CH_BLE, buf[i], false);

  if (blelink::just_connected) {
    blelink::just_connected = false;
    hw::beep(2600, 40, 1);
    Serial.println("bluetooth connected");
  }
  if (blelink::just_disconnected) {
    blelink::just_disconnected = false;
    stop_all();                        // lost the brain: stop
    Serial.println("bluetooth disconnected: stopping");
  }

  Event e;
  while (xQueueReceive(events, &e, 0) == pdTRUE) send(CH_SERIAL | CH_BLE, e.text);
  delay(2);
}
