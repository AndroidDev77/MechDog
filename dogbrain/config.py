"""Settings. Defaults live here; override any of them in config.toml (see config.example.toml)."""
import copy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DEFAULTS = {
    "dog": {
        "transport": "ble",          # ble | serial | tcp | sim
        "address": "",               # BLE address (blank = find MechDog_XX), serial port, or host:port
        "walk_speed_cm_s": 10.0,     # calibrate: time a 'walk 60 0' over 1 m
        "turn_rate_deg_s": 30.0,     # calibrate: time a full turn at 'walk 0 30'
        "max_walk_s": 6.0,           # longest single move the AI may ask for
        "low_battery_mv": 6800,      # 2S pack: warn and stop walking below this
    },
    "camera": {
        "enabled": True,
        "url": "http://192.168.5.1:81/stream",
        "yolo_model": "yolo11n.pt",
        "fps": 5,
        "conf": 0.45,
        "face_match": 0.36,          # SFace cosine threshold; higher = stricter
    },
    "audio": {
        "source": "head",            # head (ESP32-S3 board) | local (computer mic/speaker) | text
        "ws_host": "0.0.0.0",
        "ws_port": 8765,
        "wake_word": "hey_jarvis",   # openWakeWord model name, or a path to your own .onnx/.tflite
        "wake_threshold": 0.5,
        "follow_up_s": 6.0,          # keep listening this long after a reply, no wake word needed
        "max_utterance_s": 15.0,
        "stt_model": "small",        # whisper size: tiny / base / small / medium
        "piper_voice": "en_US-lessac-medium",
        "volume": 0.8,
    },
    "brain": {
        "provider": "ollama",        # ollama | claude
        "model": "qwen2.5:7b",       # any Ollama model with tool calling
        "vision_model": "moondream", # Ollama vision model for "what do you see?"
        "claude_model": "claude-sonnet-5-5",
        "ollama_host": "http://localhost:11434",
        "history_turns": 12,
        "name": "Rex",
        "owner": "Chris",
        "persona": (
            "You are {name}, a small robot dog and companion to {owner}. You are curious, warm, "
            "a bit playful, and loyal. You speak out loud through a small speaker, so keep replies "
            "to one or two short sentences, no lists, no emoji, no markdown. You can move, look "
            "around and remember things using your tools. Use a tool when the person asks you to do "
            "something physical or asks what you see; otherwise just talk. Never pretend you did "
            "something you didn't do. If a tool reports a problem, say so plainly."
        ),
    },
    "behavior": {
        "enabled": True,
        "idle_after_s": 90,          # start idle antics after this long without talk
        "greet_cooldown_min": 30,    # don't greet the same person more often than this
        "quiet_start": "22:30",      # no idle movement or greetings between these times
        "quiet_end": "07:30",
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config.toml"
    cfg = DEFAULTS
    if path.exists():
        with open(path, "rb") as f:
            cfg = _merge(DEFAULTS, tomllib.load(f))
    DATA.mkdir(exist_ok=True)
    return cfg
