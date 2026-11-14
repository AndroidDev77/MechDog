// smooth_gait.ino - smooth trot for Hiwonder MechDog, using Hiwonder's own IK and servo driver.
//
// What's ours:   gait.h (foot trajectories, timing, velocity ramps) and the command interface.
// What's theirs: quad_kinematics (MechDog_Arduino library) for leg IK + angle->pulse mapping,
//                pwm_servo.cpp/.h + Servo.cpp/.h (copied from any of their Arduino examples)
//                for the servo outputs and your saved calibration offsets.
// See firmware/README.md for setup. Back up the stock firmware with esptool before flashing.
//
// Control over USB serial, 115200 baud. Human-friendly commands for tuning:
//   w <mm/s> <deg/s>       walk (e.g. "w 80 0", "w 0 40" turns left). Auto-stops after 10 s.
//   s                      stop (finishes the step and settles)
//   g <swing> <stand> <h>  gait timing ms/ms/mm (e.g. "g 160 200 18"); stand >= swing enforced
//   h <mm>                 body height offset (-25..15)
//   a <mm/s^2>             acceleration limit
//   home                   stand in the default pose
//   p                      print status
// Machine frames (same framing as dog/main.py, for dogbrain over a cable or bridge):
//   CMD|10|$ stop   CMD|11|<mm/s>|<deg/s>|$ walk (1 s watchdog)   CMD|14|$ home
//   CMD|15|$ status -> CMD|15|<dist_mm>|0|0|-1|<state>|$   CMD|17|<swing>|<stand>|<h>|$   CMD|18|$ ping
// Not in this firmware yet: ultrasonic obstacle stop, IMU, Bluetooth. It reports dist 5000.

#include "quad_kinematics.h"
#include "pwm_servo.h"
#include "gait.h"

static const uint32_t TICK_MS = (uint32_t)gait::TICK_MS;   // 20 ms, 50 Hz
static const uint32_t WATCHDOG_MS = 1000;                  // for CMD|11 frames
static const uint32_t MANUAL_WALK_MS = 10000;              // for typed "w" commands

quad_kinematics kin;
gait::Trot trot;

// Commands from the serial task to the control task
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
volatile float pend_vx = 0, pend_yaw = 0;
volatile bool pend_cmd = false, pend_params = false, pend_home = false;
gait::Params pend_p;
volatile uint32_t walk_deadline = 0;   // millis() after which walking stops
volatile uint32_t ik_errors = 0;

static mech_point_t home_points[4];

static void stand_home(int duration_ms) {
  for (int i = 0; i < 4; i++)
    home_points[i] = {gait::HOME[i].x, gait::HOME[i].y, gait::HOME[i].z - trot.p.body_z_mm};
  kin.set_default_pose(home_points, duration_ms);
}

// Send this tick's foot targets through Hiwonder's IK.
static void write_feet() {
  for (int i = 0; i < 4; i++) {
    mech_point_t pt = {trot.foot[i].x, trot.foot[i].y, trot.foot[i].z};
    float th[3] = {0, 0, 0};
    // leg_set_theta silently skips a servo whose angle is out of range, which shows up as a
    // twitch. Check first, and hold that leg's last pose instead.
    if (kin.leg_inverse_kinematics_offset(i, pt, th) != 0 || th[0] < 0 || th[0] > PI ||
        th[1] > 0 || th[1] < -PI) {
      ik_errors++;
      continue;
    }
    kin.leg_set_theta(i, th, TICK_MS);
  }
}

static void control_task(void*) {
  TickType_t last = xTaskGetTickCount();
  bool was_running = false;
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(TICK_MS));

    portENTER_CRITICAL(&mux);
    bool do_cmd = pend_cmd, do_params = pend_params, do_home = pend_home;
    float vx = pend_vx, yaw = pend_yaw;
    gait::Params np = pend_p;
    pend_cmd = pend_params = pend_home = false;
    uint32_t deadline = walk_deadline;
    portEXIT_CRITICAL(&mux);

    if (do_params) trot.set_params(np);
    if (do_cmd) trot.command(vx, yaw);
    if ((trot.cmd_vx != 0 || trot.cmd_yaw != 0) && (int32_t)(millis() - deadline) > 0)
      trot.command(0, 0);                                   // watchdog / auto-stop

    if (do_home && trot.running) pend_home = true;           // finish stopping first
    if (do_home && !trot.running) {
      trot.reset();
      stand_home(800);
      vTaskDelay(pdMS_TO_TICKS(850));
      last = xTaskGetTickCount();
      continue;
    }

    trot.step(TICK_MS);
    if (trot.running || was_running) write_feet();          // one last write after settling
    was_running = trot.running;
  }
}

// ---------------------------------------------------------------- commands
static void walk(float vx, float yaw_deg, uint32_t timeout_ms) {
  portENTER_CRITICAL(&mux);
  pend_vx = vx;
  pend_yaw = yaw_deg * PI / 180.0f;
  pend_cmd = true;
  walk_deadline = millis() + timeout_ms;
  portEXIT_CRITICAL(&mux);
}

