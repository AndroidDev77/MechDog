"""
dog_link.py - PC side of the link to MechDog (talks to dog/main.py).

Three transports, same protocol:
  --ble                            Bluetooth LE, finds "MechDog_XX" automatically (no cable)
  --ble <address>                  Bluetooth LE to a specific dog
  --port /dev/tty.usbserial-XXXX   USB-to-TTL adapter wired to the dog's serial port
  --tcp 192.168.1.60:2323          Wi-Fi serial bridge on the head's ESP32-S3 board (later)

Interactive use:
    python dog_link.py --ble
    dog> walk 50 0        # forward, keeps walking until 'stop' (heartbeat keeps it alive)
    dog> walk 40 20       # forward + turn left
    dog> stop
    dog> act sit_dowm
    dog> status
    dog> led 255 0 0
    dog> quit

As a library (this is what the AI brain will call):
    link = DogLink.ble()          # or DogLink.serial("/dev/tty.usbserial-1410")
    link.walk(50, 0); time.sleep(2); link.stop()
"""
import argparse
import asyncio
import cmd
import queue
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


class _BleTransport:
    """Bluetooth LE via bleak, run on its own asyncio loop in a background thread."""

    NAME_PREFIX = "MechDog_"

    def __init__(self, address: str = None, timeout: float = 10.0):
        try:
            import bleak  # noqa: F401
        except ImportError:
            raise RuntimeError("pip install bleak")
        self.rx = queue.Queue()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self._run(self._connect(address, timeout), timeout + 15)

    def _run(self, coro, timeout=10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _connect(self, address, timeout):
        from bleak import BleakClient, BleakScanner
        if address is None:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, adv: (d.name or adv.local_name or "").startswith(self.NAME_PREFIX),
                timeout=timeout)
            if dev is None:
                raise RuntimeError("No MechDog_XX found - is it on, running dog/main.py, "
                                   "and is the phone app disconnected?")
            address = dev.address
            print(f"Found {dev.name} at {address}")
        self.client = BleakClient(address)
        await self.client.connect()

        self.write_char = None
        for service in self.client.services:
            for char in service.characteristics:
                if service.uuid.startswith(("00001800", "00001801", "0000180a")):
                    continue  # standard GAP / GATT / device-info services
                if "notify" in char.properties:
                    await self.client.start_notify(char, lambda _c, d: self.rx.put(bytes(d)))
                if self.write_char is None and (
                        "write-without-response" in char.properties or "write" in char.properties):
                    self.write_char = char
        if self.write_char is None:
            raise RuntimeError("Connected, but found no writable characteristic. "
                               "Run ble_probe.py probe <address> and send me the output.")
        self.with_response = "write-without-response" not in self.write_char.properties
        self.chunk = max(20, getattr(self.client, "mtu_size", 23) - 3)

    async def _write(self, data: bytes):
        for i in range(0, len(data), self.chunk):
            await self.client.write_gatt_char(self.write_char, data[i:i + self.chunk],
                                              response=self.with_response)

    def write(self, data: bytes) -> None:
        self._run(self._write(data))

    def read(self) -> bytes:
        try:
            return self.rx.get(timeout=0.1)
        except queue.Empty:
            return b""

    def close(self) -> None:
        try:
            self._run(self.client.disconnect())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)


class DogLink:
    def __init__(self, transport):
        self.t = transport
        self.lock = threading.Lock()
        self.status = {}          # latest CMD|15 reply
        self.events = []          # CMD|19 events (blocked, watchdog, done, error)
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
    def ble(cls, address: str = None) -> "DogLink":
        return cls(_BleTransport(address))

    @classmethod
    def tcp(cls, host: str, port: int) -> "DogLink":
        return cls(_TcpTransport(host, port))

    # ---- commands -------------------------------------------------------
    def send(self, *fields) -> None:
        msg = "CMD|" + "|".join(str(f) for f in fields) + "|$"
        with self.lock:
            self.t.write(msg.encode())

    def walk(self, stride: int, turn: int = 0) -> None:
        """stride mm (-60..120, + forward), turn deg (-40..40, + left). Runs until stop()."""
        self.walking = not (stride == 0 and turn == 0)
        self.send(11, int(stride), int(turn))

    def stop(self) -> None:
        self.walking = False
        self.send(10)

    def action(self, name: str) -> None:
        if name not in ACTIONS:
            raise ValueError(f"unknown action {name!r}; choose from {ACTIONS}")
        self.walking = False
        self.send(12, name)

    def pose(self, roll: int = 0, pitch: int = 0, height: int = 0) -> None:
        self.send(13, int(roll), int(pitch), int(height))

    def default_pose(self) -> None:
        self.walking = False
        self.send(14)

    def led(self, r: int, g: int, b: int) -> None:
        self.send(16, r, g, b)

    def request_status(self) -> None:
        self.send(15)

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
                self.send(15)     # status poll doubles as the watchdog heartbeat
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
        if f[0] == "15" and len(f) >= 6:
            self.status = {
                "distance_mm": int(f[1]), "roll": float(f[2]), "pitch": float(f[3]),
                "battery": f[4], "state": f[5], "time": time.time(),
            }
        elif f[0] == "19":
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
    g.add_argument("--ble", nargs="?", const="auto", metavar="ADDRESS",
                   help="Bluetooth LE; finds MechDog_XX if no address given")
    g.add_argument("--port", help="serial port, e.g. /dev/tty.usbserial-1410 or COM5")
    g.add_argument("--tcp", help="host:port of a Wi-Fi serial bridge")
    args = ap.parse_args()

    if args.ble:
        link = DogLink.ble(None if args.ble == "auto" else args.ble)
    elif args.port:
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
