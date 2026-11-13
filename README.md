# mechdog-companion

A local-AI companion brain for the Hiwonder MechDog robot dog. The dog streams camera video and microphone audio to a computer on the home network. The computer runs vision, speech and an LLM locally, then sends back speech and safe movement commands.

## Status

Phase 0–2 starter scripts:

| Script | What it does |
| --- | --- |
| `see.py` | Pulls the ESP32-S3 vision module's MJPEG stream and runs YOLO on it (Apple Silicon GPU or CPU) |
| `ble_probe.py` | Finds the dog over Bluetooth LE and lists its services, to work out the movement commands |

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
