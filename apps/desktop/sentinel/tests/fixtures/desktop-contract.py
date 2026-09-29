"""Disposable-VM visual/input oracle for native Wayland and X11/Xwayland.

The fixture draws real GTK surfaces and observes their pixels through Sentinel's
production screenshot path. Actual application events, not host cursor metadata,
acknowledge virtual input. Frame-clock notifications drive bounded capture waits.
"""

import base64
from collections import deque
import ctypes
import io
import itertools
import json
import os
import secrets
import select
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
os.environ.update(session["environment"])
backend = sys.argv[1]
assert backend in {"x11", "wayland"}
assert sys.argv[2:] in ([], ["--diagnose-clipboard-copy-mismatch"])
diagnose_copy_mismatch = bool(sys.argv[2:])
if backend == "x11":
    assert os.environ.get("DISPLAY"), "Session did not publish its actual X11 display"
os.environ["GDK_BACKEND"] = backend

# Open the privileged computer-control connection first, then exercise the GUI
# as its real desktop user. Do not accidentally qualify only root applications.
sys.path.insert(0, "/opt/sentinel/desktop")
from sentinel_display import Clipboard, Desktop  # noqa: E402 -- requires guest module path

user = session["user"]
# The production metadata is root-private. Preserve its exact contents in a
# private test-only runtime file before dropping privileges; this does not
# fabricate a display/auth cookie or run GUI/clipboard clients as root.
metadata_fd, metadata_path = tempfile.mkstemp(
    prefix="sentinel-oracle-", suffix=".json", dir=session["environment"]["XDG_RUNTIME_DIR"]
)
with os.fdopen(metadata_fd, "w") as metadata:
    json.dump(session, metadata)
    os.fchown(metadata.fileno(), user["uid"], user["gid"])
clipboard = Clipboard(metadata_path)
desktop = Desktop(clipboard=clipboard)
os.setgroups(user["groups"])
os.setgid(user["gid"])
os.setuid(user["uid"])
assert os.getuid() != 0
os.chdir(user["home"])

import gi  # noqa: E402 -- initialize GUI only after switching to the desktop user

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402 -- requires GI version selection
from PIL import Image  # noqa: E402 -- keep GUI imports after session initialization

signal.alarm(60)
window = Gtk.Window(title="Sentinel desktop contract")
actual_backend = type(Gdk.Display.get_default()).__name__.lower()
window.fullscreen()
area = Gtk.DrawingArea()
area.set_can_focus(True)
area.add_events(
    Gdk.EventMask.BUTTON_PRESS_MASK
    | Gdk.EventMask.BUTTON_RELEASE_MASK
    | Gdk.EventMask.KEY_PRESS_MASK
    | Gdk.EventMask.POINTER_MOTION_MASK
    | Gdk.EventMask.ENTER_NOTIFY_MASK
    | Gdk.EventMask.LEAVE_NOTIFY_MASK
    | Gdk.EventMask.FOCUS_CHANGE_MASK
)
window.add(area)
color = (0, 0, 1)
frames = threading.Condition()
sequence = 0
frame_activity = {"ticks": 0, "last_tick_ns": None, "last_draw_ns": None}
clicked, keyed = threading.Event(), threading.Event()
first_click_position = []
errors = []
input_events = deque(maxlen=40)
sample_timing = threading.Event()
timing_ready = threading.Event()
tick_times = []
timing_lock = threading.Lock()


def on_gui(callback):
    """Bounded cross-thread invocation; GTK is only accessed on its main loop."""
    done = threading.Event()
    values, failures = [], []

    def invoke():
        try:
            values.append(callback())
        except BaseException as error:
            failures.append(error)
        finally:
            done.set()
        return False

    GLib.idle_add(invoke)
    if not done.wait(5):
        raise TimeoutError("GTK clipboard callback was not dispatched")
    if failures:
        raise failures[0]
    return values[0]


