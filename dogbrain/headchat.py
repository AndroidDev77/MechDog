"""
dogbrain/headchat.py - a chat window for the head audio board, no AI involved.

    python -m dogbrain.headchat            # head board (it connects to ws://<this Mac>:8765/head)
    python -m dogbrain.headchat --local    # this computer's mic and speaker instead
    python -m dogbrain.headchat --wake     # only transcribe what follows the wake word

Type a message and press Enter: Piper speaks it through the dog's speaker. Anything the dog's
mic hears is transcribed with Whisper and shows up in the chat. The mic is muted while the dog
is talking, so it doesn't transcribe itself.

Don't run it at the same time as `python -m dogbrain`: both listen on port 8765.
Needs Tk: on Homebrew Python, `brew install python-tk@3.13`.
"""
import argparse
import queue
import threading
import time

from .audio import Ears, HeadAudio, LocalAudio, Speaker, Transcriber
from .config import load

LISTEN_MODES = ("everything", "wake word", "off")


class ChatCore:
    """Everything but the window: speaking, listening and status, reported through `events`.

    Events are (kind, text) tuples: "said" / "heard" (chat lines), "info", "error",
    "head" ("connected" / "waiting"), "mic" ("listening" / "hearing speech" /
    "transcribing" / "off" / "loading"), "voice" ("ready").
    """

    def __init__(self, cfg: dict, io, speaker=None, stt=None, vad=None, wake=None,
                 listen: str = "everything"):
        self.cfg, self.io = cfg, io
        self.events: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.outbox: "queue.Queue[str]" = queue.Queue()
        self.speaker = speaker               # loaded in start() unless given (tests)
        self._stt, self._vad, self._wake = stt, vad, wake
        self.listen = listen
        self.ears = None
        self._alive = False

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        self._alive = True
        threading.Thread(target=self._load_voice, daemon=True).start()
        threading.Thread(target=self._load_ears, daemon=True).start()
        threading.Thread(target=self._speak_loop, daemon=True).start()
        threading.Thread(target=self._watch_head, daemon=True).start()

    def close(self) -> None:
        self._alive = False
        if self.ears:
            self.ears._alive = False
        self.stop_speaking()

    def _load_voice(self) -> None:
        if self.speaker is None:
            voice = self.cfg["audio"]["piper_voice"]
            self.events.put(("info", f"loading voice {voice}..."))
            try:
                self.speaker = Speaker(voice)
            except Exception as e:
                self.events.put(("error", f"voice failed to load: {e}"))
                return
        self.events.put(("voice", "ready"))

    def _load_ears(self) -> None:
        self.events.put(("mic", "loading"))
        try:
            stt = self._stt or Transcriber(self.cfg["audio"]["stt_model"])
            self.ears = Ears(self.io, self.cfg, on_text=self._heard, on_wake=self._woke,
                             wake=self._wake, vad=self._vad, stt=stt,
                             always=self.listen != "wake word", on_status=self._mic_status)
        except Exception as e:
            self.events.put(("error", f"speech recognition failed to load: {e}"))
            return
        self.ears.paused = self.listen == "off"
        self.ears.start()
        self._mic_status("idle")

    def _watch_head(self) -> None:
        was = None
        while self._alive:
            now = bool(getattr(self.io, "connected", True))
            if now != was:
                self.events.put(("head", "connected" if now else "waiting"))
                was = now
            time.sleep(0.3)

    # ---- speaking -------------------------------------------------------
    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self.outbox.put(text)

    def stop_speaking(self) -> None:
        while not self.outbox.empty():
            try:
                self.outbox.get_nowait()
            except queue.Empty:
                break
        self.io.stop()

    def _speak_loop(self) -> None:
        while self._alive:
            try:
                text = self.outbox.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.speaker is None:
                self.events.put(("error", "voice still loading; try again in a moment"))
                continue
            if not getattr(self.io, "connected", True):
                self.events.put(("error", "head board isn't connected; message not spoken"))
                continue
            self.events.put(("said", text))
            try:
                self.io.play(self.speaker(text))
            except Exception as e:
                self.events.put(("error", f"couldn't speak: {e}"))

    def set_volume(self, v: float) -> None:
        self.io.set_volume(v)

    # ---- listening ------------------------------------------------------
    def set_listen(self, mode: str) -> None:
        """"everything", "wake word" or "off"."""
        self.listen = mode
        if self.ears is None:
            return
        self.ears.paused = mode == "off"
        if mode == "off":
            self._mic_status("idle")
            return

        def switch():
            if mode == "wake word" and self.ears.wake is None:
                self.events.put(("mic", "loading"))
            try:
                self.ears.set_always(mode == "everything")
            except Exception as e:
                self.events.put(("error", f"wake word failed to load: {e}"))
                return
            self._mic_status("idle")

        threading.Thread(target=switch, daemon=True).start()

    def _heard(self, text: str) -> None:
        self.events.put(("heard", text))

    def _woke(self) -> None:
        self.events.put(("info", "(wake word)"))

    def _mic_status(self, state: str) -> None:
        if self.listen == "off":
            label = "off"
        else:
            label = {"speech": "hearing speech", "transcribing": "transcribing"}.get(
                state, "listening" if self.listen == "everything" else "waiting for wake word")
        self.events.put(("mic", label))


