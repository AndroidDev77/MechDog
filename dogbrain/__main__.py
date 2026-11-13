"""
Run the companion:

    python -m dogbrain                 # uses config.toml
    python -m dogbrain --text          # type instead of talk (no mic/speaker needed)
    python -m dogbrain --text --sim    # no dog, no camera: try the brain on its own
"""
import argparse
import re
import threading
import time

from .config import load
from .link import DogLink
from .memory import Memory
from .tools import Body
from .brain import Brain
from .behavior import Behavior

STOP_WORDS = re.compile(r"^\W*(stop|halt|freeze|stay)\W*$", re.I)


def connect_dog(cfg: dict) -> DogLink:
    d = cfg["dog"]
    t, addr = d["transport"], d["address"]
    if t == "sim":
        return DogLink.sim()
    if t == "serial":
        return DogLink.serial(addr)
    if t == "tcp":
        host, port = addr.rsplit(":", 1)
        return DogLink.tcp(host, int(port))
    return DogLink.ble(addr or None)


class App:
    def __init__(self, cfg: dict, text_mode: bool):
        self.cfg = cfg
        self.speak_lock = threading.Lock()

        print("Connecting to the dog...")
        self.link = connect_dog(cfg)
        for _ in range(30):
            if self.link.status:
                break
            time.sleep(0.1)
        print("  body:", self.link.status or "no status yet (is dog/main.py running?)")

        self.memory = Memory()
        self.vision = None
        self.body = Body(self.link, None, self.memory, cfg)
        self.brain = Brain(cfg, self.body, None, self.memory)
        self.behavior = Behavior(cfg, self.brain, self.body, self.memory, self.say)

        if cfg["camera"]["enabled"]:
            from .vision import Vision
            self.vision = Vision(cfg, on_event=self.behavior.on_vision_event)
            self.vision.start()
            self.body.vision = self.brain.vision = self.vision

        self.io = self.ears = self.tts = None
        if not text_mode:
            from .audio import Ears, Speaker, make_io
            self.io = make_io(cfg)
            print("Loading voice...")
            self.tts = Speaker(cfg["audio"]["piper_voice"])
            print("Loading speech recognition and wake word...")
            self.ears = Ears(self.io, cfg, on_text=self.on_text, on_wake=self.on_wake)

        self.link.on_event = self.on_dog_event

    # ---- speech ----------------------------------------------------------------
    def say(self, text: str, follow_up: bool = True) -> None:
        if not text:
            return
        with self.speak_lock:
            print(f"{self.cfg['brain']['name']}: {text}")
            if self.tts and self.io:
                self.io.play(self.tts(text))
                while self.io.read(timeout=0) is not None:   # drop our own echo
                    pass
        if follow_up and self.ears:
            self.ears.open_follow_up()

    def on_wake(self) -> None:
        self.body.t_set_light("green")

    def on_text(self, text: str) -> None:
        print(f"you: {text}")
        if STOP_WORDS.match(text):
            self.body.t_stop()
            self.say("Stopping.", follow_up=False)
            return
        self.body.t_set_light("purple")                # thinking
        try:
            reply = self.brain.respond(text)
        except Exception as e:
            reply = "Sorry, my brain isn't responding right now."
            print(f"  [brain error] {e}")
        self.body.t_set_light("blue")
        self.say(reply)

    def on_dog_event(self, event: str, detail: str) -> None:
        if event in ("blocked", "watchdog"):
            self.brain.note(f"body stopped: {event} {detail}")

    # ---- run -------------------------------------------------------------------
    def run(self, text_mode: bool) -> None:
        if self.cfg["behavior"]["enabled"]:
            self.behavior.start()
        self.body.t_set_light("blue")
        name = self.cfg["brain"]["name"]
        if text_mode:
            print(f"\nTalk to {name} by typing. Ctrl-C or 'quit' to exit.\n")
            while True:
                try:
                    text = input("you: ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if text.lower() in ("quit", "exit"):
                    break
                if text:
                    print("\033[F\033[K", end="")      # we re-print it in on_text
                    self.on_text(text)
        else:
            wake = self.cfg["audio"]["wake_word"].replace("_", " ")
            print(f"\nSay '{wake}' to talk to {name}. Ctrl-C to exit.\n")
            self.ears.start()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        self.link.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="MechDog AI companion")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--text", action="store_true", help="type instead of talk")
    ap.add_argument("--sim", action="store_true", help="simulated dog, camera off")
    args = ap.parse_args()

    cfg = load(args.config)
    if args.sim:
        cfg["dog"]["transport"] = "sim"
        cfg["camera"]["enabled"] = False
    text_mode = args.text or cfg["audio"]["source"] == "text"
    App(cfg, text_mode).run(text_mode)


if __name__ == "__main__":
    main()
