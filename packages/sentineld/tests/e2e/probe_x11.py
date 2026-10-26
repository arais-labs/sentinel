# The guest half of the pointer checks (e2e.py), run as root by the agent with python3 -c: a
# window over the whole screen, on the desktop's X server (Xorg, or the compositor's Xwayland), that
# reports where the desktop delivers the pointer: one JSON line per event on stdout.
#   {"ready": true, "width", "height"}         the window is mapped
#   {"event": "motion"|"press"|"release", "x", "y", "button"?}   root coordinates
# Only libX11 through ctypes: nothing to install. argv[1]: seconds to run; argv[2] "managed": a
# full-screen window the window manager places (for a compositor that does not show the default, an
# override-redirect window above everything).
import ctypes
import ctypes.util
import glob
import json
import os
import select
import sys
import time


def say(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


# The desktop session's user and environment (desktop/session.sh publish).
session = json.load(open("/run/sentinel-desktop/session.json"))
user = session["user"]
env = dict(session["environment"])
# An Xwayland server with access control: its cookie is in the session processes' environment.
for proc in glob.glob("/proc/[0-9]*"):
    try:
        if os.stat(proc).st_uid != user["uid"]:
            continue
        with open(proc + "/environ", "rb") as f:
            found = [
                kv[11:].decode() for kv in f.read().split(b"\0") if kv.startswith(b"XAUTHORITY=")
            ]
        if found:
            env["XAUTHORITY"] = found[0]
            break
    except OSError:
        pass
os.environ.update(env)
os.environ["HOME"] = user["home"]
os.setgroups(user["groups"])
os.setgid(user["gid"])
os.setuid(user["uid"])

name = ctypes.util.find_library("X11") or "libX11.so.6"
try:
    X = ctypes.CDLL(name)
except OSError as e:
    say({"error": "no libX11: %s" % e})
    sys.exit(3)


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("background_pixmap", ctypes.c_ulong),
        ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong),
        ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong),
    ]


class XButtonEvent(ctypes.Structure):  # also the layout of XMotionEvent up to `state`
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("button", ctypes.c_uint),
    ]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("button", XButtonEvent), ("pad", ctypes.c_long * 24)]


X.XOpenDisplay.restype = ctypes.c_void_p
X.XOpenDisplay.argtypes = [ctypes.c_char_p]
for fn in ("XDefaultScreen", "XConnectionNumber", "XPending", "XFlush"):
    getattr(X, fn).argtypes = [ctypes.c_void_p]
X.XRootWindow.restype = ctypes.c_ulong
X.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
X.XDisplayWidth.argtypes = X.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
X.XCreateWindow.restype = ctypes.c_ulong
X.XCreateWindow.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(XSetWindowAttributes),
]
X.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
X.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(XEvent)]

display = X.XOpenDisplay(None)
if not display:
    say({"error": "cannot open display %s" % os.environ.get("DISPLAY")})
    sys.exit(3)
screen = X.XDefaultScreen(display)
width, height = X.XDisplayWidth(display, screen), X.XDisplayHeight(display, screen)
attrs = XSetWindowAttributes()
attrs.background_pixel = 0xFF00FF  # magenta: the host sees the window in screenshots
managed = len(sys.argv) > 2 and sys.argv[2] == "managed"
attrs.override_redirect = 0 if managed else 1
attrs.event_mask = (
    (1 << 2) | (1 << 3) | (1 << 6) | (1 << 15) | (1 << 17)
)  # buttons, motion, expose, structure
CW_BACK_PIXEL, CW_OVERRIDE_REDIRECT, CW_EVENT_MASK = 1 << 1, 1 << 9, 1 << 11
window = X.XCreateWindow(
    display,
    X.XRootWindow(display, screen),
    0,
    0,
    width,
    height,
    0,
    0,
    1,
    None,
    CW_BACK_PIXEL | CW_OVERRIDE_REDIRECT | CW_EVENT_MASK,
    ctypes.byref(attrs),
)
if managed:
    X.XInternAtom.restype = ctypes.c_ulong
    X.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    X.XChangeProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    fullscreen = (ctypes.c_ulong * 1)(X.XInternAtom(display, b"_NET_WM_STATE_FULLSCREEN", 0))
    X.XChangeProperty(
        display, window, X.XInternAtom(display, b"_NET_WM_STATE", 0), 4, 32, 0, fullscreen, 1
    )
X.XMapRaised(display, window)
X.XFlush(display)

deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 60)
fd = X.XConnectionNumber(display)
event = XEvent()
kinds = {4: "press", 5: "release", 6: "motion"}
while time.time() < deadline:
    if not X.XPending(display):
        select.select([fd], [], [], 0.2)
        continue
    X.XNextEvent(display, ctypes.byref(event))
    if event.type == 19:  # MapNotify
        say({"ready": True, "width": width, "height": height})
    elif event.type in kinds:
        b = event.button
        out = {"event": kinds[event.type], "x": b.x_root, "y": b.y_root}
        if event.type != 6:
            out["button"] = b.button
        say(out)
