import time

import numpy as np

from dogbrain.audio import RATE, VAD, Ears
from dogbrain.headchat import ChatCore

FRAME = RATE // 50


class FakeHead:
    def __init__(self, frames=(), connected=True):
        self.frames = list(frames)
        self.connected = connected
        self.playing = False
        self.volume = 0.8
        self.played = []

    def read(self, timeout=0.5):
        if self.frames:
            return self.frames.pop(0)
        time.sleep(0.01)
        return None

    def play(self, pcm):
        self.playing = True
        self.played.append(len(pcm))
        self.playing = False

    def stop(self):
        pass

    def set_volume(self, v):
        self.volume = v


def tone(n, amp):
    return list((np.sin(np.arange(n * FRAME) / 5) * amp).astype(np.int16).reshape(n, FRAME))


def energy_vad():
    v = VAD()
    v.model = None
    return v


def events_until(core, kind, timeout=2.0):
    got, end = [], time.time() + timeout
    while time.time() < end:
        try:
            ev = core.events.get(timeout=0.05)
        except Exception:
            continue
        got.append(ev)
        if ev[0] == kind:
            return got
    return got


def make(cfg, head, listen="everything"):
    core = ChatCore(cfg, head, speaker=lambda text: np.zeros(len(text) * 100, dtype=np.int16),
                    stt=lambda audio: "hello dog", vad=energy_vad(), listen=listen)
    core.start()
    return core


def test_hears_without_wake_word(cfg):
    head = FakeHead(tone(5, 0) + tone(40, 8000) + tone(60, 0))
    core = make(cfg, head)
    try:
        evs = events_until(core, "heard")
        assert ("heard", "hello dog") in evs
        assert core.ears.wake is None          # no wake word model loaded in this mode
    finally:
        core.close()


def test_typed_text_is_spoken(cfg):
    head = FakeHead()
    core = make(cfg, head)
    try:
        events_until(core, "voice")
        core.say("  sit down  ")
        evs = events_until(core, "said")
        assert ("said", "sit down") in evs
        time.sleep(0.1)
        assert head.played == [len("sit down") * 100]
    finally:
        core.close()


def test_not_spoken_when_head_missing(cfg):
    head = FakeHead(connected=False)
    core = make(cfg, head)
    try:
        events_until(core, "voice")
        core.say("anyone there?")
        evs = events_until(core, "error")
        assert evs[-1][0] == "error" and head.played == []
    finally:
        core.close()


def test_listening_off_hears_nothing(cfg):
    head = FakeHead()
    core = make(cfg, head, listen="off")
    try:
        events_until(core, "mic")
        time.sleep(0.2)
        head.frames = tone(5, 0) + tone(40, 8000) + tone(60, 0)
        evs = events_until(core, "heard", timeout=0.8)
        assert not any(k == "heard" for k, _ in evs)
    finally:
        core.close()


def test_volume_goes_to_the_head(cfg):
    head = FakeHead()
    core = make(cfg, head)
    try:
        core.set_volume(0.3)
        assert head.volume == 0.3
    finally:
        core.close()


def test_ears_always_mode_keeps_listening(cfg):
    frames = tone(5, 0) + tone(30, 8000) + tone(60, 0) + tone(30, 8000) + tone(60, 0)
    heard = []
    ears = Ears(FakeHead(frames), cfg, on_text=heard.append, vad=energy_vad(),
                stt=lambda a: "again", always=True)
    ears._alive = True
    __import__("threading").Thread(target=ears.run, daemon=True).start()
    time.sleep(0.5)
    ears._alive = False
    assert heard == ["again", "again"]
