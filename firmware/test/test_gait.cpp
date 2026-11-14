// PC unit test for firmware/smooth_gait/gait.h
// Build & run:  g++ -std=c++17 -O1 -I../smooth_gait test_gait.cpp -o test_gait && ./test_gait
#include <cassert>
#include <cmath>
#include <cstdio>
#include <initializer_list>
#include <utility>
#include <algorithm>
#include "gait.h"

using namespace gait;

// Hiwonder's leg_inverse_kinematics, reconstructed from the disassembly.
static const float L1 = 60.5f, L2 = 65.0f, HIP_X = 60.25f;
static bool ik_ok(int leg, const Vec3& p, float* th1, float* th2) {
  float x = p.x - ((leg == 0 || leg == 2) ? HIP_X : -HIP_X), z = p.z;
  float r2 = x * x + z * z, r = sqrtf(r2);
  if (r > L1 + L2) return false;
  float t2 = acosf((r2 - L1 * L1 - L2 * L2) / (2 * L1 * L2));
  if (t2 > 0) t2 = -t2;
  float t1 = atan2f(-z, x) - atan2f(L2 * sinf(t2), L1 + L2 * cosf(t2));
  *th1 = t1; *th2 = t2;
  // leg_set_theta silently skips servos outside these ranges
  return t1 >= 0 && t1 <= (float)M_PI && t2 <= 0 && t2 >= -(float)M_PI;
}


// Hiwonder's stock gait for one leg, as disassembled (swing_process / stand_process, 20 ms ticks).
struct Stock {
  float lift_vz = 0, land_vz = 0, transition_dvx = 0;
  Stock(float sw = 150, float st = 200, float h = 25, float v = 120, float dt = 20) {
    float T = sw + st, m = (T / 2) * v / 1000;          // update_gait_base_data
    float aep = m, pep = -m;                             // relative to home, straight walking
    auto pos = [&](float ph, float& x, float& z) {
      if (ph < sw) {
        float p = ph / sw;
        x = p < 0.6f ? pep + (aep - pep) * (p / 0.6f) : aep;
        z = p < 0.4f ? h * p / 0.4f : (p < 0.6f ? h : h * (1 - (p - 0.6f) / 0.4f));
      } else {
        x = aep + (pep - aep) * ((ph - sw) / st);
        z = 0;
      }
    };
    // Vertical speed is constant on the up and down legs of the trapezoid: h / (0.4 * swing).
    lift_vz = land_vz = h / (0.4f * sw / 1000);
    int n = (int)(T / dt) * 2;
    float px[64], pz[64];
    for (int k = 0; k < n; k++) pos(fmodf(k * dt, T), px[k], pz[k]);
    for (int k = 2; k < n; k++) {
      float ph = fmodf(k * dt, T), phprev = fmodf((k - 1) * dt, T);
      bool lift = ph < sw && phprev >= sw, land = ph >= sw && phprev < sw;
      float dx = px[k] - px[k - 1], dz = pz[k] - pz[k - 1];
      if (lift || land) transition_dvx = fmaxf(transition_dvx, fabsf(dx - (px[k - 1] - px[k - 2])) / (dt / 1000));
      (void)dz;
    }
  }
};

struct Stats {
  float max_dx = 0, max_dz = 0, max_land_vz = 0, max_lift_vz = 0, max_transition_dvx = 0;
  int min_feet = 4, ik_fail = 0, steps = 0;
};

static void run(Trot& g, float vx, float yaw, float seconds, Stats& s, float dt = 20) {
  g.command(vx, yaw);
  Vec3 prev[4], prev2[4];
  float last_air_dz[4] = {0, 0, 0, 0};
  for (int i = 0; i < 4; i++) prev[i] = prev2[i] = g.foot[i];
  bool have2 = false;
  int n = (int)(seconds * 1000 / dt);
  for (int k = 0; k < n; k++) {
    bool was_air[4];
    for (int i = 0; i < 4; i++) was_air[i] = g.in_air(i);
    g.step(dt);
    s.steps++;
    if (g.running) s.min_feet = std::min(s.min_feet, g.feet_down());
    for (int i = 0; i < 4; i++) {
      const Vec3& f = g.foot[i];
      float th1, th2;
      if (!ik_ok(i, f, &th1, &th2)) s.ik_fail++;
      float dx = f.x - prev[i].x, dz = f.z - prev[i].z;
      s.max_dx = fmaxf(s.max_dx, fabsf(dx));
      s.max_dz = fmaxf(s.max_dz, fabsf(dz));
      bool lift = !was_air[i] && g.in_air(i), land = was_air[i] && !g.in_air(i);
      if (have2 && (lift || land)) {   // speed jump right at lift-off / touchdown
        float dvx = dx - (prev[i].x - prev2[i].x);
        s.max_transition_dvx = fmaxf(s.max_transition_dvx, fabsf(dvx) / (dt / 1000));
      }
      // touchdown speed: the larger of the final swing tick and the landing tick
      if (land) s.max_land_vz = fmaxf(s.max_land_vz, fmaxf(fabsf(dz), fabsf(last_air_dz[i])) / (dt / 1000));
      if (g.in_air(i)) last_air_dz[i] = dz;
      if (lift) s.max_lift_vz = fmaxf(s.max_lift_vz, fabsf(dz) / (dt / 1000));
      prev2[i] = prev[i];
      prev[i] = f;
    }
    have2 = true;
  }
}

