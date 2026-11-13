# dog/main.py - runs ON the MechDog (MicroPython, Hiwonder firmware).
#
# Turns the dog into a "body" the PC brain drives. Listens on BOTH:
#   - Bluetooth LE (advertises as MechDog_XX, same as the stock firmware)
#   - the serial port (9600 8N1, USB-to-TTL adapter or a Wi-Fi bridge)
# Upload with Hiwonder's Python editor as main.py so it starts on boot.
#
# Every message is  CMD|<code>|<args...>|$  and replies go back on the channel
# the command came in on.
#
# PC brain commands (codes 10+, see dog_link.py):
#   CMD|10|$                         stop walking
#   CMD|11|<stride>|<turn>|$         walk. stride mm (-60..120, + forward), turn deg (-40..40, + left)
#                                    must be refreshed: stops if nothing is heard for 1 s
#   CMD|12|<action_name>|$           run a built-in action group (see ACTIONS)
#   CMD|13|<roll>|<pitch>|<height>|$ body pose (roll/pitch +-17 deg, height -25..15 mm)
#   CMD|14|$                         default standing pose
#   CMD|15|$                         status request (also a heartbeat)
#                                    reply CMD|15|<dist_mm>|<roll>|<pitch>|<battery>|<state>|$
#   CMD|16|<r>|<g>|<b>|$             eye LED colour
#   CMD|18|$                         ping, reply CMD|18|ok|$
#   events: CMD|19|<blocked|watchdog|done|error>|<detail>|$
#
# Hiwonder phone-app commands still work (codes 1-6), so the stock app can
# drive the dog too: 1|5 reset pose, 2|type|num actions, 3|dir walking,
# 4|1 distance, 4|3|r|g|b LED, 6 battery. Balance / obstacle-avoid modes are
# not included.

import Hiwonder
import time
import machine
import Hiwonder_IIC
from HW_MechDog import MechDog
from Hiwonder_BLE import BLE

WATCHDOG_MS = 1000      # PC walk mode stops if the PC goes quiet this long
OBSTACLE_CM = 15        # never walk forward closer than this

ACTIONS = (
    "left_foot_kick", "right_foot_kick", "stand_four_legs", "sit_dowm",  # (sic) Hiwonder's name
    "go_prone", "stand_two_legs", "handshake", "scrape_a_bow", "nodding_motion",
    "boxing", "stretch_oneself", "pee", "press_up", "rotation_pitch", "rotation_roll",
)
# Hiwonder app direction codes -> move(stride, turn)
APP_DIRS = {0: (0, 0), 1: (80, -40), 2: (90, -25), 3: (120, 0), 4: (90, 25),
            5: (80, 40), 6: (-40, -20), 7: (-40, 0), 8: (-40, 20)}

mac = machine.unique_id()
ble = BLE(BLE.MODE_BLE_SLAVE, "MechDog_{:02X}".format(mac[5]))
uart = Hiwonder.UART(9600)
mechdog = MechDog()
i2c1 = Hiwonder_IIC.IIC(1)
sonar = Hiwonder_IIC.I2CSonar(i2c1)
imu = Hiwonder_IIC.MPU(i2c1)

state = "idle"          # idle / walking / action / blocked
mode = None             # "pc" (watchdog applies) or "app"
stride = 0
turn = 0
last_heard = time.ticks_ms()
pending_action = None
distance_cm = 999
reply_ch = uart         # channel the last command arrived on

time.sleep(1)
mechdog.set_default_pose()
sonar.setRGB(0, 0x00, 0x40, 0xff)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def send(*fields, ch=None):
    try:
        (ch or reply_ch).send_data("CMD|" + "|".join(str(f) for f in fields) + "|$")
    except Exception:
        pass


def halt(reason=None):
    global state, stride, turn, mode
    mechdog.move(0, 0)
    stride = 0
    turn = 0
    mode = None
    if state in ("walking", "blocked"):
        state = "idle"
    if reason:
        send(19, reason, 0)


