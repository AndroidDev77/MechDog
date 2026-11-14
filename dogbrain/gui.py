"""
dogbrain/gui.py - point-and-click remote for MechDog (Tkinter, built on dogbrain.link).

    python -m dogbrain.gui              # Bluetooth, finds MechDog_XX
    python -m dogbrain.gui --sim        # pretend dog, no hardware
    python -m dogbrain.gui --port /dev/tty.usbserial-1120

Drive with the arrow pad or the keyboard (arrows / WASD, space or Esc = stop). Walking keeps
going until you stop; the link's status polls keep the dog's 1 s watchdog fed, and closing the
window stops the dog.

Needs Tk: on Homebrew Python, `brew install python-tk@3.13`.
"""
import argparse
import queue
import threading
import time
import tkinter as tk
from tkinter import colorchooser, ttk

from .link import ACTIONS, DogLink

MAX_SPEED = 100      # mm/s, the firmware's boot cap (both directions)
MAX_TURN = 40        # deg/s

KEYS = {   # key -> (forward sign, turn sign)
    "Up": (1, 0), "w": (1, 0), "Down": (-1, 0), "s": (-1, 0),
    "Left": (0, 1), "a": (0, 1), "Right": (0, -1), "d": (0, -1),
}


class DogGui:
    def __init__(self, root: tk.Tk, connect):
        self.root = root
        self.connect = connect        # callable returning a DogLink (may block, e.g. BLE scan)
        self.link = None
        self.motion = (0, 0)          # last walk sent, so held keys don't flood the link
        self.events = queue.Queue()   # filled from the link's reader thread

        root.title("MechDog")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        for key in KEYS:
            root.bind(f"<KeyPress-{key}>", lambda e, k=key: self.drive(*KEYS[k]))
        root.bind("<space>", lambda e: self.stop())
        root.bind("<Escape>", lambda e: self.stop())

        self.set_controls("disabled")
        threading.Thread(target=self._connect, daemon=True).start()
        self.root.after(250, self._tick)

    # ---- layout ---------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}
        self.controls = []

        status = ttk.LabelFrame(self.root, text="Status")
        status.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)
        self.vars = {k: tk.StringVar(value="-") for k in ("link", "state", "dist", "battery", "imu")}
        for i, (key, label) in enumerate((("link", "Link"), ("state", "State"), ("dist", "Distance"),
                                          ("battery", "Battery"), ("imu", "Roll / pitch"))):
            ttk.Label(status, text=label + ":").grid(row=0, column=2 * i, sticky="e", padx=(8, 2))
            ttk.Label(status, textvariable=self.vars[key], width=14).grid(row=0, column=2 * i + 1, sticky="w")
        self.vars["link"].set("connecting...")

        drive = ttk.LabelFrame(self.root, text="Drive")
        drive.grid(row=1, column=0, sticky="nsew", **pad)
        arrows = ((0, 0, "↖", 1, 1), (0, 1, "↑", 1, 0), (0, 2, "↗", 1, -1),
                  (1, 0, "↺", 0, 1), (1, 2, "↻", 0, -1),
                  (2, 0, "↙", -1, 1), (2, 1, "↓", -1, 0), (2, 2, "↘", -1, -1))
        for r, c, text, fwd, turn in arrows:
            b = ttk.Button(drive, text=text, width=4, command=lambda f=fwd, t=turn: self.drive(f, t))
            b.grid(row=r, column=c, ipady=8, padx=2, pady=2)
            self.controls.append(b)
        tk.Button(drive, text="STOP", bg="#d33", fg="white", activebackground="#a00", width=5,
                  command=self.stop).grid(row=1, column=1, ipady=8, padx=2, pady=2)
        self.speed = tk.IntVar(value=MAX_SPEED)
        self.turn = tk.IntVar(value=30)
        for i, (label, var, top) in enumerate((("Speed mm/s", self.speed, MAX_SPEED),
                                               ("Turn deg/s", self.turn, MAX_TURN))):
            ttk.Label(drive, text=label).grid(row=3 + i, column=0, sticky="e")
            s = tk.Scale(drive, from_=0, to=top, orient="horizontal", variable=var, length=160)
            s.grid(row=3 + i, column=1, columnspan=2, sticky="w")
            self.controls.append(s)
        ttk.Label(drive, text="Keys: arrows / WASD, space = stop", foreground="gray").grid(
            row=5, column=0, columnspan=3, pady=(4, 0))

        pose = ttk.LabelFrame(self.root, text="Body pose")
        pose.grid(row=1, column=1, sticky="nsew", **pad)
        self.roll, self.pitch, self.height = tk.IntVar(value=0), tk.IntVar(value=0), tk.IntVar(value=30)
        for i, (label, var, lo, hi) in enumerate((("Roll deg", self.roll, -15, 15),
                                                  ("Pitch deg", self.pitch, -15, 15),
                                                  ("Height mm", self.height, -10, 30))):
            ttk.Label(pose, text=label).grid(row=i, column=0, sticky="e")
            s = tk.Scale(pose, from_=lo, to=hi, orient="horizontal", variable=var, length=160)
            s.grid(row=i, column=1, sticky="w")
            self.controls.append(s)
        row = ttk.Frame(pose)
        row.grid(row=3, column=0, columnspan=2, pady=4)
        for text, cmd in (("Apply pose", self.apply_pose), ("Home", self.home), ("Eye colour...", self.pick_led)):
            b = ttk.Button(row, text=text, command=cmd)
            b.pack(side="left", padx=2)
            self.controls.append(b)

        tricks = ttk.LabelFrame(self.root, text="Tricks")
        tricks.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)
        for i, name in enumerate(ACTIONS):
            label = name.replace("sit_dowm", "sit_down").replace("_", " ")
            b = ttk.Button(tricks, text=label, command=lambda n=name: self.trick(n))
            b.grid(row=i // 5, column=i % 5, sticky="ew", padx=2, pady=2)
            self.controls.append(b)

        log = ttk.LabelFrame(self.root, text="Events")
        log.grid(row=3, column=0, columnspan=2, sticky="nsew", **pad)
        self.log = tk.Listbox(log, height=6)
        self.log.pack(fill="both", expand=True)
        self.root.columnconfigure((0, 1), weight=1)
        self.root.rowconfigure(3, weight=1)

    def set_controls(self, state: str) -> None:
        for w in self.controls:
            w.configure(state=state)
            if isinstance(w, tk.Scale):   # hand focus back, or arrow keys would nudge the slider
                w.bind("<ButtonRelease-1>", lambda e: self.root.focus_set())

    def note(self, text: str) -> None:
        self.log.insert("end", time.strftime("%H:%M:%S  ") + text)
        self.log.see("end")
        if self.log.size() > 200:
            self.log.delete(0)

    # ---- connection -----------------------------------------------------
    def _connect(self) -> None:
        try:
            link = self.connect()
        except Exception as e:   # BLE off, dog not found, port busy...
            self.events.put(("link_failed", str(e)))
            return
        link.on_event = lambda event, detail: self.events.put((event, detail))
        self.link = link
        self.events.put(("link_up", ""))

    # ---- commands -------------------------------------------------------
    def drive(self, fwd: int, turn: int) -> None:
        if not self.link:
            return
        motion = (fwd * self.speed.get(), turn * self.turn.get())
        if motion == self.motion:
            return
        self.motion = motion
        self.link.walk(*motion)
        self.note(f"walk {motion[0]} mm/s, turn {motion[1]} deg/s")

    def stop(self) -> None:
        self.motion = (0, 0)
        if self.link:
            self.link.stop()
            self.note("stop")

    def trick(self, name: str) -> None:
        self.motion = (0, 0)
        self.link.action(name)
        self.note(f"trick {name}")

    def apply_pose(self) -> None:
        self.link.pose(self.roll.get(), self.pitch.get(), self.height.get())
        self.note(f"pose roll {self.roll.get()} pitch {self.pitch.get()} height {self.height.get()}")

    def home(self) -> None:
        self.motion = (0, 0)
        self.link.default_pose()
        self.note("home")

    def pick_led(self) -> None:
        rgb, _ = colorchooser.askcolor(title="Eye colour")
        if rgb:
            self.link.led(*(int(v) for v in rgb))
            self.note("eyes rgb(%d, %d, %d)" % tuple(int(v) for v in rgb))

    # ---- periodic UI update (Tk calls must stay on this thread) ---------
    def _tick(self) -> None:
        while not self.events.empty():
            event, detail = self.events.get()
            if event == "link_up":
                self.vars["link"].set("connected")
                self.set_controls("normal")
                self.note("connected")
            elif event == "link_failed":
                self.vars["link"].set("failed")
                self.note(f"connection failed: {detail}")
            else:
                if event in ("blocked", "watchdog"):
                    self.motion = (0, 0)   # the dog stopped itself; let the next press resend
                self.note(f"dog: {event} {detail}".rstrip())
        st = self.link.status if self.link else {}
        if st:
            age = time.time() - st["time"]
            self.vars["state"].set(st["state"] if age < 2 else f"{st['state']} (stale)")
            self.vars["dist"].set(f"{st['distance_mm']} mm" if st["distance_mm"] < 5000 else "clear")
            self.vars["battery"].set(f"{int(st['battery']) / 1000:.2f} V")
            self.vars["imu"].set(f"{st['roll']:.1f} / {st['pitch']:.1f}")
        self.root.after(250, self._tick)

    def close(self) -> None:
        if self.link:
            try:
                self.link.close()   # sends stop first
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--ble", nargs="?", const="auto", metavar="ADDRESS",
                   help="Bluetooth LE (default); finds MechDog_XX if no address given")
    g.add_argument("--sim", action="store_true", help="pretend dog, no hardware")
    g.add_argument("--port", help="serial port, e.g. /dev/tty.usbserial-1120")
    g.add_argument("--tcp", help="host:port of a Wi-Fi serial bridge")
    args = ap.parse_args()

    if args.sim:
        connect = DogLink.sim
    elif args.port:
        connect = lambda: DogLink.serial(args.port)
    elif args.tcp:
        host, port = args.tcp.rsplit(":", 1)
        connect = lambda: DogLink.tcp(host, int(port))
    else:
        connect = lambda: DogLink.ble(None if args.ble in (None, "auto") else args.ble)

    root = tk.Tk()
    DogGui(root, connect)
    root.mainloop()


if __name__ == "__main__":
    main()
