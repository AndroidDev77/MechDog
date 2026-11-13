"""
The tools the AI can use, and the safety rules around them.

The LLM never sends raw servo values. It asks for things like "move forward 50 cm"
or "find Chris", and Body turns that into short, checked commands to the dog.
"""
import time

ACTIONS = {
    "sit": "sit_dowm",
    "lie_down": "go_prone",
    "stand": "stand_four_legs",
    "stand_on_hind_legs": "stand_two_legs",
    "shake_hands": "handshake",
    "bow": "scrape_a_bow",
    "nod": "nodding_motion",
    "stretch": "stretch_oneself",
    "push_ups": "press_up",
    "kick_left": "left_foot_kick",
    "kick_right": "right_foot_kick",
    "boxing": "boxing",
    "wiggle": "rotation_roll",
}
COLORS = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 64, 255), "yellow": (255, 200, 0),
    "purple": (160, 0, 255), "white": (255, 255, 255), "orange": (255, 100, 0), "off": (0, 0, 0),
}

# Provider-neutral JSON-schema tool specs (OpenAI/Ollama "function" shape).
SPECS = [
    {"name": "move", "description": "Walk forward or backward a distance.",
     "parameters": {"type": "object", "properties": {
         "direction": {"type": "string", "enum": ["forward", "backward"]},
         "distance_cm": {"type": "number", "description": "how far, 5-100 cm"}},
         "required": ["direction", "distance_cm"]}},
    {"name": "turn", "description": "Turn in place. Positive degrees = left, negative = right.",
     "parameters": {"type": "object", "properties": {
         "degrees": {"type": "number", "description": "-180 to 180"}},
         "required": ["degrees"]}},
    {"name": "stop", "description": "Stop moving immediately.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "do_action", "description": "Perform a body action or trick.",
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)}},
         "required": ["action"]}},
    {"name": "look", "description": "See what is in front of you. Pass a question to look closely "
                                    "(slower), e.g. 'what color is the cup?'.",
     "parameters": {"type": "object", "properties": {
         "question": {"type": "string"}}}},
    {"name": "find", "description": "Turn around until you see a person or object, e.g. 'Chris', "
                                    "'person', 'cat', 'ball'.",
     "parameters": {"type": "object", "properties": {
         "target": {"type": "string"}}, "required": ["target"]}},
    {"name": "approach", "description": "Find a person or object and walk up to it (stops about "
                                        "35 cm away). Use for 'come here'.",
     "parameters": {"type": "object", "properties": {
         "target": {"type": "string"}}, "required": ["target"]}},
    {"name": "set_light", "description": "Set the colour of your eye light.",
     "parameters": {"type": "object", "properties": {
         "color": {"type": "string", "enum": list(COLORS)}}, "required": ["color"]}},
    {"name": "remember", "description": "Save a fact for later, e.g. 'Chris's birthday is May 3'.",
     "parameters": {"type": "object", "properties": {
         "fact": {"type": "string"}}, "required": ["fact"]}},
    {"name": "recall", "description": "Search your long-term memory.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
    {"name": "status", "description": "Check your battery, the distance ahead and your camera.",
     "parameters": {"type": "object", "properties": {}}},
]