int main() {
  // --- walking straight at 120 mm/s, then stopping ---
  {
    Trot g;
    Stats s;
    run(g, 120, 0, 4.0f, s);
    Stock st;
    printf("walk 120   smooth: lift-off vz %3.0f  touchdown vz %3.0f  x-speed jump at transitions %3.0f mm/s"
           "  (min feet %d, ik fails %d)\n", s.max_lift_vz, s.max_land_vz, s.max_transition_dvx,
           s.min_feet, s.ik_fail);
    printf("           stock : lift-off vz %3.0f  touchdown vz %3.0f  x-speed jump at transitions %3.0f mm/s\n",
           st.lift_vz, st.land_vz, st.transition_dvx);
    fflush(stdout);
    assert(s.ik_fail == 0);
    assert(s.min_feet >= 2);                            // a diagonal pair always supports the body
    assert(s.max_land_vz < 0.25f * st.land_vz);         // soft landings
    assert(s.max_lift_vz < 0.25f * st.lift_vz);         // soft lift-off
    assert(s.max_transition_dvx < 0.35f * st.transition_dvx);  // no lurch at lift-off/touchdown
    Stats s2;
    run(g, 0, 0, 3.0f, s2);
    assert(!g.running);                    // settles and stops stepping
    for (int i = 0; i < 4; i++) assert(fabsf(g.foot[i].x - HOME[i].x) < 1.5f);
    printf("stop: settled at home, running=%d\n", g.running);
  }
  // --- same settings as stock (150/200/25): apples-to-apples ---
  {
    Trot g;
    Params p; p.swing_ms = 150; p.stand_ms = 200; p.height_mm = 25;
    g.set_params(p);
    Stats s;
    run(g, 120, 0, 4.0f, s);
    Stock st(150, 200, 25, 120);
    printf("same params smooth: lift-off vz %3.0f  touchdown vz %3.0f  x-speed jump %3.0f mm/s"
           "  vs stock %3.0f / %3.0f / %3.0f\n", s.max_lift_vz, s.max_land_vz, s.max_transition_dvx,
           st.lift_vz, st.land_vz, st.transition_dvx);
    fflush(stdout);
    assert(s.ik_fail == 0 && s.min_feet >= 2);
    assert(s.max_land_vz < 0.4f * st.land_vz && s.max_transition_dvx < 0.5f * st.transition_dvx);
  }
  // --- stance feet move at exactly the commanded speed (stock: 1.75x) ---
  {
    Trot g;
    g.command(100, 0);
    for (int k = 0; k < 100; k++) g.step(20);       // reach speed
    for (int k = 0; k < 200; k++) {
      float x0 = g.foot[1].x; bool air0 = g.in_air(1);
      g.step(20);
      if (!air0 && !g.in_air(1)) {
        float v = (x0 - g.foot[1].x) / 0.020f;
        assert(fabsf(v - 100) < 0.5f);
      }
    }
    printf("stance speed matches command\n");
  }
  // --- turning in place and walking backwards stay reachable and supported ---
  for (auto cmd : {std::pair<float, float>{0, 1.0f}, {0, -1.0f}, {-80, 0}, {150, 0.8f}}) {
    Trot g;
    Stats s;
    run(g, cmd.first, cmd.second, 3.0f, s);
    printf("cmd vx %4.0f yaw %4.1f: ik fails %d, min feet %d, touchdown vz %.0f mm/s\n", cmd.first,
           cmd.second, s.ik_fail, s.min_feet, s.max_land_vz);
    assert(s.ik_fail == 0 && s.min_feet >= 2);
  }
  // --- tallest body height stays reachable at speed, turning and backwards ---
  {
    Params p;
    p.body_z_mm = 40;
    p.clamp();
    assert(p.body_z_mm == 30);
    for (auto cmd : {std::pair<float, float>{100, 0}, {150, 0}, {150, 0.8f}, {-90, 0}, {0, 1.2f}}) {
      Trot g;
      g.set_params(p);
      Stats s;
      run(g, cmd.first, cmd.second, 3.0f, s);
      printf("tall (body_z %.0f) vx %4.0f yaw %4.1f: ik fails %d, min feet %d\n", p.body_z_mm,
             cmd.first, cmd.second, s.ik_fail, s.min_feet);
      assert(s.ik_fail == 0 && s.min_feet >= 2);
    }
  }
  // --- body tilt: every height x the largest tilts, standing and walking, stays reachable ---
  {
    int combos = 0;
    for (float bz : {-25.0f, -10.0f, 0.0f, 15.0f, 30.0f})
      for (float h : {12.0f, 18.0f, 30.0f})
        for (float r : {-15.0f, 0.0f, 15.0f})
          for (float pt : {-15.0f, 0.0f, 15.0f})
            for (auto cmd : {std::pair<float, float>{0, 0}, {100, 0}, {0, 1.0f}}) {
              Params p;
              p.body_z_mm = bz; p.height_mm = h; p.roll_deg = r; p.pitch_deg = pt;
              p.clamp();
              Trot g;
              g.set_params(p);
              g.reset();                       // start already in the pose
              Stats s;
              run(g, cmd.first, cmd.second, 2.0f, s);
              if (s.ik_fail) {
                printf("tilt FAIL body_z %.0f height %.0f roll %.1f pitch %.1f vx %.0f yaw %.1f: %d\n",
                       p.body_z_mm, p.height_mm, p.roll_deg, p.pitch_deg, cmd.first, cmd.second, s.ik_fail);
                fflush(stdout);
              }
              assert(s.ik_fail == 0 && s.min_feet >= 2);
              combos++;
            }
    Params p; p.body_z_mm = 15; p.height_mm = 18; p.pitch_deg = 15; p.clamp();
    printf("tilt: %d height/tilt/walk combos reachable (e.g. body +15 allows pitch %.1f deg)\n",
           combos, p.pitch_deg);
  }
  // --- pose changes ease in: no foot jumps, standing or walking ---
  {
    for (float vx : {0.0f, 80.0f}) {
      Trot g;
      g.command(vx, 0);
      for (int k = 0; k < 50; k++) g.step(20);
      Params p = g.p; p.body_z_mm = 25; p.pitch_deg = 10; p.roll_deg = -8;
      g.set_params(p);
      float worst = 0;
      Vec3 before[4];
      for (int k = 0; k < 100; k++) {
        for (int i = 0; i < 4; i++) before[i] = g.foot[i];
        bool air[4]; for (int i = 0; i < 4; i++) air[i] = g.in_air(i);
        g.step(20);
        for (int i = 0; i < 4; i++)
          if (!air[i] && !g.in_air(i)) worst = fmaxf(worst, fabsf(g.foot[i].z - before[i].z));
      }
      fflush(stdout); printf("pose change while %s: worst stance-foot z step %.2f mm/tick, posing done=%d\n",
             vx == 0 ? "standing" : "walking", worst, !g.posing());
      fflush(stdout);
      assert(worst < 3.5f && !g.posing());   // = height + pitch + roll easing at their set rates
      assert(fabsf(g.body_z() - g.p.body_z_mm) < 1e-3f);
    }
  }
  // --- params are clamped so both pairs are never airborne together ---
  {
    Params p;
    p.swing_ms = 150; p.stand_ms = 40; p.height_mm = 80;
    p.clamp();
    assert(p.stand_ms >= p.swing_ms && p.height_mm <= 35);
    Trot g;
    g.set_params(p);
    Stats s;
    run(g, 120, 0, 3.0f, s);
    assert(s.min_feet >= 2);
    printf("unsafe params clamped (stand %.0f, height %.0f)\n", p.stand_ms, p.height_mm);
  }
  // --- changing params mid-walk causes no jump ---
  {
    Trot g;
    g.command(100, 0);
    for (int k = 0; k < 60; k++) g.step(20);
    Vec3 before[4];
    for (int i = 0; i < 4; i++) before[i] = g.foot[i];
    Params p = g.p; p.swing_ms = 200; p.stand_ms = 240;
    g.set_params(p);
    g.step(20);
    float worst = 0;
    for (int i = 0; i < 4; i++)
      worst = fmaxf(worst, hypotf(g.foot[i].x - before[i].x, g.foot[i].z - before[i].z));
    printf("param change mid-walk: worst foot jump %.1f mm\n", worst);
    assert(worst < 12);
  }
  printf("ALL GAIT TESTS PASSED\n");
  return 0;
}
