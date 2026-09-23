"""Observe real guest cursor pixels through production capture in a disposable VM.

No redraw timer: movement, shape changes and hiding must present on an idle
primary plane. A custom asymmetric cursor makes hotspot/color/orientation errors
observable independently of distribution themes and host pointer metadata.
"""

import base64
import io
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading
import time

session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
os.environ.update(session["environment"])
os.environ["GDK_BACKEND"] = sys.argv[1]
sys.path.insert(0, "/opt/sentinel/desktop")
from sentinel_display import Desktop, Clipboard  # noqa: E402 — requires the guest module path

user = session["user"]
fd, metadata = tempfile.mkstemp(dir=session["environment"]["XDG_RUNTIME_DIR"])
with os.fdopen(fd, "w") as stream:
    json.dump(session, stream)
    os.fchown(stream.fileno(), user["uid"], user["gid"])
desktop = Desktop(clipboard=Clipboard(metadata))
drm_state = os.open("/sys/kernel/debug/dri/0/state", os.O_RDONLY)
os.setgroups(user["groups"])
os.setgid(user["gid"])
os.setuid(user["uid"])
import gi  # noqa: E402 — initialize GTK after entering the desktop user's environment

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402 — requires version selection above
from PIL import Image  # noqa: E402 — load imaging libraries as the desktop user

signal.alarm(60)
window = Gtk.Window(title="Sentinel guest cursor oracle")
window.fullscreen()
area = Gtk.DrawingArea()
window.add(area)
background = (40, 60, 80)
area.connect(
    "draw",
    lambda widget, cr: (cr.set_source_rgb(*(c / 255 for c in background)), cr.paint(), False)[-1],
)
errors = []
click_received = threading.Event()
clicks = []
area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)


def clicked(widget, event):
    clicks.append((event.button, round(event.x), round(event.y)))
    click_received.set()


area.connect("button-press-event", clicked)


def gui(callback):
    done = threading.Event()
    result, failures = [], []

    def run():
        try:
            result.append(callback())
        except BaseException as error:
            failures.append(error)
        finally:
            done.set()
        return False

    GLib.idle_add(run)
    assert done.wait(5), "GTK main loop stalled"
    if failures:
        raise failures[0]
    return result[0]


def shape(alternate=False):
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    if alternate:
        colors.reverse()
    rgba = bytes(
        c for y in range(32) for x in range(32) for c in (*colors[(y // 16) * 2 + x // 16], 255)
    )
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(rgba), GdkPixbuf.Colorspace.RGB, True, 8, 32, 32, 128
    )
    return Gdk.Cursor.new_from_pixbuf(Gdk.Display.get_default(), pixbuf, 5, 7), colors


def await_pixels(samples):
    deadline = time.monotonic() + 5
    while True:
        shot = desktop.screenshot()
        image = Image.open(
            io.BytesIO(base64.b64decode(shot["screenshot"].split(",", 1)[1]))
        ).convert("RGB")
        actual = [(point, image.getpixel(point), expected) for point, expected in samples]
        if all(
            max(abs(a - b) for a, b in zip(pixel, expected)) <= 3 for _, pixel, expected in actual
        ):
            return image.size
        assert time.monotonic() < deadline, f"Guest cursor pixels did not arrive: {actual}"
        time.sleep(0.025)


def cursor_samples(x, y, colors):
    return [
        ((x - 5 + dx, y - 7 + dy), color)
        for (dx, dy), color in zip([(8, 8), (24, 8), (8, 24), (24, 24)], colors)
    ]


def exercise():
    try:
        # Wait for fullscreen map without requesting recurring application damage.
        await_pixels([((200, 200), background), ((600, 400), background)])
        desktop.execute({"type": "move", "x": 100, "y": 100})
        desktop.execute({"type": "click", "x": 300, "y": 250})
        assert click_received.wait(3), "Guest application did not receive the pointer click"
        assert clicks == [(1, 300, 250)], f"Incorrect click button or coordinates: {clicks}"
        desktop.execute({"type": "move", "x": 300, "y": 250})
        cursor, colors = gui(shape)
        gui(lambda: area.get_window().set_cursor(cursor))
        size = await_pixels(cursor_samples(300, 250, colors))
        os.lseek(drm_state, 0, os.SEEK_SET)
        state = os.read(drm_state, 65536).decode()
        planes = [
            block
            for block in re.split(r"(?=^plane\[|^crtc\[|^connector\[)", state, flags=re.M)
            if block.startswith("plane[")
        ]
        assert len(planes) == 2 and all(
            re.search(r"^\s+fb=[1-9]\d*", block, re.M) for block in planes
        ), f"Desktop must actively use both primary and cursor planes: {state}"
        desktop.execute({"type": "move", "x": 500, "y": 350})
        await_pixels(cursor_samples(500, 350, colors) + [((303, 251), background)])
        # Shape replacement must present without moving or redrawing the window.
        cursor, colors = gui(lambda: shape(True))
        gui(lambda: area.get_window().set_cursor(cursor))
        await_pixels(cursor_samples(500, 350, colors))
        # Clip the cursor's nonzero hotspot against the top/left display edge.
        desktop.execute({"type": "move", "x": 1, "y": 1})
        await_pixels(cursor_samples(1, 1, colors) + [((503, 351), background)])
        gui(
            lambda: area.get_window().set_cursor(
                Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.BLANK_CURSOR)
            )
        )
        await_pixels([((4, 2), background), ((20, 18), background)])
        desktop.execute({"type": "move", "x": 400, "y": 300})
        await_pixels([((403, 301), background), ((419, 317), background)])
        print(
            json.dumps(
                {
                    "backend": sys.argv[1],
                    "viewport": size,
                    "guest_cursor_pixels": True,
                    "hotspot": [5, 7],
                    "idle_motion": True,
                    "idle_shape_change": True,
                    "clipping": True,
                    "hide": True,
                    "no_trails": True,
                    "active_cursor_plane": True,
                    "click": clicks[0],
                }
            ),
            flush=True,
        )
    except BaseException as error:
        errors.append(error)
    finally:
        os.close(drm_state)
        desktop.close()
        GLib.idle_add(Gtk.main_quit)


window.show_all()
thread = threading.Thread(target=exercise)
thread.start()
Gtk.main()
thread.join(10)
window.destroy()
Path(metadata).unlink()
assert not thread.is_alive()
if errors:
    raise errors[0]