class Body:
    def __init__(self, link, vision, memory, cfg: dict):
        self.link, self.vision, self.memory = link, vision, memory
        self.cfg = cfg["dog"]
        self.abort = False               # set by stop() / safety to cancel long tool runs

    # ---- dispatch ------------------------------------------------------------
    def run(self, name: str, args: dict | None) -> str:
        args = args or {}
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"error: no tool named {name}"
        try:
            return fn(**args)
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}"
        except Exception as e:
            self.link.stop()
            return f"error: {name} failed: {e}"

    # ---- helpers ---------------------------------------------------------------
    def battery_mv(self) -> int | None:
        try:
            return int(self.link.status.get("battery"))
        except (TypeError, ValueError):
            return None

    def battery_low(self) -> bool:
        mv = self.battery_mv()
        return mv is not None and 0 < mv < self.cfg["low_battery_mv"]

    def _drive(self, stride: int, turn: int, seconds: float) -> str:
        """Walk for a time, watching for blocks, aborts and a lost link."""
        if self.battery_low():
            return "refused: battery is too low to walk"
        seconds = min(seconds, self.cfg["max_walk_s"])
        self.abort = False
        start = time.time()
        self.link.walk(stride, turn)
        try:
            while time.time() - start < seconds:
                if self.abort:
                    return "stopped: told to stop"
                if not self.link.walking:          # dog reported blocked / watchdog
                    ev = self.link.events[-1] if self.link.events else None
                    if ev and ev[1] == "blocked":
                        return f"stopped early: obstacle {int(ev[2]) // 10} cm ahead"
                    return "stopped early"
                time.sleep(0.05)
        finally:
            self.link.stop()
        return "done"

    # ---- tools -----------------------------------------------------------------
    def t_move(self, direction: str = "forward", distance_cm: float = 30) -> str:
        d = max(5.0, min(float(distance_cm), 100.0))
        stride = 60 if direction == "forward" else -40
        speed = self.cfg["walk_speed_cm_s"] * (1 if stride > 0 else 0.6)
        r = self._drive(stride, 0, d / speed)
        return f"moved {direction} about {d:.0f} cm" if r == "done" else r

    def t_turn(self, degrees: float = 90) -> str:
        deg = max(-180.0, min(float(degrees), 180.0))
        if abs(deg) < 5:
            return "done"
        r = self._drive(0, 30 if deg > 0 else -30, abs(deg) / self.cfg["turn_rate_deg_s"])
        return f"turned {'left' if deg > 0 else 'right'} about {abs(deg):.0f} degrees" \
            if r == "done" else r

    def t_stop(self) -> str:
        self.abort = True
        self.link.stop()
        return "stopped"

    def t_do_action(self, action: str = "") -> str:
        name = ACTIONS.get(action)
        if not name:
            return f"error: unknown action {action}"
        since = time.time()
        self.link.action(name)
        ev = self.link.wait_event(("done", "error"), since, timeout=8)
        if ev is None:
            return f"started {action}"
        return f"did {action}" if ev[0] == "done" else f"couldn't {action}: {ev[1]}"

    def t_look(self, question: str = "") -> str:
        if self.vision is None:
            return "no camera configured"
        if question:
            return self.vision.describe(question)
        return f"I see: {self.vision.summary()}"

    def t_find(self, target: str = "person") -> str:
        if self.vision is None or not self.vision.connected:
            return "can't look: camera offline"
        for step in range(13):                     # up to a full circle in 30 degree steps
            time.sleep(0.6)                         # let detection catch up after moving
            side = self.vision.sees(target)
            if side == "ahead":
                return f"found {target} straight ahead"
            if side in ("left", "right"):
                self.t_turn(15 if side == "left" else -15)
                return f"found {target}, now facing them"
            if self.abort:
                return "stopped: told to stop"
            if step < 12:
                r = self.t_turn(30)
                if r.startswith(("refused", "stopped")):
                    return r
        return f"looked all around but couldn't find {target}"

    def t_approach(self, target: str = "person") -> str:
        r = self.t_find(target)
        if not r.startswith("found"):
            return r
        for _ in range(8):
            dist = self.link.status.get("distance_mm", 9999) / 10
            if dist < 40:
                return f"I'm right in front of {target}"
            side = self.vision.sees(target) if self.vision else "ahead"
            if side is None:
                return f"lost sight of {target}"
            if side != "ahead":
                self.t_turn(15 if side == "left" else -15)
            r = self.t_move("forward", min(30, max(5, dist - 35)))
            if not r.startswith("moved"):
                return r if "obstacle" not in r else f"I'm close to {target}"
        return f"got closer to {target}"

    def t_set_light(self, color: str = "blue") -> str:
        self.link.led(*COLORS.get(color, COLORS["blue"]))
        return f"light is {color}"

    def t_remember(self, fact: str = "") -> str:
        return self.memory.remember(fact)

    def t_recall(self, query: str = "") -> str:
        hits = self.memory.recall(query)
        return "; ".join(hits) if hits else "nothing found"

    def t_status(self) -> str:
        s = self.link.status
        if not s:
            return "no contact with my body"
        mv = self.battery_mv()
        batt = f"battery {mv / 1000:.1f} V" + (" (low)" if self.battery_low() else "") \
            if mv else "battery unknown"
        cam = "camera on" if self.vision and self.vision.connected else "camera off"
        return f"{batt}, {s.get('distance_mm', 0) // 10} cm clear ahead, {cam}"
