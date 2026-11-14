# smooth_gait firmware

Full replacement firmware for the MechDog body. It keeps Hiwonder's inverse kinematics, servo mapping, calibration offsets and tricks, and replaces their walking gait with a smooth one. It also adds the ultrasonic obstacle stop, IMU tilt, battery protection, eye LEDs, buzzer and a Bluetooth link that dogbrain talks to directly.

## What it does

| | |
| --- | --- |
| **Walking** | Smooth trot: soft lift-off and touchdown, no lurch, exact speed, same top speed forwards and backwards. Speed and turn ramp gradually; stopping finishes the step and settles all four feet |
| **Boot gait** | Tall stance (body +30 mm), quick 160 ms swing and 640 ms stance: 64 mm steps at the 100 mm/s top speed, with only a short time on two feet |
| **Speed cap** | The top speed applies to the fastest foot, not just the body. Turning while walking speeds up the outer legs, so forward speed eases off to keep the turn rate you asked for. Every walk + turn combination stays within the legs' reach |
| **Body pose** | Height, roll and pitch ease to their targets while standing or walking, limited so no leg overreaches |
| **Tricks** | All 14 of Hiwonder's (sit, handshake, bow, push-ups...) plus two smooth body wiggles (`rotation_roll`, `rotation_pitch`), then eases back to standing. `stop` cancels one mid-way |
| **Obstacle stop** | Won't walk forward if something is closer than 15 cm (eyes go red, two beeps). Turning and backing away still work |
| **Battery** | Below 6.8 V for 5 s: stops, won't walk or do tricks, eyes orange, three beeps. Unlocks above 7.1 V |
| **Watchdog** | Walking started by a `CMD\|11` frame stops if no frame of any kind arrives for 1 s (dogbrain's status polls keep it alive), or if Bluetooth disconnects |
| **Bluetooth** | Advertises as `MechDog_XX`, the same name as stock, using the Nordic UART service. `python -m dogbrain.link --ble` finds it automatically |
| **Status** | Distance, IMU roll/pitch, battery mV and state, in the same frame format as `dog/main.py` |

### Why the gait is different

Disassembling Hiwonder's `MechDog_Arduino.a` showed their swing is built from straight lines: up for 40% of the swing, flat for 20%, down for 40%, with the forward motion stopping dead at 60%. Feet land at about 420 mm/s and change speed abruptly at lift-off.

| Default settings (150 ms / 200 ms / 25 mm, 120 mm/s) | Stock | smooth_gait |
| --- | --- | --- |
| Vertical speed at lift-off / touchdown | 417 mm/s | 105 mm/s |
| Forward speed jump at lift-off / touchdown | 338 mm/s | 98 mm/s |
| Actual walking speed vs commanded | ~1.75x | 1.0x |

Figures are from `test/test_gait.cpp`, which models the disassembled stock code.

## Setup

1. **Back up the stock firmware first:** `esptool --port <port> --baud 921600 read-flash 0 ALL mechdog_full.bin`. Restore with `write-flash 0 mechdog_full.bin`.
2. **ESP32 board package 2.0.x** (Hiwonder's library and servo driver need it), board "ESP32 Dev Module", **Partition Scheme: Huge APP (3MB No OTA)**. Bluetooth makes the firmware too big for the default partition.
   With arduino-cli: `--fqbn esp32:esp32:esp32:PartitionScheme=huge_app`.
3. **Hiwonder's library:** install `MechDog_Arduino.zip` (Sketch → Include Library → Add .ZIP Library).
4. **Copy these five files** from any Hiwonder Arduino example, e.g. `03 Arduino编程项目程序/03 二次开发玩法课程/1.3 动作组运行程序/ActionRun/` in [Hiwonder/MechDog](https://github.com/Hiwonder/MechDog), into `firmware/smooth_gait/`:
   `pwm_servo.cpp`, `pwm_servo.h`, `Servo.cpp`, `Servo.h`, `action.h`.
   They are git-ignored; don't commit them. Without `action.h` it still builds; only the two built-in wiggles (`rotation_roll`, `rotation_pitch`) are available.
5. Upload and open the Serial Monitor at 115200. It beeps twice and prints `MechDog ready as MechDog_XX`.

## Commands

Typed on the Serial Monitor:

```
p                 status: gait, pose, distance, IMU, battery, Bluetooth, IK errors
w 60 0            walk forward 60 mm/s (auto-stops after 10 s); w 0 30 turns left
s                 stop (also cancels a trick)
g 160 640 18      swing ms, stand ms, step height mm (boot values; step length = speed x stand)
h 20              body height (mm above Hiwonder's default; boots at 30)
t 0 8             body tilt: roll, pitch in degrees (pitch + = nose up, roll + = left side down)
a 300             acceleration limit, mm/s^2
act handshake     trick (sit_dowm, go_prone, stand_four_legs, stand_two_legs, handshake,
                  scrape_a_bow, nodding_motion, boxing, stretch_oneself, pee, press_up,
                  left_foot_kick, right_foot_kick, two_legs_back)
led 0 255 0       eye colour
home              default pose
```

Machine frames (Bluetooth or serial) match `dog/main.py`. See the header of `smooth_gait.ino`.

## With dogbrain

`python -m dogbrain.link --ble` and `python -m dogbrain` work unchanged. Speeds are exact with this firmware, so put these in `config.toml`:

```toml
[dog]
walk_speed_cm_s = 6.0
turn_rate_deg_s = 30.0
```

## Limits

- **Testing so far:** gait maths unit-tested (`test/`). The full firmware passes a syntax check against stub headers (with and without `action.h`). Only the gait has run on the dog; the sensors, Bluetooth, tricks and safety features are new and untested on hardware.
- **Servo speed:** at the boot gait's 100 mm/s, the knee peaks around 835 deg/s during a swing (about 1070 deg/s on the first swings while speeding up). Small hobby servos may not keep up with that, so the swing could trail slightly; if feet scuff, slow down or lengthen the swing (`g 200 640 18`).
- **IMU:** tilt is measured and reported, but not used to self-balance yet. It calibrates level at boot, so power it on standing on a flat surface.
- **Hiwonder phone app:** the app looks for Hiwonder's own Bluetooth service, so it won't connect to this firmware.
- **Missing ultrasonic module:** if the module isn't found, the obstacle stop is off. `p` says so.

## Test

```
cd firmware && g++ -std=c++17 -Ismooth_gait test/test_gait.cpp -o test/test_gait && ./test/test_gait
```
