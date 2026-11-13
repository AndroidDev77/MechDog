"""
Audio: the dog's ears and voice.

Sources (config audio.source):
  head  - the ESP32-S3 board on the dog (head/head.ino) streams mic audio over WebSocket
          and plays what we send back
  local - this computer's microphone and speaker (for testing before the board arrives)

Pipeline: mic -> wake word (openWakeWord) -> voice activity (Silero) -> Whisper -> text
          text -> Piper TTS -> speaker

Try it:  python -m dogbrain.audio --say "Hello, I am Rex"
         python -m dogbrain.audio --listen
"""
import asyncio
import platform
import queue
import threading
import time
import urllib.request

import numpy as np

from .config import DATA, load

RATE = 16000
FRAME = RATE // 50          # 20 ms


def resample(x: np.ndarray, src: int, dst: int = RATE) -> np.ndarray:
    if src == dst or len(x) == 0:
        return x
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(x.dtype)


# ============================================================================ I/O
class HeadAudio:
    """WebSocket server the head board connects to (ws://<this computer>:8765/head)."""

    def __init__(self, host: str, port: int, volume: float = 0.8):
        self.host, self.port, self.volume = host, port, volume
        self.mic: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=500)
        self.ws = None
        self.playing = False
        self._stop_play = False
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._serve(), self.loop).result(10)

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def _serve(self):
        import websockets
        self.server = await websockets.serve(self._handler, self.host, self.port, max_size=2**20)
        print(f"[audio] waiting for head board on ws://{self.host}:{self.port}/head")

    async def _handler(self, ws, *_):
        if self.ws is not None:
            await self.ws.close()
        self.ws = ws
        print("[audio] head board connected")
        await ws.send(f"vol:{self.volume}")
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    pcm = np.frombuffer(msg, dtype=np.int16)
                    try:
                        self.mic.put_nowait(pcm)
                    except queue.Full:
                        pass
        finally:
            if self.ws is ws:
                self.ws = None
                print("[audio] head board disconnected")

    def read(self, timeout: float = 0.5):
        try:
            return self.mic.get(timeout=timeout)
        except queue.Empty:
            return None

    async def _play(self, pcm: np.ndarray):
        ws = self.ws
        if ws is None:
            return
        lead = 0.25                                   # keep this much audio queued on the board
        start = time.monotonic()
        for i in range(0, len(pcm), FRAME):
            if self._stop_play or self.ws is not ws:
                await ws.send("stop")
                return
            await ws.send(pcm[i:i + FRAME].tobytes())
            ahead = (i + FRAME) / RATE - (time.monotonic() - start)
            if ahead > lead:
                await asyncio.sleep(ahead - lead)
        remaining = len(pcm) / RATE - (time.monotonic() - start)
        if remaining > 0:
            await asyncio.sleep(remaining)

    def play(self, pcm: np.ndarray) -> None:
        """Play 16 kHz int16 mono audio; returns when finished."""
        self.playing, self._stop_play = True, False
        try:
            asyncio.run_coroutine_threadsafe(self._play(pcm), self.loop).result()
        finally:
            self.playing = False

    def stop(self) -> None:
        self._stop_play = True


class LocalAudio:
    """This computer's default mic and speaker."""

    def __init__(self, volume: float = 0.8):
        import sounddevice as sd
        self.sd, self.volume = sd, volume
        self.mic: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=500)
        self.playing = False
        self.connected = True

        def cb(indata, frames, t, status):
            try:
                self.mic.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                     blocksize=FRAME, callback=cb)
        self.stream.start()

    def read(self, timeout: float = 0.5):
        try:
            return self.mic.get(timeout=timeout)
        except queue.Empty:
            return None

    def play(self, pcm: np.ndarray) -> None:
        self.playing = True
        try:
            self.sd.play((pcm * self.volume).astype(np.int16), RATE)
            self.sd.wait()
        finally:
            self.playing = False

    def stop(self) -> None:
        self.sd.stop()


