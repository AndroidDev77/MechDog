import time

import numpy as np

from dogbrain.audio import RATE, Ears, VAD, resample

FRAME = RATE // 50


class FakeIO:
    def __init__(self, frames):
        self.frames = list(frames)
        self.playing = False

    def read(self, timeout=0.5):
        return self.frames.pop(0) if self.frames else None


class FakeWake:
    def feed(self, pcm):
        return bool(pcm[0] == 7777)

    def reset(self):
        pass


def tone(n, amp):
    return (np.sin(np.arange(n * FRAME) / 5) * amp).astype(np.int16).reshape(n, FRAME)


def energy_vad():
    v = VAD()
    v.model = None          # force the simple energy gate
    return v


def test_wake_then_utterance(cfg):
    wake = np.zeros(FRAME, dtype=np.int16)
    wake[0] = 7777
    frames = [wake] + list(tone(10, 0)) + list(tone(40, 8000)) + list(tone(60, 0))
    heard = []
    ears = Ears(FakeIO(frames), cfg, on_text=heard.append, wake=FakeWake(), vad=energy_vad(),
                stt=lambda a: f"{len(a) // FRAME} frames")
    ears._alive = True
    t = __import__("threading").Thread(target=ears.run, daemon=True)
    t.start()
    time.sleep(0.5)
    ears._alive = False
    assert len(heard) == 1 and int(heard[0].split()[0]) >= 40


def test_no_wake_word_means_nothing_heard(cfg):
    frames = list(tone(80, 8000))
    heard = []
    ears = Ears(FakeIO(frames), cfg, on_text=heard.append, wake=FakeWake(), vad=energy_vad(),
                stt=lambda a: "x" * 5)
    ears._alive = True
    t = __import__("threading").Thread(target=ears.run, daemon=True)
    t.start()
    time.sleep(0.3)
    ears._alive = False
    assert heard == []


def test_follow_up_needs_no_wake_word(cfg):
    frames = list(tone(5, 0)) + list(tone(30, 8000)) + list(tone(60, 0))
    heard = []
    ears = Ears(FakeIO(frames), cfg, on_text=heard.append, wake=FakeWake(), vad=energy_vad(),
                stt=lambda a: "yes please")
    ears.open_follow_up()
    ears._alive = True
    t = __import__("threading").Thread(target=ears.run, daemon=True)
    t.start()
    time.sleep(0.3)
    ears._alive = False
    assert heard == ["yes please"]


def test_resample_length():
    x = np.zeros(22050, dtype=np.int16)
    assert len(resample(x, 22050)) == RATE


def test_short_noise_is_ignored(cfg):
    frames = list(tone(5, 8000)) + list(tone(60, 0))         # 0.1 s blip
    heard = []
    ears = Ears(FakeIO(frames), cfg, on_text=heard.append, wake=FakeWake(), vad=energy_vad(),
                stt=lambda a: "blip")
    ears.open_follow_up()
    ears._alive = True
    t = __import__("threading").Thread(target=ears.run, daemon=True)
    t.start()
    time.sleep(0.3)
    ears._alive = False
    assert heard == []
