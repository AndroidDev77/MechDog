# smooth_gait firmware

A replacement walking gait for the MechDog body. It keeps Hiwonder's inverse kinematics, servo mapping and your calibration offsets, and replaces their foot trajectories.

## Why

Disassembling Hiwonder's `MechDog_Arduino.a` showed that their swing is built from straight lines. The foot goes straight up for 40% of the swing, stays flat for 20%, then comes straight down, and the forward motion stops dead at 60%. With the defaults (150 ms swing, 25 mm lift), each foot lands at about 420 mm/s and changes speed abruptly at lift-off. That's the jerk you see.

| At default settings (150 ms / 200 ms / 25 mm, 120 mm/s) | Stock | smooth_gait |
| --- | --- | --- |
| Vertical speed at lift-off / touchdown | 417 mm/s | 105 mm/s |
| Forward speed jump at lift-off / touchdown | 338 mm/s | 98 mm/s |
| Actual walking speed vs commanded | ~1.75x | 1.0x |

Figures are from `test/test_gait.cpp`, which compares against a model of the disassembled stock code. With smooth_gait's own defaults (160 / 200 / 18 mm), touchdown is 75 mm/s.

How it does it:
- **Swing height:** a minimum-jerk bump, so vertical speed and acceleration are zero at lift-off and touchdown.
- **Swing forward motion:** a curve that starts and ends at the stance foot's speed, so there's no lurch.
- **Stance:** feet move at exactly the commanded body speed.
- **Ramps:** speed and turn changes are acceleration-limited. Stopping finishes the current step and settles all feet home.
- **Safety limits:** stand time is never shorter than swing time, otherwise both diagonal pairs would be airborne at once. Timing snaps to the 20 ms control tick.
- **Out-of-range angles:** IK results outside the servo range are caught. That leg holds its pose for the tick, where the stock code silently skips the servo.

## Setup

1. **Back up the stock firmware first** (dog plugged in over USB-C):
   ```
   esptool --port /dev/tty.usbserial-XXXX --baud 921600 read-flash 0 ALL mechdog_full.bin
   ```
   Restore any time with `esptool --port ... write-flash 0 mechdog_full.bin`.
2. **Arduino IDE:** install the ESP32 board package at the version Hiwonder's MechDog Arduino guide specifies (their library was built with the 2.0.x toolchain, GCC 8.4). Select their recommended board (ESP32 Dev Module).
3. **Libraries:** Sketch → Include Library → Add .ZIP Library → `MechDog_Arduino.zip` (and `MPU6050.zip`), from Hiwonder's download folder.
4. **Copy Hiwonder's servo driver** into `firmware/smooth_gait/`: `pwm_servo.cpp`, `pwm_servo.h`, `Servo.cpp`, `Servo.h`. They're in any of their Arduino examples, e.g. `03 Arduino编程项目程序/03 二次开发玩法课程/1.3 动作组运行程序/ActionRun/` in [Hiwonder/MechDog](https://github.com/Hiwonder/MechDog). They're git-ignored here; don't commit them.
5. Open `smooth_gait.ino`, upload, open the Serial Monitor at 115200 baud.

## Tuning

```
p                 status
w 60 0            walk forward 60 mm/s (auto-stops after 10 s)
w 0 40            turn left 40 deg/s
s                 stop
g 160 200 18      swing ms, stand ms, step height mm
h -10             body 10 mm lower
a 300             gentler acceleration
home              stand in the default pose
```

Start slow (`w 40 0`), then raise speed. Lower step height and a longer swing are softer. Shorter swings are faster but harder on the servos. If `p` shows `ik_errors` climbing, the feet are being asked to go out of reach: lower the speed or body height change.

## Limits (for now)

- **No obstacle stop, IMU or Bluetooth yet:** use it for tuning on a cable. The status frame reports `dist 5000`. Don't drive it unattended through dogbrain until the ultrasonic stop is added.
- **Untested on hardware:** the gait maths is unit-tested (`test/`), and the sketch has been checked for syntax against stub headers. It hasn't been compiled against the real ESP32 core or run on a dog yet.
- **Knee linkage:** like Hiwonder's code, the knee is treated as one-to-one with its servo, with no linkage correction.

## Test

```
cd firmware && g++ -std=c++17 -Ismooth_gait test/test_gait.cpp -o test/test_gait && ./test/test_gait
```