# ============================================================================ wake word / VAD
class WakeWord:
    def __init__(self, model: str, threshold: float):
        from openwakeword.model import Model
        try:
            self.m = Model(wakeword_models=[model], inference_framework="onnx")
        except Exception:
            import openwakeword
            openwakeword.utils.download_models()
            self.m = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = threshold
        self.buf = np.zeros(0, dtype=np.int16)

    def feed(self, pcm: np.ndarray) -> bool:
        self.buf = np.concatenate([self.buf, pcm])
        hit = False
        while len(self.buf) >= 1280:                 # openWakeWord wants 80 ms chunks
            chunk, self.buf = self.buf[:1280], self.buf[1280:]
            scores = self.m.predict(chunk)
            if max(scores.values()) >= self.threshold:
                hit = True
        return hit

    def reset(self) -> None:
        self.buf = np.zeros(0, dtype=np.int16)
        self.m.reset()


class VAD:
    """Silero VAD if available, otherwise a simple energy gate."""

    def __init__(self):
        self.buf = np.zeros(0, dtype=np.int16)
        try:
            import torch
            from silero_vad import load_silero_vad
            self.torch, self.model = torch, load_silero_vad()
        except Exception:
            self.model = None
        self.last = 0.0

    def feed(self, pcm: np.ndarray) -> float:
        """Speech probability for the latest audio."""
        if self.model is None:
            rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
            self.last = min(1.0, rms / 1500.0)
            return self.last
        self.buf = np.concatenate([self.buf, pcm])
        while len(self.buf) >= 512:                  # Silero wants 512-sample chunks at 16 kHz
            chunk, self.buf = self.buf[:512], self.buf[512:]
            x = self.torch.from_numpy(chunk.astype(np.float32) / 32768.0)
            self.last = float(self.model(x, RATE).item())
        return self.last

    def reset(self) -> None:
        self.buf = np.zeros(0, dtype=np.int16)
        if self.model is not None:
            self.model.reset_states()


# ============================================================================ speech <-> text
class Transcriber:
    def __init__(self, size: str):
        self.size = size
        self.mlx = platform.system() == "Darwin" and platform.machine() == "arm64"
        if self.mlx:
            try:
                import mlx_whisper
                self.mlx_whisper = mlx_whisper
                self.repo = f"mlx-community/whisper-{size}-mlx"
            except ImportError:
                self.mlx = False
        if not self.mlx:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(size, compute_type="int8")

    def __call__(self, pcm: np.ndarray) -> str:
        audio = pcm.astype(np.float32) / 32768.0
        if self.mlx:
            r = self.mlx_whisper.transcribe(audio, path_or_hf_repo=self.repo, language="en")
            return r["text"].strip()
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1)
        return " ".join(s.text for s in segs).strip()


class Speaker:
    """Piper TTS. Voices download from Hugging Face on first use into data/voices/."""

    def __init__(self, voice: str):
        from piper import PiperVoice
        self.voice = PiperVoice.load(str(self._fetch(voice)))
        self.rate = self.voice.config.sample_rate

    @staticmethod
    def _fetch(name: str):
        d = DATA / "voices"
        d.mkdir(parents=True, exist_ok=True)
        onnx = d / f"{name}.onnx"
        if not onnx.exists():
            region, speaker, quality = name.split("-")
            base = (f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
                    f"{region.split('_')[0]}/{region}/{speaker}/{quality}/{name}")
            print(f"Downloading voice {name}...")
            urllib.request.urlretrieve(base + ".onnx", onnx)
            urllib.request.urlretrieve(base + ".onnx.json", d / f"{name}.onnx.json")
        return onnx

    def __call__(self, text: str) -> np.ndarray:
        if hasattr(self.voice, "synthesize_stream_raw"):               # piper-tts 1.2
            raw = b"".join(self.voice.synthesize_stream_raw(text))
        else:                                                          # piper-tts 1.3+
            raw = b"".join(c.audio_int16_bytes for c in self.voice.synthesize(text))
        return resample(np.frombuffer(raw, dtype=np.int16), self.rate)


