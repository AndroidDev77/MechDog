// hardware.h - MechDog sensors and outputs, from Hiwonder's Arduino examples (Hiwonder.cpp,
// HW_MechDog.cpp), rewritten without their helper classes:
//   - ultrasonic module (I2C 0x77): distance in mm, plus the two "eye" RGB LEDs
//   - MPU6050 IMU (I2C 0x68): roll / pitch, same maths and signs as Hiwonder's balancing code
//   - battery: ADC pin 34 x 3.6 = millivolts (Hiwonder alarms below 7000)
//   - buzzer: pin 21, LEDC channel 11 (servos use channels 0-9)
// Everything on the I2C bus is touched ONLY by the sensor task (hw::start()), so other tasks
// read the cached values and post LED / beep requests.
#pragma once
#include <Arduino.h>
#include <Wire.h>

namespace hw {

static const int SDA_PIN = 22, SCL_PIN = 23;   // Hiwonder "IIC1"
static const int BATTERY_PIN = 34;
static const int BUZZER_PIN = 21, BUZZER_CH = 11;
static const uint8_t SONAR_ADDR = 0x77, IMU_ADDR = 0x68;

struct State {
  volatile int distance_mm = 5000;   // 5000 = nothing in range / no reading
  volatile float roll = 0, pitch = 0;   // degrees, Hiwonder's convention
  volatile int battery_mv = 0;
  volatile bool sonar_ok = false, imu_ok = false;
};
static State state;

// Requests from other tasks
static volatile uint32_t led_rgb = 0x0040FF;   // blue
static volatile bool led_dirty = true;
static volatile uint16_t beep_hz = 0, beep_ms = 0;
static volatile uint8_t beep_count = 0;

static inline void set_led(uint8_t r, uint8_t g, uint8_t b) {
  led_rgb = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
  led_dirty = true;
}
static inline void beep(uint16_t hz = 2000, uint16_t ms = 80, uint8_t count = 1) {
  beep_hz = hz;
  beep_ms = ms;
  beep_count = count;
}

// ---------------------------------------------------------------- I2C helpers
static bool i2c_write(uint8_t addr, uint8_t reg, const uint8_t* data, size_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  for (size_t i = 0; i < n; i++) Wire.write(data[i]);
  return Wire.endTransmission() == 0;
}
static bool i2c_read(uint8_t addr, uint8_t reg, uint8_t* out, size_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission() != 0) return false;     // stop, then read (as Hiwonder's helper)
  if (Wire.requestFrom((int)addr, (int)n) != (int)n) return false;
  for (size_t i = 0; i < n; i++) out[i] = Wire.read();
  return true;
}

// ---------------------------------------------------------------- ultrasonic + LEDs
static int sonar_hist[3] = {5000, 5000, 5000};
static void sonar_update() {
  uint8_t b[2];
  if (!i2c_read(SONAR_ADDR, 0, b, 2)) {     // register 0: distance, uint16 little-endian, mm
    state.sonar_ok = false;
    return;
  }
  int mm = b[0] | (b[1] << 8);
  if (mm == 65535 || mm == 0) mm = 5000;    // module reports 65535 when there's no echo
  sonar_hist[0] = sonar_hist[1];
  sonar_hist[1] = sonar_hist[2];
  sonar_hist[2] = mm;
  int a = sonar_hist[0], c = sonar_hist[1], d = sonar_hist[2];   // median of 3 rejects spikes
  state.distance_mm = max(min(a, c), min(max(a, c), d));
  state.sonar_ok = true;
}
static void led_apply() {
  uint8_t mode = 0;                          // register 2: 0 = fixed colour (1 = breathing)
  uint8_t rgb[3] = {(uint8_t)(led_rgb >> 16), (uint8_t)(led_rgb >> 8), (uint8_t)led_rgb};
  if (i2c_write(SONAR_ADDR, 2, &mode, 1) && i2c_write(SONAR_ADDR, 3, rgb, 3) &&
      i2c_write(SONAR_ADDR, 6, rgb, 3))      // registers 3-5: left eye, 6-8: right eye
    led_dirty = false;
}