static void set_params(const gait::Params& p) {
  portENTER_CRITICAL(&mux);
  pend_p = p;
  pend_params = true;
  portEXIT_CRITICAL(&mux);
}

static const char* state_name() {
  if (!trot.running) return "idle";
  return (trot.cmd_vx == 0 && trot.cmd_yaw == 0) ? "stopping" : "walking";
}

static void print_status() {
  Serial.printf("state=%s vx=%.0f yaw=%.2f swing=%.0f stand=%.0f height=%.0f body_z=%.0f "
                "accel=%.0f ik_errors=%u\n", state_name(), trot.vx, trot.yaw, trot.p.swing_ms,
                trot.p.stand_ms, trot.p.height_mm, trot.p.body_z_mm, trot.p.accel, ik_errors);
}

static void handle_frame(const String& body) {      // body = "11|80|0" (between CMD| and |$)
  float f[4] = {0, 0, 0, 0};
  int code = body.toInt(), n = 0, start = body.indexOf('|');
  while (start >= 0 && n < 4) {
    int next = body.indexOf('|', start + 1);
    f[n++] = body.substring(start + 1, next < 0 ? body.length() : next).toFloat();
    start = next;
  }
  switch (code) {
    case 10: walk(0, 0, 0); break;
    case 11: walk(f[0], f[1], WATCHDOG_MS); break;
    case 14: walk(0, 0, 0); pend_home = true; break;
    case 15: Serial.printf("CMD|15|5000|0|0|-1|%s|$", state_name()); break;
    case 17: {
      gait::Params p = trot.p;
      p.swing_ms = f[0]; p.stand_ms = f[1]; p.height_mm = f[2];
      set_params(p);
      break;
    }
    case 18: Serial.print("CMD|18|ok|$"); break;
    default: Serial.print("CMD|19|error|unknown_code|$"); break;
  }
}

static void handle_line(String line) {
  line.trim();
  if (line.length() == 0) return;
  float a = 0, b = 0, c = 0;
  int n = sscanf(line.c_str() + 1, "%f %f %f", &a, &b, &c);
  char k = line[0];
  gait::Params p = trot.p;
  if (line == "home") { walk(0, 0, 0); pend_home = true; Serial.println("ok home"); }
  else if (k == 'w' && n >= 1) { walk(a, n >= 2 ? b : 0, MANUAL_WALK_MS); Serial.println("ok walk"); }
  else if (k == 's') { walk(0, 0, 0); Serial.println("ok stop"); }
  else if (k == 'g' && n == 3) {
    p.swing_ms = a; p.stand_ms = b; p.height_mm = c; p.clamp();
    set_params(p);
    Serial.printf("ok gait swing=%.0f stand=%.0f height=%.0f\n", p.swing_ms, p.stand_ms, p.height_mm);
  }
  else if (k == 'h' && n == 1) { p.body_z_mm = a; p.clamp(); set_params(p); Serial.println("ok height"); }
  else if (k == 'a' && n == 1) { p.accel = a; set_params(p); Serial.println("ok accel"); }
  else if (k == 'p') print_status();
  else Serial.println("? commands: w <mm/s> <deg/s> | s | g <swing> <stand> <h> | h <mm> | a <mm/s2> | home | p");
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  pwm_servo_init();                    // attaches servos, loads your calibration offsets
  kin.begin(pwm_servo_set_position);   // their IK + 50 Hz pose timer (idle unless asked)
  delay(500);
  // Boot settings: tall stance, long steps (stride = speed x stand, so 62 mm at the 100 mm/s cap)
  trot.p.body_z_mm = 30;
  trot.p.swing_ms = 300;
  trot.p.stand_ms = 620;
  trot.p.max_speed = 100;
  trot.reset();
  stand_home(1500);
  delay(1600);
  xTaskCreatePinnedToCore(control_task, "gait", 6144, nullptr, 5, nullptr, 1);
  Serial.println("smooth_gait ready. Type 'p' for status, 'w 60 0' to walk, 's' to stop.");
}

void loop() {
  static String buf;
  while (Serial.available()) {
    char ch = (char)Serial.read();
    buf += ch;
    int s = buf.indexOf("CMD|");
    int e = (s >= 0) ? buf.indexOf('$', s) : -1;
    if (s >= 0 && e > s) {                       // machine frame
      String body = buf.substring(s + 4, e);
      if (body.endsWith("|")) body.remove(body.length() - 1);
      handle_frame(body);
      buf = buf.substring(e + 1);
    } else if (s < 0 && (ch == '\n' || ch == '\r')) {   // typed line
      handle_line(buf);
      buf = "";
    }
    if (buf.length() > 128) buf = "";
  }
  delay(2);
}