class WaylandSelectionObserver:
    """Observe completed native selection transfers, not elapsed time or retries."""

    def __init__(self):
        self.events = []
        self.buffer = b""
        self.process = None
        self.errors = tempfile.TemporaryFile()

    def __enter__(self):
        # stdout belongs to this observer. Each callback emits one bounded record
        # after reading the offered data; payload never becomes a shell argument.
        callback = (
            "import json,os,sys; data=sys.stdin.buffer.read(262145); "
            'print(json.dumps({"state":os.environ.get("CLIPBOARD_STATE"),'
            '"text":data.decode("utf-8"),"oversize":len(data)>262144}),flush=True)'
        )
        try:
            self.process = subprocess.Popen(
                [
                    "/opt/sentinel/graphics/bin/wl-paste",
                    "--no-newline",
                    "--type",
                    "text",
                    "--watch",
                    sys.executable,
                    "-c",
                    callback,
                ],
                env={**session["environment"], "LC_ALL": "C.UTF-8"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self.errors,
                start_new_session=True,
            )
            marker = "Sentinel selection barrier " + secrets.token_hex(16)
            baseline_ready = threading.Event()

            def observe_baseline():
                owner = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

                def changed(clipboard_owner, _event):
                    clipboard_owner.request_text(
                        lambda _owner, text, _data: (
                            baseline_ready.set() if text == marker else None
                        ),
                        None,
                    )

                return owner.connect("owner-change", changed)

            baseline_handler = on_gui(observe_baseline)
            try:
                clipboard.write(marker)
                self.wait(marker, phase="ready")
                # The setup marker must also have crossed into Xwayland before
                # the tested Copy. Otherwise delayed setup could steal ownership.
                assert baseline_ready.wait(5), "Baseline selection did not reach GTK/Xwayland"
            finally:
                on_gui(
                    lambda: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).disconnect(baseline_handler)
                )
            self.marker = marker
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def wait(self, expected, *, phase):
        deadline = time.monotonic() + 5
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                event = json.loads(line)
                event.update(phase=phase, observed_at=time.monotonic())
                self.events.append(event)
                assert not event["oversize"], "Clipboard observation exceeds 256 KiB"
                assert len(self.events) <= 64, "Excessive selection churn"
                if event["text"] == expected and event["state"] in ("data", "sensitive"):
                    return
                if phase == "copy" and event["state"] != "nil" and event["text"] != self.marker:
                    raise AssertionError(f"Unexpected competing clipboard selection: {event!r}")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError(f"Wayland selection did not reach {phase}: {self.events!r}")
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError(f"Wayland selection observer exited: {self.process.poll()}")
            self.buffer += chunk
            # JSON can escape each input byte as six ASCII bytes (e.g. NUL).
            if len(self.buffer) > 6 * 262145 + 1024:
                raise ValueError("Oversized observer record")

    def __exit__(self, _kind, original, _traceback):
        cleanup_errors = []

        def attempt(action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append(repr(error))

        def stop():
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)

        def report():
            self.errors.seek(0)
            print(
                json.dumps(
                    {
                        "clipboard_selection_events": self.events,
                        "observer_errors": self.errors.read().decode(errors="replace")[-4096:],
                        "observer_cleanup_errors": cleanup_errors,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )

        if self.process:
            attempt(stop)
            attempt(self.process.stdout.close)
        attempt(report)
        attempt(self.errors.close)
        if cleanup_errors:
            message = "Clipboard observer cleanup failed: " + "; ".join(cleanup_errors)
            if original is not None:
                original.add_note(message)
            else:
                raise RuntimeError(message)


def clipboard_contract(diagnose_copy_mismatch=False):
    # The X11 server acknowledgement is not a Wayland bridge acknowledgement.
    # Watch the destination protocol before one real Copy and one production read.
    if backend == "x11" and session["clipboard_backend"] == "wayland":
        with WaylandSelectionObserver() as observer:
            result = clipboard_contract_transfer(diagnose_copy_mismatch, observer)
            result["selection_events"] = observer.events
            return result
    return clipboard_contract_transfer(diagnose_copy_mismatch)


def clipboard_contract_transfer(diagnose_copy_mismatch=False, observer=None):
    # This is an application-owned selection, not wl-copy/xclip reading their
    # own clipboard. In particular, the Wayland window retains keyboard focus
    # throughout the check, exposing KWin's X11-focus clipboard restriction.
    application_text = "GTK application → Sentinel · 世界 👋\n"
    if observer:
        application_text += secrets.token_hex(16) + "\n"
    sentinel_text = "Sentinel → GTK application · café 🎨\n"

    def require_focus():
        assert (
            window.is_active() and area.has_focus()
        ), "Clipboard oracle lost application keyboard focus"

    copied = threading.Event()

    def copy_from_application(_widget, event):
        if (
            Gdk.keyval_to_lower(event.keyval) != Gdk.KEY_c
            or not event.state & Gdk.ModifierType.CONTROL_MASK
        ):
            return False
        require_focus()
        owner = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        owner.set_text(application_text, -1)
        Gdk.Display.get_default().sync()
        copied.set()
        return True

    # A normal Copy action carries the current input serial. An arbitrary idle
    # callback can carry an older serial than a clipboard manager's update;
    # compositors correctly reject that stale ownership request. Exercise one
    # actual keyboard Copy gesture, not retries or background selection writes.
    copy_handler = on_gui(lambda: area.connect("key-press-event", copy_from_application))
    try:
        desktop.execute({"type": "keypress", "keys": ["Control_L", "c"]})
        assert copied.wait(5), "GTK did not receive the Copy keyboard gesture"
    finally:
        on_gui(lambda: area.disconnect(copy_handler))
    if observer:
        observer.wait(application_text, phase="copy")
    received_text = clipboard.read()
    if diagnose_copy_mismatch and received_text != application_text:
        # Preserve a failed gate, not a passing retry. This explicit diagnostic
        # mode lets unrelated rendering/restart checks finish; the harness still
        # fails overall. Reverse transfer is untested without application ownership.
        on_gui(require_focus)
        return {
            "application_to_sentinel": False,
            "sentinel_to_application": None,
            "backend": session["clipboard_backend"],
            "failure": {
                "kind": "application-selection-mismatch",
                "received": received_text,
                "local_owner": repr(
                    on_gui(lambda: Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD))
                ),
            },
        }
    assert received_text == application_text, (
        "Sentinel did not read the focused application's selection: "
        f"received={received_text!r}, backend={session['clipboard_backend']}, "
        f"local_owner={on_gui(lambda: Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD))!r}"
    )
    owner_changed = threading.Event()

    def watch_owner():
        local_owner = Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD)
        assert local_owner is not None, "GTK application does not own its copied selection"

        def changed(_clipboard, _event):
            # Ignore a queued notification of our original GTK ownership.
            if Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD) != local_owner:
                owner_changed.set()

        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).connect("owner-change", changed)

    owner_handler = on_gui(watch_owner)
    try:
        clipboard.write(sentinel_text)
        # Clipboard ownership crosses asynchronous compositor/client queues.
        # A writer exiting does not mean GTK has received the new selection;
        # request_text would still return GTK's own cached text until this event.
        assert owner_changed.wait(5), "GTK did not observe Sentinel taking clipboard ownership"
    finally:
        on_gui(lambda: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).disconnect(owner_handler))
    pasted, paste_result = threading.Event(), []

    def paste_in_application():
        require_focus()

        def received(_clipboard, text, _data):
            paste_result.append(text)
            pasted.set()

        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_text(received, None)

    on_gui(paste_in_application)
    assert pasted.wait(5), "GTK did not receive the Sentinel clipboard selection"
    assert paste_result == [sentinel_text], f"Application clipboard mismatch: {paste_result!r}"
    on_gui(require_focus)
    return {
        "application_to_sentinel": True,
        "sentinel_to_application": True,
        "backend": session["clipboard_backend"],
    }


