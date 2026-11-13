# mechdog-companion

A local-AI companion brain for the Hiwonder MechDog robot dog. The dog streams camera video and microphone audio to a computer on the home network. The computer runs vision, speech and an LLM locally, then sends back speech and safe movement commands.

## Status

Phase 0–2 starter scripts:

| File | Runs on | What it does |
| --- | --- | --- |
| `dog/main.py` | the dog | Takes `CMD|...|$` commands over the serial port: walk, stop, actions, pose, LED, status. Stops on its own after 1 s without a heartbeat or if something is within 15 cm ahead |
| `dog_link.py` | PC | Python API + interactive shell that drives the dog over a USB-to-TTL adapter now, or a Wi-Fi serial bridge later |
| `see.py` | PC | Pulls the ESP32-S3 vision module's MJPEG stream and runs YOLO on it (Apple Silicon GPU or CPU) |
| `ble_probe.py` | PC | Optional: lists the dog's Bluetooth LE services |

## Controlling the dog

1. Open `dog/main.py` in Hiwonder's Python editor and upload it to the dog as `main.py`.
2. Wire a 3.3 V USB-to-TTL adapter (CP2102 / CH340) to the dog's 4-pin serial port: TX to RX, RX to TX, GND to GND. Leave its VCC unconnected.
3. Run `python dog_link.py --port /dev/tty.usbserial-XXXX` (`ls /dev/tty.usb*` on macOS, `COMx` on Windows).

```
dog> walk 50 0      # forward; keeps going until stop
dog> walk 40 20     # forward + turn left
dog> stop
dog> act sit_dowm   # 'actions' lists all 15
dog> status         # distance, roll, pitch, battery, state
```

### Protocol (9600 8N1)

| Command | Meaning |
| --- | --- |
| `CMD|0|$` | stop |
| `CMD|1|stride|turn|$` | walk, stride mm -60..80, turn deg -30..30 |
| `CMD|2|name|$` | built-in action group |
| `CMD|3|roll|pitch|height|$` | body pose, each clamped to ±15 |
| `CMD|4|$` | default standing pose |
| `CMD|5|$` | status request / heartbeat. Reply: `CMD|5|dist_mm|roll|pitch|battery|state|$` |
| `CMD|6|r|g|b|$` | eye LED colour |
| `CMD|8|$` | ping |

Events from the dog: `CMD|9|blocked|dist_mm|$`, `CMD|9|watchdog|0|$`, `CMD|9|done|action|$`, `CMD|9|error|reason|$`.

## Setup

```
python3 -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```
python see.py --cam <camera-ip> --show
python ble_probe.py scan
python ble_probe.py probe <address>
```

Close the Hiwonder phone app before probing; the dog accepts one Bluetooth connection at a time.

## Roadmap

1. Remote control from the computer (`dog_link.py`)
2. Vision events: objects, faces
3. Audio: ESP32-S3 mic/speaker board streaming to the computer
4. Brain: local LLM with persona, memory and robot tools
5. Companion behaviour and safety hardening