# ============================================================================ the loop
class Ears:
    """Listens continuously; calls on_text(text) for each thing said to the dog."""

    SLEEP, LISTEN = "sleep", "listen"

    def __init__(self, io, cfg: dict, on_text, on_wake=None, wake=None, vad=None, stt=None):
        a = cfg["audio"]
        self.io, self.on_text, self.on_wake = io, on_text, on_wake
        self.wake = wake or WakeWord(a["wake_word"], a["wake_threshold"])
        self.vad = vad or VAD()
        self.stt = stt or Transcriber(a["stt_model"])
        self.follow_up_s = a["follow_up_s"]
        self.max_s = a["max_utterance_s"]
        self.state = self.SLEEP
        self.listen_until = 0.0          # follow-up window end
        self._alive = False

    def start(self) -> None:
        self._alive = True
        threading.Thread(target=self.run, daemon=True).start()

    def open_follow_up(self) -> None:
        """Called after the dog speaks: the person can answer without the wake word."""
        self.state = self.LISTEN
        self.listen_until = time.time() + self.follow_up_s

    def run(self) -> None:
        speech, silent = [], 0
        muted_until = 0.0
        while self._alive:
            pcm = self.io.read()
            if pcm is None:
                continue
            now = time.time()
            if self.io.playing:                       # don't hear ourselves talk
                muted_until = now + 0.4
                continue
            if now < muted_until:
                continue

            if self.state == self.SLEEP:
                if self.wake.feed(pcm):
                    self.wake.reset()
                    self.vad.reset()
                    if self.on_wake:
                        self.on_wake()
                    self.state, self.listen_until = self.LISTEN, now + self.follow_up_s
                    speech, silent = [], 0
                continue

            # LISTEN: wait for speech to start, then record until 0.8 s of silence.
            # Durations are counted in audio samples, not wall-clock time.
            p = self.vad.feed(pcm)
            if not speech:
                if p > 0.5:
                    speech, silent = [pcm], 0
                elif now > self.listen_until:
                    self.state = self.SLEEP            # nobody spoke; go back to sleep
                continue
            speech.append(pcm)
            silent = silent + len(pcm) if p < 0.3 else 0
            total = sum(len(x) for x in speech)
            if silent > 0.8 * RATE or total > self.max_s * RATE:
                audio, voiced = np.concatenate(speech), total - silent
                speech, silent = [], 0
                self.state = self.SLEEP
                self.vad.reset()
                if voiced > RATE * 0.4:
                    text = self.stt(audio)
                    if text and len(text.strip(" .,!?")) > 1:
                        self.on_text(text)


def make_io(cfg: dict):
    a = cfg["audio"]
    if a["source"] == "head":
        return HeadAudio(a["ws_host"], a["ws_port"], a["volume"])
    if a["source"] == "local":
        return LocalAudio(a["volume"])
    return None


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--say")
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load(args.config)
    if cfg["audio"]["source"] == "text":
        cfg["audio"]["source"] = "local"
    io = make_io(cfg)
    if args.say:
        if cfg["audio"]["source"] == "head":
            print("Waiting for the head board...")
            while not io.connected:
                time.sleep(0.2)
        io.play(Speaker(cfg["audio"]["piper_voice"])(args.say))
    if args.listen:
        ears = Ears(io, cfg, on_text=lambda t: print(f"heard: {t!r}"),
                    on_wake=lambda: print("(wake word)"))
        print(f"Say '{cfg['audio']['wake_word'].replace('_', ' ')}', then talk. Ctrl-C to quit.")
        ears.run()


if __name__ == "__main__":
    main()