def draw(_widget, context):
    global sequence
    context.set_source_rgb(*color)
    context.paint()
    # Solid fullscreen coverage cannot detect a compositor zooming the surface
    # beyond its final size. Fixed interior fiducials measure its geometry too.
    for x, y in geometry_markers(_widget.get_allocated_width(), _widget.get_allocated_height()):
        context.set_source_rgb(0, 0, 0)
        context.rectangle(x - 6, y - 6, 12, 12)
        context.fill()
        context.set_source_rgb(1, 1, 1)
        context.rectangle(x - 4, y - 4, 8, 8)
        context.fill()
    # Keep presentation event-driven while the test waits for the host capture.
    # A small changing marker forces damage without affecting sample positions.
    context.set_source_rgb(1, 1, 1)
    context.rectangle(4 + sequence % 32, 4, 2, 2)
    context.fill()
    with frames:
        sequence += 1
        frame_activity["last_draw_ns"] = time.monotonic_ns()
        frames.notify_all()


def click(_widget, event):
    global color
    if event.button == 1:
        if not first_click_position:
            first_click_position.append((event.x, event.y))
        color = (0, 1, 0)
        area.grab_focus()
        clicked.set()
    return True


def observe(_widget, event):
    if event.type in {
        Gdk.EventType.MOTION_NOTIFY,
        Gdk.EventType.ENTER_NOTIFY,
        Gdk.EventType.LEAVE_NOTIFY,
        Gdk.EventType.BUTTON_PRESS,
        Gdk.EventType.BUTTON_RELEASE,
        Gdk.EventType.FOCUS_CHANGE,
    }:
        input_events.append(
            {
                "type": str(event.type),
                "monotonic_ns": time.monotonic_ns(),
                "x": getattr(event, "x", None),
                "y": getattr(event, "y", None),
                "button": getattr(event, "button", None),
            }
        )
    return False


