// gait.h - smooth trot gait for Hiwonder MechDog (no Arduino dependencies; unit-tested on a PC)
//
// Replaces Hiwonder's piecewise-linear swing (straight up, flat, straight down, with the
// forward motion stopping dead at 60%) with:
//   - swing x: cubic Hermite curve whose start/end velocity matches the stance foot, so the
//     foot leaves and meets the ground with no speed jump (no scuff, no lurch)
//   - swing z: minimum-jerk bump h*64*s^3*(1-s)^3: zero vertical speed AND acceleration at
//     lift-off and touchdown (soft landings)
//   - stance: feet are integrated against the commanded body velocity, so the dog actually
//     walks at the speed you ask for (stock walks ~1.75x faster than commanded)
//   - commands are ramped with acceleration limits; stopping finishes the steps and settles
//     every foot back on its home position
//
// Conventions match Hiwonder's quad_kinematics: body frame, x forward, y left, z up, mm.
// Legs: 0 = front-left, 1 = rear-left, 2 = front-right, 3 = rear-right (from their
// leg_inverse_kinematics_offset). Trot pairs: {0,3} and {1,2}, half a cycle apart.
// Their legs are 2-DOF planar (hip + knee pitch), so only x and z matter to the IK.
#pragma once
#include <math.h>

namespace gait {

struct Vec3 { float x, y, z; };

// Hiwonder default_pose (HW_MechDog.cpp)
static const Vec3 HOME[4] = {
    {59.25f, 46.0f, -80.0f}, {-71.25f, 46.0f, -80.0f},
    {59.25f, -46.0f, -80.0f}, {-71.25f, -46.0f, -80.0f}};

static const float TICK_MS = 20.0f;   // the firmware's control period (50 Hz)

struct Params {
  float swing_ms = 160.0f;    // foot in the air
  float stand_ms = 200.0f;    // foot on the ground; must be >= swing_ms (see clamp())
  float height_mm = 18.0f;    // step height
  float body_z_mm = 0.0f;     // body height offset (+ = taller)
  float max_speed = 150.0f;   // mm/s
  float max_yaw = 1.2f;       // rad/s
  float accel = 400.0f;       // mm/s^2
  float yaw_accel = 4.0f;     // rad/s^2