def walk(s, t, how):
    global state, stride, turn, mode
    if state == "action":
        send(19, "error", "busy")
        return
    if s == 0 and t == 0:
        halt()
        return
    if s > 0 and distance_cm < OBSTACLE_CM:
        mechdog.move(0, 0)
        state = "blocked"
        send(19, "blocked", int(distance_cm * 10))
        return
    stride, turn, mode = s, t, how
    mechdog.move(s, t)
    state = "walking"


def queue_action(name):
    global state, pending_action
    if state == "action":
        send(19, "error", "busy")
        return
    halt()
    pending_action = name
    state = "action"


def handle(f):
    code = int(f[0])

    # ---- PC brain protocol ----
    if code == 10:
        halt()
    elif code == 11:
        walk(clamp(int(f[1]), -60, 120), clamp(int(f[2]), -40, 40), "pc")
    elif code == 12:
        if f[1] in ACTIONS:
            queue_action(f[1])
        else:
            send(19, "error", "unknown_action")
    elif code == 13:
        r = clamp(int(f[1]), -17, 17)
        p = clamp(int(f[2]), -17, 17)
        h = clamp(int(f[3]), -25, 15)
        mechdog.set_pose([0, 0, h], [r, p, 0], 300)
    elif code == 14:
        halt()
        mechdog.set_default_pose()
    elif code == 15:
        a = imu.read_angle()
        send(15, int(distance_cm * 10), round(a[0], 1), round(a[1], 1),
             Hiwonder.Battery_power(), state)
    elif code == 16:
        sonar.setRGB(0, clamp(int(f[1]), 0, 255), clamp(int(f[2]), 0, 255),
                     clamp(int(f[3]), 0, 255))
    elif code == 18:
        send(18, "ok")

    # ---- Hiwonder phone-app protocol ----
    elif code == 1 and int(f[1]) == 5:
        halt()
        mechdog.set_default_pose()
    elif code == 2:
        num = int(f[2])
        if int(f[1]) == 1 and 1 <= num <= len(ACTIONS):
            queue_action(ACTIONS[num - 1])
    elif code == 3:
        s, t = APP_DIRS.get(int(f[1]), (0, 0))
        walk(s, t, "app")
    elif code == 4 and int(f[1]) == 1:
        send(4, min(int(distance_cm * 10), 5000))
    elif code == 4 and int(f[1]) == 3:
        sonar.setRGB(0, int(f[2]), int(f[3]), int(f[4]))
    elif code == 6:
        send(6, Hiwonder.Battery_power())


def poll(ch):
    """Handle one command from a channel if there is one. Returns True if it did."""
    global last_heard, reply_ch
    if not ch.contains_data("CMD"):
        return False
    data = ch.read_uart_cmd()
    if not data:
        return False
    last_heard = time.ticks_ms()
    reply_ch = ch
    try:
        handle(ch.parse_uart_cmd(data))
    except Exception:
        send(19, "error", "bad_cmd")
    return True


def rx_loop():
    while True:
        busy = poll(uart)
        if ble.is_connected():
            busy = poll(ble) or busy
        if not busy:
            time.sleep(0.01)


def safety_loop():
    """Watchdog + obstacle stop + action runner."""
    global state, pending_action
    was_connected = False
    while True:
        connected = ble.is_connected()
        if was_connected and not connected and state == "walking":
            halt()                       # Bluetooth dropped mid-walk
        was_connected = connected

        if state == "walking":
            if mode == "pc" and time.ticks_diff(time.ticks_ms(), last_heard) > WATCHDOG_MS:
                halt("watchdog")
            elif stride > 0 and distance_cm < OBSTACLE_CM:
                mechdog.move(0, 0)
                state = "blocked"
                sonar.setRGB(0, 0xff, 0x00, 0x00)
                send(19, "blocked", int(distance_cm * 10))
        if pending_action:
            name = pending_action
            pending_action = None
            mechdog.action_run(name)
            time.sleep(2)
            state = "idle"
            send(19, "done", name)
        time.sleep(0.03)


def sonar_loop():
    global distance_cm
    time.sleep(1)
    while True:
        d = sonar.getDistance()
        if d is not None:
            distance_cm = d
        time.sleep(0.08)


Hiwonder.startMain(rx_loop)
Hiwonder.startMain(safety_loop)
Hiwonder.startMain(sonar_loop)