def key(_widget, event):
    global color
    if event.keyval == Gdk.KEY_space:
        color = (1, 0, 0)
        keyed.set()
        return True
    return False


def tick(widget, _clock):
    with frames:
        frame_activity["ticks"] += 1
        frame_activity["last_tick_ns"] = time.monotonic_ns()
    # Actual application callback arrival times, not compositor predictions or
    # physical monitor presentation. Collect at most 120 consecutive intervals.
    if sample_timing.is_set():
        with timing_lock:
            if len(tick_times) < 121:
                tick_times.append(time.monotonic_ns())
                if len(tick_times) == 121:
                    timing_ready.set()
    widget.queue_draw()
    return True


def callback_timing():
    sample_timing.set()
    complete = timing_ready.wait(10)
    with timing_lock:
        captured = list(tick_times)
    intervals = [(after - before) / 1_000_000 for before, after in itertools.pairwise(captured)]
    if not intervals:
        raise TimeoutError("No application frame-clock intervals after input")
    ordered = sorted(intervals)
    return {
        "kind": "application_frame_clock_callbacks_not_physical_display_fps",
        "interval_count": len(intervals),
        "complete": complete,
        "median_ms": round(statistics.median(intervals), 3),
        "p95_ms": round(ordered[(95 * len(ordered) + 99) // 100 - 1], 3),
        "max_ms": round(max(intervals), 3),
        "effective_callback_hz": round(1000 * len(intervals) / sum(intervals), 2),
    }


def geometry_markers(width, height):
    return [
        (116, 116),
        *((width * x // 3, height * y // 3) for x, y in [(1, 1), (2, 1), (1, 2), (2, 2)]),
    ]


def geometry_samples(width, height):
    return [
        ((x + dx, y + dy), expected)
        for x, y in geometry_markers(width, height)
        for dx, dy, expected in [
            (0, 0, (255, 255, 255)),
            (-3, 0, (255, 255, 255)),
            (2, 0, (255, 255, 255)),
            (0, -3, (255, 255, 255)),
            (0, 2, (255, 255, 255)),
            (-5, 0, (0, 0, 0)),
            (4, 0, (0, 0, 0)),
            (0, -5, (0, 0, 0)),
            (0, 4, (0, 0, 0)),
        ]
    ]


def capture_sample_positions(width, height):
    # Check broad fullscreen coverage, but not compositor-owned edge shading.
    # The precise interior fiducials independently verify the surface mapping;
    # uniform boundary colors alone cannot detect its scale or translation.
    # Keep clear of the animated damage marker at x=4..36, y=4..5.
    inset = 20
    return [
        *((width * x // 4, height * y // 4) for x, y in [(1, 1), (3, 1), (1, 3), (3, 3)]),
        (inset, inset),
        (width - inset - 1, inset),
        (inset, height - inset - 1),
        (width - inset - 1, height - inset - 1),
        (width // 2, inset),
        (width // 2, height - inset - 1),
        (inset, height // 2),
        (width - inset - 1, height // 2),
        (100, 100),
    ]


def capture_color(desktop, expected, *, pointer_entered=True):
    deadline = time.monotonic() + 15
    seen = -1
    image = None
    while True:
        with frames:
            notified = frames.wait_for(
                lambda previous=seen: sequence != previous,
                max(0, deadline - time.monotonic()),
            )
            seen = sequence
            activity = {**frame_activity, "draws": sequence}
        if not notified:
            error = TimeoutError(
                "Application produced no new draw callback before the pixel-gate deadline"
            )
            diagnostics = {
                "backend": actual_backend,
                "expected_color": expected,
                "monotonic_ns": time.monotonic_ns(),
                "activity": activity,
                "input_events": list(input_events),
            }
            runtime = Path(session["environment"]["XDG_RUNTIME_DIR"])
            try:
                if image is not None:
                    image.save(runtime / "sentinel-oracle-last-frame.png")
                shot = desktop.screenshot()  # Evidence only; cannot pass the failed gate.
                Image.open(io.BytesIO(base64.b64decode(shot["screenshot"].split(",", 1)[1]))).save(
                    runtime / "sentinel-oracle-failure.png"
                )
            except Exception as diagnostic_error:
                diagnostics["capture_error"] = str(diagnostic_error)
            try:
                diagnostics["gtk"] = on_gui(
                    lambda: {
                        "window_mapped": window.get_mapped(),
                        "area_mapped": area.get_mapped(),
                        "window_visible": window.get_visible(),
                        "area_visible": area.get_visible(),
                        "active": window.is_active(),
                        "area_focus": area.has_focus(),
                        "allocation": [area.get_allocated_width(), area.get_allocated_height()],
                        "window_state": int(window.get_window().get_state()),
                        "frame_counter": area.get_frame_clock().get_frame_counter(),
                    }
                )
            except Exception as diagnostic_error:
                diagnostics["gtk_error"] = str(diagnostic_error)
            try:
                (runtime / "sentinel-oracle-failure.json").write_text(
                    json.dumps(diagnostics, indent=2)
                )
            except Exception as diagnostic_error:
                diagnostics["write_error"] = str(diagnostic_error)
            error.add_note(json.dumps(diagnostics))
            raise error
        shot = desktop.screenshot()
        image = Image.open(
            io.BytesIO(base64.b64decode(shot["screenshot"].split(",", 1)[1]))
        ).convert("RGB")
        width, height = image.size
        samples = [
            image.getpixel(point)
            for point in capture_sample_positions(width, height)
            if pointer_entered or point != (100, 100)
        ]
        geometry = [
            (image.getpixel(point), wanted) for point, wanted in geometry_samples(width, height)
        ]
        if all(
            all(abs(actual - wanted) <= 3 for actual, wanted in zip(pixel, expected))
            for pixel in samples
        ) and all(
            all(abs(actual - wanted) <= 3 for actual, wanted in zip(pixel, target))
            for pixel, target in geometry
        ):
            return {"size": [width, height], "pixels": samples, "geometry_verified": True}
        if time.monotonic() >= deadline:
            image.save(
                Path(session["environment"]["XDG_RUNTIME_DIR"]) / "sentinel-oracle-failure.png"
            )
            raise AssertionError(
                f"Desktop pixels did not reach {expected}: {samples}; geometry={geometry}"
            )


def x11_pointer():
    if backend != "x11":
        return None
    # Independent connection: never call GTK's X connection from this thread.
    xlib = ctypes.CDLL("libX11.so.6")
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    xlib.XQueryPointer.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        *([ctypes.POINTER(ctypes.c_int)] * 4),
        ctypes.POINTER(ctypes.c_uint),
    ]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = xlib.XOpenDisplay(None)
    if not display:
        return {"connected": False}
    try:
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy = [ctypes.c_int() for _ in range(4)]
        mask = ctypes.c_uint()
        valid = xlib.XQueryPointer(
            display,
            xlib.XDefaultRootWindow(display),
            *map(ctypes.byref, [root, child, rx, ry, wx, wy, mask]),
        )
        return {
            "valid": bool(valid),
            "child": child.value,
            "x": rx.value,
            "y": ry.value,
            "buttons": mask.value,
        }
    finally:
        xlib.XCloseDisplay(display)


def exercise():
    try:
        assert backend in actual_backend, (backend, actual_backend)
        try:
            # Before the first real input, Xwayland may not own the stationary
            # pointer yet. Its previous cursor is not an application pixel.
            # Keep every surface/geometry sample, then require the click pixel
            # too in both post-click captures, with no preliminary pointer move.
            blue = capture_color(desktop, (0, 0, 255), pointer_entered=False)
            pointer_before = x11_pointer()
            focus_before = on_gui(
                lambda: {
                    "active": window.is_active(),
                    "area_focus": area.has_focus(),
                    "mapped": window.get_mapped(),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )
            desktop.execute({"type": "click", "x": 100, "y": 100})
            if not clicked.wait(5):
                diagnostics = {
                    "initial_events": list(input_events),
                    "pointer_before": pointer_before,
                    "pointer_after": x11_pointer(),
                    "focus_before": focus_before,
                }
                # Diagnose focus vs motion without accepting either retry as a
                # successful test. The original single-click contract failed.
                desktop.execute({"type": "click", "x": 100, "y": 100})
                diagnostics["same_point_retry"] = clicked.wait(1)
                desktop.execute({"type": "click", "x": 120, "y": 120})
                diagnostics["moved_point_retry"] = clicked.wait(1)
                diagnostics["final_events"] = list(input_events)
                raise AssertionError(
                    f"Application did not receive the virtual pointer click: {diagnostics}"
                )
            assert all(
                abs(value - 100) <= 2 for value in first_click_position[0]
            ), f"First application click missed the requested point (100, 100): {first_click_position[0]}"
            capture_color(desktop, (0, 255, 0))
            desktop.execute({"type": "keypress", "keys": ["space"]})
            assert keyed.wait(5), "Application did not receive the virtual keyboard event"
            capture_color(desktop, (255, 0, 0))
            print(
                json.dumps(
                    {
                        "backend": actual_backend,
                        "viewport": blue["size"],
                        "geometry_verified": blue["geometry_verified"],
                        "click": True,
                        "first_click_position": first_click_position[0],
                        "key": True,
                        "visible_colors": 3,
                        "clipboard": clipboard_contract(diagnose_copy_mismatch),
                        "timing": callback_timing(),
                    }
                ),
                flush=True,
            )
        finally:
            desktop.close()
    except BaseException as error:  # noqa: BLE001
        # Propagate worker errors after GTK exits, including assertion failures.
        errors.append(error)
    finally:
        GLib.idle_add(Gtk.main_quit)


area.connect("draw", draw)
area.connect("event", observe)
window.connect("focus-in-event", observe)
window.connect("focus-out-event", observe)
area.connect("button-press-event", click)
area.connect("key-press-event", key)
area.add_tick_callback(tick)
window.show_all()
# This oracle measures the application surface, including the click pixel.
# The production capture correctly includes the pointer, so hide it through
# GTK's native window cursor instead of masking pixels or moving before the
# first-click test. desktop-cursor.py independently qualifies visible cursors.
area.get_window().set_cursor(
    Gdk.Cursor.new_for_display(window.get_display(), Gdk.CursorType.BLANK_CURSOR)
)
thread = threading.Thread(target=exercise)
thread.start()
Gtk.main()
thread.join(timeout=15)
assert not thread.is_alive(), "Desktop oracle did not finish"
window.destroy()
Path(metadata_path).unlink()
if errors:
    raise errors[0]