  void clamp() {
    if (swing_ms < 80) swing_ms = 80;
    if (swing_ms > 600) swing_ms = 600;
    // With pairs half a cycle apart, stand < swing means both pairs are airborne at once.
    if (stand_ms < swing_ms) stand_ms = swing_ms;
    if (stand_ms > 1200) stand_ms = 1200;
    // Keep every swing start on the tick grid: swing and stand are whole ticks, and the
    // half-cycle offset between the pairs is too (so the cycle is an even number of ticks).
    swing_ms = roundf(swing_ms / TICK_MS) * TICK_MS;
    stand_ms = roundf(stand_ms / TICK_MS) * TICK_MS;
    if (stand_ms < swing_ms) stand_ms = swing_ms;
    if (fmodf(swing_ms + stand_ms, 2 * TICK_MS) != 0) stand_ms += TICK_MS;
    if (height_mm < 5) height_mm = 5;
    if (height_mm > 35) height_mm = 35;
    if (body_z_mm < -25) body_z_mm = -25;
    if (body_z_mm > 30) body_z_mm = 30;   // feet 110 mm down; knee still clearly bent
  }
  float period_ms() const { return swing_ms + stand_ms; }
};

// Smoothstep-style helpers
static inline float bump(float s) {            // 0 -> 1 -> 0, zero vel + accel at ends
  float a = s * (1 - s);
  return 64.0f * a * a * a;
}
static inline float hermite(float p0, float m0, float p1, float m1, float s) {
  float s2 = s * s, s3 = s2 * s;
  return (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * m0 +
         (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * m1;
}
static inline float approach(float cur, float target, float step) {
  if (cur < target) return (cur + step > target) ? target : cur + step;
  return (cur - step < target) ? target : cur - step;
}

class Trot {
 public:
  Params p;
  float vx = 0, yaw = 0;              // current (ramped) body velocity: mm/s, rad/s
  float cmd_vx = 0, cmd_yaw = 0;      // commanded
  bool running = false;
  Vec3 foot[4];                       // output: foot targets for the IK

  Trot() { reset(); }

  void reset() {
    p.clamp();
    for (int i = 0; i < 4; i++) {
      foot[i] = HOME[i];
      foot[i].z -= p.body_z_mm;
      ground_x_[i] = HOME[i].x;
      in_air_[i] = false;
    }
    t_ms_ = 0;
    vx = yaw = 0;
    running = false;
  }

  void command(float vx_mm_s, float yaw_rad_s) {
    cmd_vx = fmaxf(-p.max_speed * 0.6f, fminf(p.max_speed, vx_mm_s));   // slower backwards
    cmd_yaw = fmaxf(-p.max_yaw, fminf(p.max_yaw, yaw_rad_s));
    if (cmd_vx != 0 || cmd_yaw != 0) running = true;
  }

  void set_params(const Params& np) {
    // Only take effect between steps would be ideal; timing changes here are smooth because
    // phase is kept as a fraction of the cycle, not reset.
    float frac = (p.period_ms() > 0) ? t_ms_ / p.period_ms() : 0;
    p = np;
    p.clamp();
    t_ms_ = frac * p.period_ms();
  }

  // Number of feet currently on the ground (for tests/telemetry)
  int feet_down() const {
    int n = 0;
    for (int i = 0; i < 4; i++) n += in_air_[i] ? 0 : 1;
    return n;
  }
  bool in_air(int i) const { return in_air_[i]; }

  // Advance by dt_ms; updates foot[].
  void step(float dt_ms) {
    const float dt = dt_ms * 0.001f;
    vx = approach(vx, cmd_vx, p.accel * dt);
    yaw = approach(yaw, cmd_yaw, p.yaw_accel * dt);

    const float T = p.period_ms();
    const float sw = p.swing_ms, st = p.stand_ms;
    const float zbase = -p.body_z_mm;

    if (!running) {
      for (int i = 0; i < 4; i++) {
        foot[i].x = ground_x_[i];
        foot[i].y = HOME[i].y;
        foot[i].z = HOME[i].z + zbase;
      }
      return;
    }

    bool settled = (cmd_vx == 0 && cmd_yaw == 0 && vx == 0 && yaw == 0);

    for (int i = 0; i < 4; i++) {
      // Ground speed of this foot relative to the body (x only: legs are planar).
      // Body turning at yaw makes a foot at lateral offset y move at -yaw*y in x.
      const float v_i = vx - yaw * HOME[i].y;
      float ph = t_ms_ + ((i == 1 || i == 2) ? 0.5f * T : 0.0f);
      if (ph >= T) ph -= T;

      if (ph < sw) {
        // ---- swing ----
        if (!in_air_[i]) {                       // lift-off: remember where we left the ground
          in_air_[i] = true;
          liftoff_x_[i] = ground_x_[i];
          liftoff_v_[i] = -v_i;
        }
        // Position at the END of this tick, so the last swing tick lands exactly on the
        // touchdown point (s = 1) and the first one has moved one tick's worth off the ground.
        const float s = fminf(1.0f, (ph + dt_ms) / sw);
        // Touchdown so the foot is centred on HOME halfway through the next stance.
        const float td = HOME[i].x + v_i * (st * 0.001f) * 0.5f;
        const float Tsw = sw * 0.001f;
        // Hermite tangents are in mm per unit s: match stance velocity at both ends.
        foot[i].x = hermite(liftoff_x_[i], liftoff_v_[i] * Tsw, td, -v_i * Tsw, s);
        foot[i].z = HOME[i].z + zbase + p.height_mm * bump(s);
        touchdown_x_[i] = td;
      } else {
        // ---- stance ----
        if (in_air_[i]) {                         // touchdown
          in_air_[i] = false;
          ground_x_[i] = touchdown_x_[i];
        }
        ground_x_[i] -= v_i * dt;                 // foot stays put on the floor; body moves
        foot[i].x = ground_x_[i];
        foot[i].z = HOME[i].z + zbase;
      }
      foot[i].y = HOME[i].y;
      if (settled && fabsf(ground_x_[i] - HOME[i].x) > 1.0f) settled = false;
    }

    // Advance the cycle after computing, so a fresh start begins exactly at phase 0.
    t_ms_ += dt_ms;
    if (t_ms_ >= T) t_ms_ -= T;

    // Stop stepping once we're commanded still and every foot is home and on the ground.
    if (settled && feet_down() == 4) {
      running = false;
      t_ms_ = 0;
    }
  }

 private:
  float t_ms_ = 0;
  float ground_x_[4];
  float liftoff_x_[4] = {0, 0, 0, 0}, liftoff_v_[4] = {0, 0, 0, 0}, touchdown_x_[4] = {0, 0, 0, 0};
  bool in_air_[4];
};

}  // namespace gait
