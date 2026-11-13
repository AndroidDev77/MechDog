import time

import pytest

from dogbrain.link import DogLink
from dogbrain.memory import Memory
from dogbrain.tools import Body


class FakeVision:
    connected = True

    def __init__(self, appears_after_turns=None, target="chris", side="ahead"):
        self.turns_left = appears_after_turns
        self.target, self.side = target, side

    def sees(self, target):
        if self.turns_left is None or target.lower() != self.target:
            return None
        return self.side if self.turns_left <= 0 else None

    def summary(self):
        return "Chris (ahead), cup (left)"

    def describe(self, q):
        return f"answer to {q}"


@pytest.fixture
def body(cfg, tmp_path):
    link = DogLink.sim()
    for _ in range(30):
        if link.status:
            break
        time.sleep(0.05)
    b = Body(link, FakeVision(), Memory(tmp_path / "m.db"), cfg)
    yield b
    link.close()


def test_move_and_turn(body):
    assert body.run("move", {"direction": "forward", "distance_cm": 50}).startswith("moved forward")
    assert ["11", "60", "0"] in body.link.t.log and ["10"] in body.link.t.log
    assert body.run("turn", {"degrees": -90}).startswith("turned right")
    assert ["11", "0", "-30"] in body.link.t.log


def test_move_stops_for_obstacle(body, cfg):
    cfg["dog"]["walk_speed_cm_s"] = 20.0
    body.link.t.distance_mm = 90
    r = body.run("move", {"direction": "forward", "distance_cm": 40})
    assert r.startswith("stopped early: obstacle 9 cm")


def test_low_battery_refuses_walking(body):
    body.link.t.battery = 6500
    time.sleep(0.5)
    assert body.run("move", {"direction": "forward", "distance_cm": 20}).startswith("refused")
    assert "(low)" in body.run("status", {})


def test_action_waits_for_done(body):
    assert body.run("do_action", {"action": "shake_hands"}) == "did shake_hands"
    assert body.run("do_action", {"action": "fly"}).startswith("error")


def test_find_turns_until_seen(body):
    v = FakeVision(appears_after_turns=2)
    body.vision = v
    orig = body.t_turn

    def counting_turn(d):
        v.turns_left -= 1
        return orig(d)

    body.t_turn = counting_turn
    assert body.run("find", {"target": "Chris"}) == "found Chris straight ahead"


def test_look_and_memory_tools(body):
    assert body.run("look", {}) == "I see: Chris (ahead), cup (left)"
    assert body.run("look", {"question": "what color?"}) == "answer to what color?"
    assert body.run("remember", {"fact": "Chris likes coffee"}) == "remembered"
    assert "coffee" in body.run("recall", {"query": "what does Chris like"})


def test_bad_tool_and_args(body):
    assert body.run("teleport", {}).startswith("error")
    assert body.run("turn", {"angle": 3}).startswith("error: bad arguments")
