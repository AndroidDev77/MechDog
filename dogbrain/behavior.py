"""Companion behaviour: greetings, idle antics, quiet hours, low battery."""
import random
import threading
import time


def in_quiet_hours(start: str, end: str, now=None) -> bool:
    t = time.localtime(now)
    m = t.tm_hour * 60 + t.tm_min
    s = int(start[:2]) * 60 + int(start[3:])
    e = int(end[:2]) * 60 + int(end[3:])
    return (s <= m < e) if s <= e else (m >= s or m < e)   # handles ranges past midnight


def ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours"
    return f"{int(seconds // 86400)} days"


class Behavior:
    def __init__(self, cfg: dict, brain, body, memory, say):
        self.cfg = cfg["behavior"]
        self.brain, self.body, self.memory, self.say = brain, body, memory, say
        self.last_greet: dict[str, float] = {}
        self.warned_battery = False
        self.next_idle = time.time() + self.cfg["idle_after_s"]
        self._alive = False

    def quiet(self) -> bool:
        return in_quiet_hours(self.cfg["quiet_start"], self.cfg["quiet_end"])

    # called from vision's event thread
    def on_vision_event(self, kind: str, data: dict) -> None:
        if kind == "face_appeared":
            name = data["name"]
            prev = self.memory.saw(name) if self.memory else None
            self.brain.note(f"{name} came into view")
            if self.quiet() or not self.cfg["enabled"]:
                return
            now = time.time()
            if now - self.last_greet.get(name, 0) < self.cfg["greet_cooldown_min"] * 60:
                return
            if now - self.brain.last_talk < 20:
                return                              # mid-conversation; don't interrupt
            self.last_greet[name] = now
            when = f" You last saw them {ago(now - prev)} ago." if prev else \
                " This is the first time you've seen them."
            threading.Thread(target=lambda: self.say(self.brain.prompt(
                f"{name} just came into view.{when} Greet them."), True), daemon=True).start()
        elif kind == "object_appeared" and data["label"] in ("cat", "dog"):
            self.brain.note(f"a {data['label']} came into view")

    def start(self) -> None:
        self._alive = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while self._alive:
            time.sleep(1)
            self._battery()
            if not self.cfg["enabled"] or self.quiet():
                continue
            now = time.time()
            if now - self.brain.last_talk < self.cfg["idle_after_s"] or now < self.next_idle:
                continue
            self.next_idle = now + random.uniform(60, 180)
            self._idle()

    def _battery(self) -> None:
        low = self.body.battery_low()
        if low and not self.warned_battery:
            self.warned_battery = True
            self.body.t_stop()
            self.body.t_set_light("orange")
            self.say("My battery is getting low. I'll sit down until I'm charged.", False)
            self.body.t_do_action("sit")
        elif not low and self.warned_battery and self.body.battery_mv():
            mv = self.body.battery_mv()
            if mv > self.body.cfg["low_battery_mv"] + 300:   # hysteresis
                self.warned_battery = False
                self.body.t_set_light("blue")

    def _idle(self) -> None:
        if self.body.battery_low() or self.body.link.status.get("state") != "idle":
            return
        pick = random.random()
        if pick < 0.5:                                 # look around
            d = random.choice((-1, 1)) * random.randint(20, 40)
            self.body.t_turn(d)
            time.sleep(1.5)
            self.body.t_turn(-d)
        elif pick < 0.75:
            self.body.t_do_action("nod")
        elif pick < 0.9:
            self.body.t_do_action("stretch")
        else:
            self.body.t_do_action("wiggle")
