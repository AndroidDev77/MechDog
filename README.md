# mechdog-companion

A local-AI companion brain for the Hiwonder MechDog robot dog. The dog streams camera video and microphone audio to a computer on the home network. The computer runs vision, speech and an LLM locally, then sends back speech and safe movement commands.

## Status

Phase 0–2 starter scripts:

| File | Runs on | What it does |
| --- | --- | --- |
| `dog/main.py` | the dog | Takes `CMD|...|$` commands over Bluetooth LE **and** the serial port: walk, stop, actions, pose, LED, status. Stops on its own after 1 s without a heartbeat, if Bluetooth drops, or if something is within 15 cm ahead. The Hiwonder phone app still works with it |
| `dog_link.py` | PC | Python API + interactive shell that drives the dog over Bluetooth (no cable), a USB-to-TTL adapter, or a Wi-Fi serial bridge |
| `see.py` | PC | Pulls the ESP32-S3 vision module's MJPEG stream and runs YOLO on it (Apple Silicon GPU or CPU) |
| `util/ble_probe.py` | PC | Troubleshooting: lists the dog's Bluetooth services and sends raw test commands |

## Controlling the dog

1. Open `dog/main.py` in Hiwonder's Python editor and upload it to the dog as `main.py`. Reboot the dog.
2. Close the Hiwonder phone app (the dog takes one Bluetooth connection at a time).
3. Run `python dog_link.py --ble`. It finds `MechDog_XX` by itself.

No Bluetooth on the PC? Wire a 3.3 V USB-to-TTL adapter to the dog's 4-pin serial port (TX to RX, RX to TX, GND to GND, VCC unconnected) and use `--port /dev/tty.usbserial-XXXX` instead.

```
dog> walk 50 0      # forward; keeps going until stop
dog> walk 40 20     # forward + turn left
dog> stop
dog> act sit_dowm   # 'actions' lists all 15
dog> status         # distance, roll, pitch, battery, state
```

### Protocol

Frames are `CMD|code|args...|$`, the same framing as Hiwonder's app and serial programs. Codes 10+ are ours; Hiwonder's app codes 1–6 are still accepted.

| Command | Meaning |
| --- | --- |
| `CMD|10|$` | stop |
| `CMD|11|stride|turn|$` | walk, stride mm -60..120, turn deg -40..40. Refresh within 1 s or it stops |
| `CMD|12|name|$` | built-in action group by name |
| `CMD|13|roll|pitch|height|$` | body pose (±17°, height -25..15 mm) |
| `CMD|14|$` | default standing pose |
| `CMD|15|$` | status / heartbeat. Reply `CMD|15|dist_mm|roll|pitch|battery|state|$` |
| `CMD|16|r|g|b|$` | eye LED colour |
| `CMD|18|$` | ping |

Events from the dog: `CMD|19|blocked|dist_mm|$`, `CMD|19|watchdog|0|$`, `CMD|19|done|action|$`, `CMD|19|error|reason|$`.

Hiwonder app codes kept: `CMD|3|dir|$` walk (0 stop, 3 forward, 1/5 left/right, 7 back), `CMD|2|1|n|$` action 1–15, `CMD|1|5|$` reset pose, `CMD|4|1|$` distance, `CMD|4|3|r|g|b|$` LED, `CMD|6|$` battery.

## Setup

```
python3 -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```
python see.py --cam <camera-ip> --show
python util/ble_probe.py scan
python util/ble_probe.py probe <address>
```

Close the Hiwonder phone app before probing; the dog accepts one Bluetooth connection at a time.

## Roadmap

1. Remote control from the computer (`dog_link.py`)
2. Vision events: objects, faces
3. Audio: ESP32-S3 mic/speaker board streaming to the computer
4. Brain: local LLM with persona, memory and robot tools
5. Companion behaviour and safety hardening
