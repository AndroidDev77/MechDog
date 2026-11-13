import time

from dogbrain.behavior import Behavior, in_quiet_hours


def at(hh, mm):
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, 0, 0, 0, -1))


def test_quiet_hours_across_midnight():
    assert in_quiet_hours("22:30", "07:30", at(23, 0))
    assert in_quiet_hours("22:30", "07:30", at(3, 0))
    assert not in_quiet_hours("22:30", "07:30", at(12, 0))
    assert in_quiet_hours("13:00", "14:00", at(13, 30))


class FakeBrain:
    last_talk = 0.0

    def __init__(self):
        self.notes, self.prompts = [], []

    def note(self, e):
        self.notes.append(e)

    def prompt(self, s):
        self.prompts.append(s)
        return "Hi!"


class FakeMemory:
    def saw(self, name):
        return time.time() - 7200


def test_greets_once_per_cooldown(cfg):
    cfg["behavior"]["quiet_start"] = cfg["behavior"]["quiet_end"] = "00:00"   # never quiet
    said, brain = [], FakeBrain()
    b = Behavior(cfg, brain, None, FakeMemory(), lambda t, f: said.append(t))
    b.on_vision_event("face_appeared", {"name": "Chris"})
    b.on_vision_event("face_appeared", {"name": "Chris"})
    time.sleep(0.2)
    assert said == ["Hi!"] and "2 hours ago" in brain.prompts[0]
    assert brain.notes.count("Chris came into view") == 2
