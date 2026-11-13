# MechDog companion

A local-AI companion brain for the Hiwonder MechDog robot dog. The dog sends camera video and microphone audio to your computer. The computer runs vision, speech recognition, an LLM and text-to-speech locally, then sends back speech and safe movement commands.

```
 MechDog                                   Your computer (dogbrain)
 ┌──────────────────────┐                  ┌──────────────────────────────────────────┐
 │ ESP32-S3 camera      │── MJPEG ────────▶│ vision   YOLO, face ID, VLM on request   │
 │ ESP32-S3 audio head  │◀─ WebSocket ────▶│ audio    wake word, Whisper, Piper       │
 │ ESP32 body           │◀─ Bluetooth ────▶│ link ◀── tools ◀── brain (LLM + memory)  │
 │  dog/main.py         │                  │          behaviour: greetings, idle      │
 └──────────────────────┘                  └──────────────────────────────────────────┘
```

## What's here

| Path | Runs on | What it does |
| --- | --- | --- |
| `dog/main.py` | dog body | Takes `CMD|...|$` commands over Bluetooth and serial. Stops by itself after 1 s without a heartbeat, if Bluetooth drops, or if something is within 15 cm. The Hiwonder phone app still works |
| `head/head.ino` | ESP32-S3 audio board | Streams the mic to the computer, plays speech back, and bridges the dog's serial port over Wi-Fi |
| `dogbrain/` | computer | The brain. `python -m dogbrain` runs everything |
| `dogbrain/link.py` | computer | Talks to the dog over Bluetooth, serial, Wi-Fi bridge, or a simulator |
| `dogbrain/vision.py` | computer | Camera stream, YOLO objects, face recognition, "what do you see?" |
| `dogbrain/audio.py` | computer | Wake word, voice activity, speech-to-text, text-to-speech |
| `dogbrain/brain.py` | computer | LLM with persona and tool calling (Ollama, or Claude optionally) |
| `dogbrain/tools.py` | computer | The 11 safe tools the AI can use: move, turn, stop, tricks, look, find, approach, light, remember, recall, status |
| `dogbrain/memory.py` | computer | Long-term memory (SQLite) |
| `dogbrain/behavior.py` | computer | Greets people it knows, idle antics, quiet hours, low battery |
| `dogbrain/mcp_server.py` | computer | Lets Claude Desktop / Claude Code drive the dog |
| `util/` | computer | `ble_probe.py` (Bluetooth troubleshooting), `see.py` (camera test) |
| `tests/` | computer | `pytest` suite using a simulated dog |

## Setup (Mac)

```
brew install python@3.11 portaudio
git clone https://github.com/AndroidDev77/MechDog.git && cd MechDog
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

brew install ollama && ollama serve &          # or the Ollama app
ollama pull qwen2.5:7b                          # brain (32 GB+ Macs: qwen2.5:14b)
ollama pull moondream                           # "what do you see?"

cp config.example.toml config.toml              # then edit it
```

Windows works too: same steps with `py -3.11`, and `venv\Scripts\activate`. Whisper uses `faster-whisper` there.

## Step by step

**1. Try the brain alone** (no dog, no camera, type instead of talk):
```
python -m dogbrain --text --sim
```

**2. Install the dog program.** Open `dog/main.py` in Hiwonder's Python editor, upload it as `main.py`, reboot the dog, and close the phone app. Then:
```
python -m dogbrain.link --ble
dog> status
dog> walk 40 0
dog> stop
```

**3. Calibrate movement** so "move 50 cm" means 50 cm. Time `walk 60 0` over 1 m, then set `walk_speed_cm_s` (100 ÷ seconds). Time a full turn at `walk 0 30`, then set `turn_rate_deg_s` (360 ÷ seconds). Also check what `status` shows for battery. The code assumes millivolts (a full 2S pack reads about 8400). If yours shows a different scale, change `low_battery_mv`.

**4. Camera.** Put the vision module on your Wi-Fi, set `camera.url` in `config.toml`, then:
```
python -m dogbrain.vision watch          # prints what it sees
python -m dogbrain.vision enroll Chris   # teach it your face (look at it, turn your head a bit)
```

**5. Voice with the computer's mic** (before the head board arrives): set `audio.source = "local"` and run
```
python -m dogbrain.audio --say "Hello, I am Rex"
python -m dogbrain
```
Say "hey jarvis" (the default wake word), then talk. After it answers you have 6 seconds to reply without the wake word. Saying "stop" always stops it straight away.

**6. Head audio board.** Wire an ESP32-S3 + INMP441 mic + NS4168 (or MAX98357A) amp as in `head/config.h`, powered from a spare 5 V module port on the dog. Fill in Wi-Fi and your Mac's IP, then flash `head/head.ino` (Arduino IDE 2, esp32 core 3.x, "WebSockets" library by Markus Sattler). Set `audio.source = "head"`.
To drop Bluetooth and drive the dog through the head board too, wire the board to the dog's serial port and set `dog.transport = "tcp"` with `address = "<board IP>:2323"`.

## Talking to it

Things it can do by voice: "come here", "go find Chris", "what do you see?", "is that cup red?", "turn around", "sit", "shake hands", "remember that my sister's name is Amy", "what's my sister's name?", "how's your battery?"

On its own it greets people it recognises (at most once every 30 minutes each), looks around or stretches when nobody has talked to it for a while, stays still during quiet hours, and sits down and says so when the battery runs low.

Eye light: blue = ready, green = listening, purple = thinking, red = blocked, orange = low battery.

## Change the wake word

openWakeWord ships `hey_jarvis`, `alexa`, `hey_mycroft` and `hey_rhasspy`. For "hey Rex", train a custom model with openWakeWord's training notebook and set `audio.wake_word` to the `.onnx` file's path.

## Using Claude

- **As the brain:** set `brain.provider = "claude"` and export `ANTHROPIC_API_KEY`. Only text goes to the API; audio and video stay on your computer.
- **As a remote control:** add `dogbrain.mcp_server` to Claude Desktop (instructions at the top of that file). Don't run it while `python -m dogbrain` is running, because the dog takes one Bluetooth connection at a time.

## Protocol

Frames are `CMD|code|args...|$` over Bluetooth or serial (9600 8N1). Codes 10+ are ours; Hiwonder's app codes 1–6 still work.

| Command | Meaning |
| --- | --- |
| `CMD|10|$` | stop |
| `CMD|11|stride|turn|$` | walk: stride mm -60..120, turn deg -40..40. Resend within 1 s or it stops |
| `CMD|12|name|$` | built-in action group by name |
| `CMD|13|roll|pitch|height|$` | body pose (±17°, height -25..15 mm) |
| `CMD|14|$` | default standing pose |
| `CMD|15|$` | status / heartbeat. Reply `CMD|15|dist_mm|roll|pitch|battery|state|$` |
| `CMD|16|r|g|b|$` | eye LED colour |
| `CMD|18|$` | ping |

Events from the dog: `CMD|19|blocked|dist_mm|$`, `CMD|19|watchdog|0|$`, `CMD|19|done|action|$`, `CMD|19|error|reason|$`.

## Status

Everything on the computer side is tested against a simulated dog (`pytest`). Nothing has run on real hardware yet. Expect to tune `dog/main.py` (body pose, Bluetooth message length), the movement calibration, and the head board's mic gain (`MIC_GAIN_SHIFT`).