// ---------------------------------------------------------------- IMU (MPU6050)
static float ax_off = 0, ay_off = 0, az_off = 0, axf = 0, ayf = 0, azf = 8192;
static bool imu_read_raw(int16_t* a) {
  uint8_t b[6];
  if (!i2c_read(IMU_ADDR, 0x3B, b, 6)) return false;   // ACCEL_XOUT_H..ACCEL_ZOUT_L
  for (int i = 0; i < 3; i++) a[i] = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]);
  return true;
}
static void imu_init() {
  uint8_t wake = 0, accel_4g = 0x08, gyro_2000 = 0x18;
  if (!i2c_write(IMU_ADDR, 0x6B, &wake, 1)) return;     // PWR_MGMT_1: wake up
  i2c_write(IMU_ADDR, 0x1C, &accel_4g, 1);              // +-4 g: 8192 LSB per g (as Hiwonder)
  i2c_write(IMU_ADDR, 0x1B, &gyro_2000, 1);
  delay(200);
  // Calibrate assuming the dog boots standing level, like Hiwonder's homeostasis code.
  long sx = 0, sy = 0, sz = 0;
  int n = 0;
  for (int k = 0; k < 20; k++) {
    int16_t a[3];
    if (imu_read_raw(a)) { sx += a[0]; sy += a[1]; sz += a[2]; n++; }
    delay(5);
  }
  if (n == 0) return;
  ax_off = (float)sx / n;
  ay_off = (float)sy / n;
  az_off = (float)sz / n - 8192.0f;
  axf = ax_off; ayf = ay_off; azf = az_off + 8192.0f;
  state.imu_ok = true;
}
static void imu_update() {
  int16_t a[3];
  if (!imu_read_raw(a)) { state.imu_ok = false; return; }
  axf = a[0] * 0.3f + axf * 0.7f;            // same low-pass as Hiwonder's read_angle()
  ayf = a[1] * 0.3f + ayf * 0.7f;
  azf = a[2] * 0.3f + azf * 0.7f;
  float ax = (axf - ax_off) / 8192.0f, ay = (ayf - ay_off) / 8192.0f, az = (azf - az_off) / 8192.0f;
  state.roll = atan2f(ay, az) * 57.29578f;
  state.pitch = -atan2f(ax, az) * 57.29578f;
  state.imu_ok = true;
}

// ---------------------------------------------------------------- battery + buzzer
static int batt_samples[8], batt_n = 0;
static void battery_update() {
  batt_samples[batt_n++ % 8] = (int)(analogRead(BATTERY_PIN) * 3.6f);
  int k = batt_n < 8 ? batt_n : 8;
  long sum = 0;
  for (int i = 0; i < k; i++) sum += batt_samples[i];
  state.battery_mv = (int)(sum / k);
}

static void buzzer_init() {
  ledcSetup(BUZZER_CH, 2000, 10);            // ESP32 Arduino core 2.0.x API (as Hiwonder's)
  ledcAttachPin(BUZZER_PIN, BUZZER_CH);
  ledcWrite(BUZZER_CH, 0);
}

// ---------------------------------------------------------------- the sensor task
static void task(void*) {
  uint32_t tick = 0, beep_off_at = 0, next_beep_at = 0;
  bool beeping = false;
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(10));            // 100 Hz
    tick++;
    if (tick % 2 == 0) imu_update();                       // 50 Hz
    if (tick % 5 == 0) sonar_update();                     // 20 Hz
    if (tick % 25 == 0) battery_update();                  // 4 Hz, averaged over 2 s
    if (led_dirty && tick % 5 == 2) led_apply();
    if (tick % 200 == 0 && !state.imu_ok) imu_init();      // retry if it dropped out

    uint32_t now = millis();
    if (beeping && (int32_t)(now - beep_off_at) >= 0) {
      ledcWrite(BUZZER_CH, 0);
      beeping = false;
      next_beep_at = now + beep_ms;                         // gap = tone length
    }
    if (!beeping && beep_count > 0 && (int32_t)(now - next_beep_at) >= 0) {
      ledcWriteTone(BUZZER_CH, beep_hz);
      beeping = true;
      beep_off_at = now + beep_ms;
      beep_count--;
    }
  }
}

static void start() {
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  buzzer_init();
  analogReadResolution(12);
  imu_init();
  sonar_update();
  for (int i = 0; i < 8; i++) battery_update();
  xTaskCreatePinnedToCore(task, "sensors", 4096, nullptr, 3, nullptr, 0);
}

}  // namespace hw
