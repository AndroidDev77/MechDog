# dog/main.py - runs ON the MechDog (MicroPython, Hiwonder firmware).
#
# Turns the dog into a "body" that the PC brain drives over the serial port.
# Upload with Hiwonder's Python editor as main.py so it starts on boot.
#
# Serial: 9600 baud, 8N1 (Hiwonder default). Wire TX->RX and RX->TX.
#
# Protocol (PC -> dog), every message is  CMD|<code>|<args...>|$
#   CMD|0|$                       stop walking
#   CMD|1|<stride>|<turn>|$       walk. stride mm (-60..80, + = forward), turn deg (-30..30, + = left)
#                                 must be refreshed: the dog stops if it hears nothing for 1 s
#   CMD|2|<action_name>|$         run a built-in action group (see ACTIONS)
#   CMD|3|<roll>|<pitch>|<height>|$  body pose, degrees / mm (each clamped to +-15)
#   CMD|4|$                       back to default standing pose
#   CMD|5|$                       status request (also counts as a heartbeat)
#   CMD|6|<r>|<g>|<b>|$           ultrasonic "eye" LED colour, 0-255
#   CMD|8|$                       ping / heartbeat
#
# Replies (dog -> PC):
#   CMD|5|<dist_mm>|<roll>|<pitch>|<battery>|<state>|$   state: idle / walking / action / blocked
#   CMD|8|ok|$                    ping reply
#   CMD|9|<event>|<detail>|$      events: blocked, watchdog, done, error

import Hiwonder
import time
import Hiwonder_IIC
from HW_MechDog import MechDog

WATCHDOG_MS = 1000      # stop walking if the PC goes quiet this long
OBSTACLE_CM = 15        # refuse to walk forward closer than this

ACTIONS = (
    "left_foot_kick", "right_foot_kick", "stand_four_legs", "sit_dowm",  # (sic) Hiwonder's name
    "go_prone", "stand_two_legs", "handshake", "scrape_a_bow", "nodding_motion",
    "boxing", "stretch_oneself", "pee", "press_up", "rotation_pitch", "rotation_roll",
)

uart = Hiwonder.UART(9600)
mechdog = MechDog()
i2c1 = Hiwonder_IIC.IIC(1)
sonar = Hiwonder_IIC.I2CSonar(i2c1)
imu = Hiwonder_IIC.MPU(i2c1)

state = "idle"          # idle / walking / action / blocked
stride = 0
turn = 0
last_heard = time.ticks_ms()
pending_action = None
distance_cm = 999

time.sleep(1)
mechdog.set_default_pose()
sonar.setRGB(0, 0x00, 0x40, 0xff)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def send(*fields):
    uart.send_data("CMD|" + "|".join(str(f) for f in fields) + "|$")


def halt(reason=None):
    global state, stride, turn
    mechdog.move(0, 0)
    stride = 0
    turn = 0
    if state == "walking" or state == "blocked":
        state = "idle"
    if reason:
        send(9, reason, 0)


def handle(f):
    global state, stride, turn, pending_action
    code = int(f[0])

    if code == 0:
        halt()
    elif code == 1:
        if state == "action":
            send(9, "error", "busy")
            return
        s = clamp(int(f[1]), -60, 80)
        t = clamp(int(f[2]), -30, 30)
        if s > 0 and distance_cm < OBSTACLE_CM:
            mechdog.move(0, 0)
            state = "blocked"
            send(9, "blocked", int(distance_cm * 10))
            return
        if s == 0 and t == 0:
            halt()
            return
        stride, turn = s, t
        mechdog.move(s, t)
        state = "walking"
    elif code == 2:
        name = f[1]
        if name not in ACTIONS:
            send(9, "error", "unknown_action")
            return
        halt()
        pending_action = name
        state = "action"
    elif code == 3:
        r = clamp(int(f[1]), -15, 15)
        p = clamp(int(f[2]), -15, 15)
        h = clamp(int(f[3]), -15, 15)
        mechdog.set_pose([0, 0, h], [r, p, 0], 300)
    elif code == 4:
        halt()
        mechdog.set_default_pose()
    elif code == 5:
        a = imu.read_angle()
        send(5, int(distance_cm * 10), round(a[0], 1), round(a[1], 1),
             Hiwonder.Battery_power(), state)
    elif code == 6:
        sonar.setRGB(0, clamp(int(f[1]), 0, 255), clamp(int(f[2]), 0, 255),
                     clamp(int(f[3]), 0, 255))
    elif code == 8:
        send(8, "ok")
    else:
        send(9, "error", "unknown_code")


def rx_loop():
    """Read and dispatch commands from the PC."""
    global last_heard
    while True:
        if uart.contains_data("CMD"):
            data = uart.read_uart_cmd()
            if not data:
                continue
            last_heard = time.ticks_ms()
            try:
                handle(uart.parse_uart_cmd(data))
            except Exception as e:
                send(9, "error", "bad_cmd")
        else:
            time.sleep(0.01)


def safety_loop():
    """Watchdog + obstacle stop + action runner."""
    global state, pending_action
    while True:
        if state == "walking":
            if time.ticks_diff(time.ticks_ms(), last_heard) > WATCHDOG_MS:
                halt("watchdog")
            elif stride > 0 and distance_cm < OBSTACLE_CM:
                mechdog.move(0, 0)
                state = "blocked"
                sonar.setRGB(0, 0xff, 0x00, 0x00)
                send(9, "blocked", int(distance_cm * 10))
        if pending_action:
            name = pending_action
            pending_action = None
            mechdog.action_run(name)
            time.sleep(2)
            state = "idle"
            send(9, "done", name)
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
