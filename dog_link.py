"""
dog_link.py - PC side of the link to MechDog (talks to dog/main.py).

Two transports, same protocol:
  --port /dev/tty.usbserial-XXXX   USB-to-TTL adapter wired to the dog's serial port (now)
  --tcp 192.168.1.60:2323          Wi-Fi serial bridge on the head's ESP32-S3 board (later)

Interactive use:
    python dog_link.py --port /dev/tty.usbserial-1410
    dog> walk 50 0        # forward, keeps walking until 'stop' (heartbeat keeps it alive)
    dog> walk 40 20       # forward + turn left
    dog> stop
    dog> act sit_dowm
    dog> status
    dog> led 255 0 0
    dog> quit

As a library (this is what the AI brain will call):
    link = DogLink.serial("/dev/tty.usbserial-1410")
    link.walk(50, 0); time.sleep(2); link.stop()
"""
import argparse
import cmd
import socket
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    serial = None

ACTIONS = (
    "left_foot_kick", "right_foot_kick", "stand_four_legs", "sit_dowm", "go_prone",
    "stand_two_legs", "handshake", "scrape_a_bow", "nodding_motion", "boxing",
    "stretch_oneself", "pee", "press_up", "rotation_pitch", "rotation_roll",
)
HEARTBEAT_S = 0.3   # well under the dog's 1 s watchdog


class _SerialTransport:
    def __init__(self, port: str, baud: int = 9600):
        if serial is None:
            raise RuntimeError("pip install pyserial")
        self.s = serial.Serial(port, baud, timeout=0.1)

    def write(self, data: bytes) -> None:
        self.s.write(data)

    def read(self) -> bytes:
        return self.s.read(256)

    def close(self) -> None:
        self.s.close()


class _TcpTransport:
    def __init__(self, host: str, port: int):
        self.s = socket.create_connection((host, port), timeout=3)
        self.s.settimeout(0.1)

    def write(self, data: bytes) -> None:
        self.s.sendall(data)

    def read(self) -> bytes:
        try:
            return self.s.recv(256)
        except socket.timeout:
            return b""

    def close(self) -> None:
        self.s.close()


class DogLink:
    def __init__(self, transport):
        self.t = transport
        self.lock = threading.Lock()
        self.status = {}          # latest CMD|5 reply
        self.events = []          # CMD|9 events (blocked, watchdog, done, error)
        self.on_event = None      # optional callback(event, detail)
        self.walking = False
        self._buf = ""
        self._alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    @classmethod
    def serial(cls, port: str, baud: int = 9600) -> "DogLink":
        return cls(_SerialTransport(port, baud))

    @classmethod
    def tcp(cls, host: str, port: int) -> "DogLink":
        return cls(_TcpTransport(host, port))

    # ---- commands -------------------------------------------------------
    def send(self, *fields) -> None:
        msg = "CMD|" + "|".join(str(f) for f in fields) + "|$"
        with self.lock:
            self.t.write(msg.encode())

    def walk(self, stride: int, turn: int = 0) -> None:
        """stride mm (-60..80, + forward), turn deg (-30..30, + left). Runs until stop()."""
        self.walking = not (stride == 0 and turn == 0)
        self.send(1, int(stride), int(turn))

    def stop(self) -> None:
        self.walking = False
        self.send(0)

    def action(self, name: str) -> None:
        if name not in ACTIONS:
            raise ValueError(f"unknown action {name!r}; choose from {ACTIONS}")
        self.walking = False
        self.send(2, name)

    def pose(self, roll: int = 0, pitch: int = 0, height: int = 0) -> None:
        self.send(3, int(roll), int(pitch), int(height))

    def default_pose(self) -> None:
        self.walking = False
        self.send(4)

    def led(self, r: int, g: int, b: int) -> None:
        self.send(6, r, g, b)

    def request_status(self) -> None:
        self.send(5)

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self._alive = False
            self.t.close()

    # ---- background threads ---------------------------------------------
    def _heartbeat(self) -> None:
        while self._alive:
            try:
                self.send(5)      # status poll doubles as the watchdog heartbeat
            except Exception:
                pass
            time.sleep(HEARTBEAT_S)

    def _reader(self) -> None:
        while self._alive:
            try:
                chunk = self.t.read()
            except Exception:
                time.sleep(0.2)
                continue
            if not chunk:
                continue
            self._buf += chunk.decode(errors="ignore")
            while "$" in self._buf:
                msg, self._buf = self._buf.split("$", 1)
                start = msg.find("CMD|")
                if start != -1:
                    self._handle(msg[start + 4:].strip("|").split("|"))

    def _handle(self, f) -> None:
        if not f or not f[0]:
            return
        if f[0] == "5" and len(f) >= 6:
            self.status = {
                "distance_mm": int(f[1]), "roll": float(f[2]), "pitch": float(f[3]),
                "battery": f[4], "state": f[5], "time": time.time(),
            }
        elif f[0] == "9":
            event, detail = f[1], (f[2] if len(f) > 2 else "")
            if event in ("blocked", "watchdog"):
                self.walking = False
            self.events.append((time.time(), event, detail))
            if self.on_event:
                self.on_event(event, detail)


class Shell(cmd.Cmd):
    intro = "MechDog link. Commands: walk S T, stop, act NAME, actions, pose R P H, home, led R G B, status, quit"
    prompt = "dog> "

    def __init__(self, link: DogLink):
        super().__init__()
        self.link = link
        link.on_event = lambda e, d: print(f"\n[event] {e} {d}\n{self.prompt}", end="")

    def do_walk(self, arg):
        s, t = (arg.split() + ["0", "0"])[:2]
        self.link.walk(int(s), int(t))

    def do_stop(self, _):
        self.link.stop()

    def do_act(self, arg):
        try:
            self.link.action(arg.strip())
        except ValueError as e:
            print(e)

    def do_actions(self, _):
        print("\n".join(ACTIONS))

    def do_pose(self, arg):
        r, p, h = (arg.split() + ["0", "0", "0"])[:3]
        self.link.pose(int(r), int(p), int(h))

    def do_home(self, _):
        self.link.default_pose()

    def do_led(self, arg):
        self.link.led(*[int(x) for x in arg.split()[:3]])

    def do_status(self, _):
        print(self.link.status or "(no status yet - is the dog running dog/main.py?)")

    def do_quit(self, _):
        self.link.close()
        return True

    do_EOF = do_quit


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--port", help="serial port, e.g. /dev/tty.usbserial-1410 or COM5")
    g.add_argument("--tcp", help="host:port of a Wi-Fi serial bridge")
    args = ap.parse_args()

    if args.port:
        link = DogLink.serial(args.port)
    else:
        host, port = args.tcp.rsplit(":", 1)
        link = DogLink.tcp(host, int(port))

    try:
        Shell(link).cmdloop()
    except KeyboardInterrupt:
        link.close()


if __name__ == "__main__":
    main()