class HeadChat:
    """The Tk window around a ChatCore."""

    def __init__(self, root, core: ChatCore, where: str):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.root, self.core = tk, root, core
        root.title("MechDog head chat")
        root.geometry("560x520")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        self.head_var = tk.StringVar(value=f"Head: waiting ({where})")
        self.mic_var = tk.StringVar(value="Mic: loading")
        ttk.Label(top, textvariable=self.head_var).pack(side="left")
        ttk.Label(top, textvariable=self.mic_var).pack(side="right")

        box = ttk.Frame(root)
        box.grid(row=1, column=0, sticky="nsew", padx=8)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        from tkinter import font
        self.log = tk.Text(box, wrap="word", state="disabled", height=20, padx=6, pady=6,
                           font=font.nametofont("TkDefaultFont"))
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(box, command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = bar.set
        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self.log.tag_configure("said_who", foreground="#185FA5", font=bold)
        self.log.tag_configure("heard_who", foreground="#0F6E56", font=bold)
        self.log.tag_configure("info", foreground="#888780")
        self.log.tag_configure("error", foreground="#A32D2D")

        send = ttk.Frame(root)
        send.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        send.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(send)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda e: self.send())
        ttk.Button(send, text="Speak", command=self.send).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(send, text="Stop", command=core.stop_speaking).grid(row=0, column=2, padx=(6, 0))

        opts = ttk.Frame(root)
        opts.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 8))
        ttk.Label(opts, text="Transcribe:").pack(side="left")
        self.mode = tk.StringVar(value=core.listen)
        for m in LISTEN_MODES:
            ttk.Radiobutton(opts, text=m, value=m, variable=self.mode,
                            command=lambda: core.set_listen(self.mode.get())).pack(side="left", padx=2)
        self.vol = tk.DoubleVar(value=round(core.io.volume * 100))
        ttk.Scale(opts, from_=0, to=100, variable=self.vol, length=120,
                  command=lambda v: core.set_volume(float(v) / 100)).pack(side="right")
        ttk.Label(opts, text="Volume").pack(side="right", padx=(0, 4))

        self.entry.focus_set()
        core.start()
        root.after(100, self._tick)

    def send(self) -> None:
        text = self.entry.get()
        if text.strip():
            self.core.say(text)
            self.entry.delete(0, "end")

    def add(self, who: str, text: str, tag: str) -> None:
        self.log["state"] = "normal"
        if who:
            self.log.insert("end", f"{who}  ", f"{tag}_who")
            self.log.insert("end", text + "\n")
        else:
            self.log.insert("end", text + "\n", tag)
        self.log["state"] = "disabled"
        self.log.see("end")

    def _tick(self) -> None:
        while True:
            try:
                kind, text = self.core.events.get_nowait()
            except queue.Empty:
                break
            if kind == "said":
                self.add("You:", text, "said")
            elif kind == "heard":
                self.add("Heard:", text, "heard")
            elif kind == "head":
                self.head_var.set(f"Head: {text}")
                self.add("", f"head board {text}", "info")
            elif kind == "mic":
                self.mic_var.set(f"Mic: {text}")
            elif kind == "voice":
                self.add("", "voice ready - type a message and press Enter", "info")
            else:
                self.add("", text, kind)
        self.root.after(100, self._tick)

    def close(self) -> None:
        self.core.close()
        self.root.destroy()


def main() -> None:
    ap = argparse.ArgumentParser(description="Type to speak through the dog; see what it hears.")
    ap.add_argument("--local", action="store_true", help="use this computer's mic and speaker")
    ap.add_argument("--wake", action="store_true", help="only transcribe after the wake word")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load(args.config)
    a = cfg["audio"]
    if args.local:
        io, where = LocalAudio(a["volume"]), "this computer"
    else:
        io, where = HeadAudio(a["ws_host"], a["ws_port"], a["volume"]), f"port {a['ws_port']}"

    import tkinter as tk
    root = tk.Tk()
    HeadChat(root, ChatCore(cfg, io, listen="wake word" if args.wake else "everything"), where)
    root.mainloop()


if __name__ == "__main__":
    main()
