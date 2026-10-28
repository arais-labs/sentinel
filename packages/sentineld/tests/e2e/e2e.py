#!/usr/bin/env python3
"""End-to-end tests of sentineld workspaces (DESIGN.md "End-to-end tests").

Runs a private sentineld (its own state and cache, never the app's) and, for every selected
distribution x desktop plus a desktop-less workspace per distribution, creates a workspace and checks
what a person would: it installs and starts, the agent runs commands, the desktop session is up, a
viewer gets video and a visible cursor that follows the pointer to every edge, clicks land where
sent, stop is quick and clean, a restart boots a clean root, and a second workspace reuses the
layer. Prints a table, writes report.json and per-failure evidence, exits non-zero on any failure.

    make -C packages/sentineld e2e [DISTROS=alpine,debian] [DESKTOPS=xfce,gnome] [JOBS=2]
    tests/e2e/e2e.py --help

Standard library only; the viewer is build/tests/e2e-viewer (viewer.cpp), the guest pointer probe
probe_x11.py.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SENTINELD = os.path.normpath(os.path.join(HERE, "..", ".."))
REPO_PACKAGES = os.path.dirname(SENTINELD)
GiB = 1 << 30

# What each desktop's session runs once it is up (/proc/*/comm, at most 15 characters).
SESSION_PROCESSES = {
    "xfce": ["Xorg", "xfwm4", "xfce4-panel"],
    "lxqt": ["labwc", "lxqt-panel"],
    "gnome": ["gnome-shell"],
    "plasma": ["kwin_wayland", "plasmashell"],
}
# Workspace resources per kind (validate()'s floors and the distribution's minimum apply).
RESOURCES = {
    "none": {"cpus": 2, "memory_gib": 2, "disk_gib": 16},
    "desktop": {"cpus": 2, "memory_gib": 4, "disk_gib": 16},
    "windows": {"cpus": 4, "memory_gib": 8, "disk_gib": 32},
}
# Windows' own shutdown takes 3-15 s (longer while it services itself): it fails only when forced.
LIMITS = {"stop_s": 10.0, "stop_windows_s": 60.0, "reuse_start_s": 120.0}
EDGE = 2  # pixels from the screen's edges the edge points sit at
# Windows' computer-use text: characters no US key types, a line break, one beyond the BMP.
TYPED = "Grüße, señor — ça va? Ελληνικά 世界 😀\nzweite Zeile"
PASTED = "Collé depuis l'hôte ✓ Ünïcödé"

CHECKS = [
    "start",
    "exec",
    "network",
    "shell",
    "shell_stream",
    "terminal",
    "session",
    "video",
    "screenshot",
    "gpu",
    "vulkan",
    "d3d_vulkan",
    "typing",
    "clipboard",
    "wallpaper",
    "cursor",
    "cursor_moving",
    "pointer",
    "clicks",
    "stop",
    "restart",
    "fs_clean",
    "reuse",
]


def log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


class Failure(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# Control client (DESIGN.md §4): NDJSON over <state>/sentineld.sock, status{api:1} first.


class Client:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.buf = b""
        self.next = 1
        self.lock = threading.Lock()
        self.request("status", api=1)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def line(self, timeout=None):
        self.sock.settimeout(timeout)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise Failure("sentineld closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def request(self, op, wait=None, **fields):
        with self.lock:
            rid = self.next
            self.next += 1
            fields.update(id=rid, op=op)
            self.sock.sendall((json.dumps(fields) + "\n").encode())
            while True:
                reply = self.line(wait)
                if reply.get("id") == rid:
                    break
        if "error" in reply:
            raise Failure("%s: %s" % (op, reply["error"].get("message", reply["error"])))
        return reply


def client_for(daemon):
    return Client(daemon.socket)


def process_open(daemon, workspace, argv):
    """The process op (DESIGN.md §4): returns the socket after `started`; frames follow."""
    c = Client(daemon.socket)
    c.sock.sendall(
        (
            json.dumps({"id": 99, "op": "process", "workspace": workspace, "argv": argv}) + "\n"
        ).encode()
    )
    reply = c.line(60)
    if "error" in reply:
        c.close()
        raise Failure("process: %s" % reply["error"].get("message"))
    return c


def frames(c, timeout):
    """A process connection's frames after `started` (DESIGN.md §4): (type, payload) until it ends."""
    c.sock.settimeout(timeout)
    data, c.buf = c.buf, b""
    while True:
        while len(data) >= 5 and len(data) >= 5 + struct.unpack(">I", data[1:5])[0]:
            n = struct.unpack(">I", data[1:5])[0]
            yield data[0], data[5 : 5 + n]
            data = data[5 + n :]
        chunk = c.sock.recv(65536)
        if not chunk:
            return
        data += chunk


def process_input(c, data):
    """A stdin frame; empty: the end of input."""
    c.sock.sendall(b"\0" + struct.pack(">I", len(data)) + data)


# ---------------------------------------------------------------------------------------------
# The private daemon.


class Daemon:
    def __init__(self, args, run_dir):
        self.args = args
        self.run_dir = run_dir
        self.state = tempfile.mkdtemp(prefix="sentineld-e2e-")  # short: the socket path limit
        self.cache = args.cache
        self.socket = os.path.join(self.state, "sentineld.sock")
        self.proc = None
        self.caffeinate = None
        self.events = {}  # workspace -> [(time, record)]
        self.cond = threading.Condition()
        self.watcher = None
        self.stopping = False

    def start(self):
        if self.args.fresh_install:
            shutil.rmtree(os.path.join(self.cache, "images"), ignore_errors=True)
        os.makedirs(self.cache, exist_ok=True)
        log_path = os.path.join(self.run_dir, "sentineld.log")
        self.log = open(log_path, "ab")
        cmd = [
            self.args.sentineld,
            "--state",
            self.state,
            "--cache",
            self.cache,
            "--share",
            self.args.share,
            "--distributions",
            self.args.distributions,
            "--workspace",
            self.args.workspace,
            "--mke2fs",
            self.args.mke2fs,
            "--lifeline",
            "--keep-images",
        ]
        # --keep-images: installed bases and layers stay in the cache for the next run, which then
        # boots them instead of installing again (--fresh-install starts from none).
        # --lifeline: sentineld stops (powering off every VM) when this process goes, however it goes.
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=self.log, stderr=self.log)
        self.caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(self.proc.pid)])
        for _ in range(200):
            if os.path.exists(self.socket):
                try:
                    Client(self.socket).close()
                    break
                except OSError:
                    pass
            if self.proc.poll() is not None:
                raise Failure("sentineld exited at start (see %s)" % log_path)
            time.sleep(0.05)
        else:
            raise Failure("sentineld did not listen")
        self.watcher = threading.Thread(target=self.watch, daemon=True)
        self.watcher.start()

    def watch(self):
        c = Client(self.socket)
        snapshot = c.request("list", watch=True)
        with self.cond:
            for wid, rec in snapshot["workspaces"].items():
                self.events.setdefault(wid, []).append((time.time(), rec))
        while not self.stopping:
            try:
                event = c.line()
            except (Failure, OSError):
                return
            if event.get("event") != "workspace":
                continue
            with self.cond:
                self.events.setdefault(event["id"], []).append((time.time(), event["record"]))
                self.cond.notify_all()

    def record(self, wid):
        with self.cond:
            history = self.events.get(wid)
            return history[-1][1] if history else None

    def history(self, wid):
        with self.cond:
            return list(self.events.get(wid, []))

    def wait(self, wid, predicate, timeout, poll=None):
        """Waits for a record satisfying predicate; `poll` runs every 0.25 s meanwhile."""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                history = self.events.get(wid)
                rec = history[-1][1] if history else None
                if rec is not None and predicate(rec):
                    return rec
                if time.time() > deadline:
                    raise Failure("timed out after %d s (last: %s)" % (timeout, summary(rec)))
                if self.proc.poll() is not None:
                    raise Failure("sentineld exited")
                self.cond.wait(0.25)
                if poll:
                    self.cond.release()
                    try:
                        poll()
                    finally:
                        self.cond.acquire()

    def workspace_dir(self, wid):
        return os.path.join(self.state, "workspaces", wid)

    def console_tail(self, wid, lines=120):
        logs = os.path.join(self.state, "logs", wid)
        try:
            newest = sorted(f for f in os.listdir(logs) if f.startswith("console-"))[-1]
            with open(os.path.join(logs, newest), "rb") as f:
                data = f.read()[-256 * 1024 :]
            return "\n".join(data.decode("utf-8", "replace").splitlines()[-lines:])
        except (OSError, IndexError):
            return ""

    def shutdown(self, created):
        """Deletes every workspace this run made, then stops sentineld; never raises."""
        try:
            c = Client(self.socket)
            for wid in list(created):
                try:
                    c.request("delete", wait=60, workspace=wid)
                except Exception as e:  # noqa: BLE001 - cleanup goes on
                    log("cleanup: delete %s: %s" % (wid, e))
            c.close()
        except Exception as e:  # noqa: BLE001
            log("cleanup: %s" % e)
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()  # the lifeline: sentineld drains and exits
            except OSError:
                pass
            try:
                self.proc.wait(60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        # Children it could not stop (it never leaves any on a normal drain).
        subprocess.run(["pkill", "-9", "-f", self.state], stderr=subprocess.DEVNULL)
        if self.caffeinate:
            self.caffeinate.terminate()
        shutil.rmtree(self.state, ignore_errors=True)


def summary(rec):
    if not rec:
        return "no record"
    return "state=%s operation=%s progress=%s error=%s" % (
        rec.get("state"),
        rec.get("operation"),
        rec.get("progress"),
        (rec.get("error") or "")[:300],
    )


# ---------------------------------------------------------------------------------------------
# Viewer: build/tests/e2e-viewer, a WebTransport client like the desktop app's.


class Viewer:
    def __init__(self, binary, endpoint, log_path):
        self.proc = subprocess.Popen(
            [binary, str(endpoint["port"]), endpoint["cert_sha256"], endpoint["ticket"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.events = []
        self.lock = threading.Condition()
        self.log = open(log_path, "a")
        self.width = self.height = None
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for raw in self.proc.stdout:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            e["wall"] = time.time()
            light = {k: v for k, v in e.items() if k != "rgba"}
            if e.get("type") != "frame" or e.get("key"):
                self.log.write(json.dumps(light) + "\n")
            with self.lock:
                if e.get("type") == "config":
                    self.width, self.height = e["width"], e["height"]
                self.events.append(e)
                self.lock.notify_all()

    def wait_for(self, predicate, timeout):
        deadline = time.time() + timeout
        with self.lock:
            while True:
                for e in self.events:
                    if predicate(e):
                        return e
                if time.time() > deadline or self.proc.poll() is not None:
                    return None
                self.lock.wait(0.2)

    def since(self, t):
        with self.lock:
            return [e for e in self.events if e["wall"] >= t]

    def send(self, line):
        try:
            self.proc.stdin.write((line + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def input(self, device, events):
        """A §6 input payload: device, count, then (type, code, value) with SYN last."""
        events = list(events) + [(0, 0, 0)]
        body = struct.pack(">HH", device, len(events))
        for t, c, v in events:
            body += struct.pack(">HHi", t, c, v)
        self.send("input " + body.hex())

    def move(self, x, y):
        w, h = self.width or 1920, self.height or 1200
        self.input(
            1, [(3, 0, round(x * 65535 / max(w - 1, 1))), (3, 1, round(y * 65535 / max(h - 1, 1)))]
        )

    def button(self, code, down):
        self.input(1, [(1, code, 1 if down else 0)])

    def key(self, code, down):
        self.input(2, [(1, code, 1 if down else 0)])

    def close(self):
        self.send("quit")
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def glide(viewer, points, step=40, interval=0.008):
    """Moves the pointer along `points` the way a hand does: small steps, ~120 Hz."""
    x0, y0 = points[0]
    viewer.move(x0, y0)
    for x1, y1 in points[1:]:
        n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / step))
        for i in range(1, n + 1):
            viewer.move(round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n))
            time.sleep(interval)
        x0, y0 = x1, y1


# ---------------------------------------------------------------------------------------------
# Images: snapshots come as PNG or JPEG; macOS's sips turns them into BMP, read here directly.


class Image:
    def __init__(self, path):
        bmp = path + ".bmp"
        subprocess.run(
            ["sips", "-s", "format", "bmp", path, "--out", bmp],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(bmp, "rb") as f:
            data = f.read()
        os.remove(bmp)
        (offset,) = struct.unpack_from("<I", data, 10)
        self.width, height = struct.unpack_from("<ii", data, 18)
        (bpp,) = struct.unpack_from("<H", data, 28)
        self.height = abs(height)
        self.topdown = height < 0
        self.bytes = bpp // 8
        self.stride = (self.width * bpp + 31) // 32 * 4
        self.data = memoryview(data)[offset:]

    def px(self, x, y):
        row = y if self.topdown else self.height - 1 - y
        i = row * self.stride + x * self.bytes
        b, g, r = self.data[i], self.data[i + 1], self.data[i + 2]
        return r, g, b

    def stats(self, grid=48):
        colors, dark = set(), 0
        for gy in range(grid):
            for gx in range(grid):
                r, g, b = self.px(
                    gx * (self.width - 1) // (grid - 1), gy * (self.height - 1) // (grid - 1)
                )
                colors.add((r >> 4, g >> 4, b >> 4))
                dark += r + g + b < 30
        return len(colors), dark / (grid * grid)

    def fraction(self, color, box, tolerance=40):
        x0, y0, x1, y1 = box
        hit = total = 0
        for y in range(y0, y1, 7):
            for x in range(x0, x1, 7):
                total += 1
                hit += all(abs(a - b) <= tolerance for a, b in zip(self.px(x, y), color))
        return hit / max(total, 1)


def changed(a, b, box, threshold=60):
    """Pixels inside box that differ between two images."""
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(a.width, b.width, x1), min(a.height, b.height, y1)
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            n += sum(abs(p - q) for p, q in zip(a.px(x, y), b.px(x, y))) > threshold
    return n


def write_png(path, width, height, rgba):
    raw = b"".join(b"\0" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height))

    def chunk(kind, body):
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    with open(path, "wb") as f:
        f.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )


# ---------------------------------------------------------------------------------------------
# One distribution x desktop.


def windows(distribution):
    """Windows guests: commands, processes and the desktop are checked the Windows way."""
    return distribution.startswith("windows-")


class Case:
    def __init__(self, run, distribution, desktop):
        self.run = run
        self.daemon = run.daemon
        self.distribution = distribution
        self.desktop = desktop
        self.name = "%s-%s" % (distribution, desktop)
        self.dir = os.path.join(run.run_dir, "cases", self.name)
        os.makedirs(self.dir, exist_ok=True)
        self.checks = {}
        self.timings = {}
        self.notes = []
        self.windows = windows(distribution)
        self.client = None
        self.viewer = None
        self.shot = 0
        self.revisions = {}
        self.started = time.time()

    # -- results
    def check(self, name, ok, detail=""):
        self.checks[name] = {"ok": ok, "detail": detail}
        log(
            "%-16s %-14s %s %s"
            % (self.name, name, "ok" if ok else ("FAIL" if ok is False else "skip"), detail)
        )

    def failed(self):
        return any(c["ok"] is False for c in self.checks.values())

    def evidence(self, wid, tag):
        # A failed base install or layer keeps its console in the cache: the newest since this case began.
        install = os.path.join(self.run.args.cache, "install")
        for name in os.listdir(install) if os.path.isdir(install) else []:
            path = os.path.join(install, name)
            if name.endswith(".console.log") and os.path.getmtime(path) >= self.started:
                shutil.copyfile(path, os.path.join(self.dir, name))
        tail = self.daemon.console_tail(wid)
        if tail:
            with open(os.path.join(self.dir, "console-%s.txt" % tag), "w") as f:
                f.write(tail)
        try:
            with open(os.path.join(self.daemon.workspace_dir(wid), "workspace.log"), "rb") as f:
                data = f.read()[-64 * 1024 :]
            with open(os.path.join(self.dir, "workspace-%s.log" % tag), "wb") as f:
                f.write(data)
        except OSError:
            pass

    # -- operations
    def spec(self, suffix=""):
        kind = "windows" if self.windows else "none" if self.desktop == "none" else "desktop"
        return {
            "name": "e2e-%s%s-%s" % (self.name, suffix, uuid.uuid4().hex[:6]),
            "distribution": self.distribution,
            "desktop": self.desktop,
            "resources": RESOURCES[kind],
        }

    def exec(self, wid, argv, timeout=60):
        r = self.client.request(
            "exec", wait=timeout + 30, workspace=wid, argv=argv, timeout=timeout
        )
        return r["code"], r["stdout"], r["stderr"]

    def sh(self, wid, script, timeout=60):
        if self.windows:
            return self.exec(wid, ["cmd.exe", "/c", script], timeout)
        return self.exec(wid, ["sh", "-c", script], timeout)

    def create(self, suffix=""):
        wid = str(uuid.uuid4())
        with self.run.lock:
            self.run.created.add(wid)
        self.revisions[wid] = self.client.request(
            "configure", workspace=wid, revision=0, spec=self.spec(suffix)
        )["record"]["revision"]
        return wid

    def start(self, wid):
        """Starts and waits for running; returns phase timings."""
        t0 = time.time()
        self.client.request("start", workspace=wid, revision=self.revisions[wid])
        boot_raw = os.path.join(self.daemon.workspace_dir(wid), "boot.raw")
        seen = {}

        def poll():
            if "disk" not in seen and os.path.exists(boot_raw):
                seen["disk"] = time.time()

        if os.path.exists(boot_raw):
            seen["disk"] = t0
        rec = self.daemon.wait(
            wid,
            lambda r: (r["state"] == "running" and not r["operation"])
            or (r["state"] == "failed" and not r["operation"]),
            self.run.args.start_timeout,
            poll,
        )
        t1 = time.time()
        if rec["state"] != "running":
            raise Failure("start failed: %s" % summary(rec))
        phases = {}
        current = None
        for t, r in self.daemon.history(wid):
            if t < t0:
                continue
            p = r.get("progress") or ""
            kind = (
                "install"
                if p.startswith("Installing the operating system")
                else "layer" if p.startswith("Installing your software") else None
            )
            if kind != current:
                if current and current in phases and len(phases[current]) == 1:
                    phases[current].append(t)
                if kind and kind not in phases:
                    phases[kind] = [t]
                current = kind
        disk = seen.get("disk", t1)
        out = {"start_s": round(t1 - t0, 1)}
        for kind, span in phases.items():
            end = span[1] if len(span) > 1 else disk
            if kind == "layer":
                end = disk  # the layer is made when the workspace's disks are cloned from it
            out[kind + "_s"] = round(end - span[0], 1)
        out["boot_s"] = round(t1 - disk, 1)
        return out

    def stop(self, wid):
        t0 = time.time()
        # Beyond the distribution's own grace (Windows: 120 s), after which sentineld powers off.
        self.client.request("stop", wait=180, workspace=wid)
        rec = self.daemon.wait(wid, lambda r: not r["operation"], 180)
        return round(time.time() - t0, 1), rec

    def snapshot(self, wid, tag):
        return self.keep_screenshot(
            self.client.request("computer", wait=60, workspace=wid)["batch"], tag
        )

    def keep_screenshot(self, batch, tag):
        url = batch.get("screenshot") or ""
        if not url.startswith("data:image/"):
            raise Failure("no screenshot: %s" % batch.get("error"))
        kind = url[len("data:image/") : url.index(";")]
        self.shot += 1
        path = os.path.join(
            self.dir, "%02d-%s.%s" % (self.shot, tag, "png" if kind == "png" else "jpg")
        )
        with open(path, "wb") as f:
            f.write(base64.b64decode(url.split(",", 1)[1]))
        return Image(path), path

    # -- the checks
    def disk_ok(self, check):
        """No new VM disks below the floor: a full host disk corrupts guests (it is skipped, not failed)."""
        free = shutil.disk_usage(self.run.args.cache).free / GiB
        if free >= self.run.args.min_disk:
            return True
        self.check(
            check,
            None,
            "skipped: %.0f GiB of disk free (needs %d)" % (free, self.run.args.min_disk),
        )
        return False

    def run_case(self):
        self.client = client_for(self.daemon)
        wid = None
        try:
            if not self.disk_ok("start"):
                return
            wid = self.create()
            try:
                t = self.start(wid)
                self.timings.update(t)
                self.check("start", True, "%.0f s" % t["start_s"])
            except Failure as e:
                self.check("start", False, str(e)[:400])
                self.evidence(wid, "start")
                return
            self.check_exec(wid)
            self.check_network(wid)
            if "shell" in self.run.distributions[self.distribution].get("features", []):
                self.check_shell(wid)
            if self.desktop != "none":
                self.check_session(wid)
                self.check_display(wid)
                if self.windows:
                    self.check_gpu(wid)
                    self.check_computer(wid)
            self.check_stop_restart(wid)
            self.check_reuse()
        except Exception as e:  # noqa: BLE001 - a broken case is a failed case, not a crashed run
            self.notes.append("harness: %s" % e)
            self.check("harness", False, "%s: %s" % (type(e).__name__, e))
            log(traceback.format_exc())
        finally:
            if self.viewer:
                self.viewer.close()
            if wid and self.failed():
                self.evidence(wid, "final")
            if wid:
                self.delete(wid)
            self.client.close()

    def delete(self, wid):
        try:
            self.client.request("delete", wait=60, workspace=wid)
            with self.run.lock:
                self.run.created.discard(wid)
        except Exception as e:  # noqa: BLE001
            self.notes.append("delete: %s" % e)

    def check_exec(self, wid):
        try:
            code, out, err = self.sh(wid, "echo e2e-ok")
            self.check(
                "exec", code == 0 and "e2e-ok" in out, "code %s %s" % (code, (err or "")[:200])
            )
        except Failure as e:
            self.check("exec", False, str(e))

    def check_network(self, wid):
        """The guest reaches the internet: a name resolves and an HTTPS page downloads."""
        if self.windows:  # PowerShell directly: cmd.exe would mangle the script's quotes
            argv = [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Resolve-DnsName example.com -Type A).IPAddress | Select-Object -First 1;"
                " (Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 https://example.com).StatusCode",
            ]
        else:
            argv = [
                "sh",
                "-c",
                "getent hosts example.com || nslookup example.com; "
                "if command -v curl >/dev/null; then curl -fsS -m 20 -o /dev/null -w '%{http_code}'"
                " https://example.com; else wget -q -T 20 -O /dev/null https://example.com && echo 200; fi",
            ]
        try:
            code, out, err = self.exec(wid, argv, 60)
            self.check(
                "network",
                code == 0 and "200" in out,
                " ".join((out + " " + (err or "")).split())[:300],
            )
        except Failure as e:
            self.check("network", False, str(e))

    def check_shell(self, wid):
        """The shell feature as the backend uses it (DESIGN.md §8): `bash -c` commands with Python, Git and
        the agents' tools; a file streamed in through `process` (uploads); and terminals: a tmux server
        that outlives the command starting it, driven over control mode like the app's terminal view,
        whose pane is a terminal."""
        work = "/tmp/e2e-shell"
        script = (
            "set -eu; rm -rf %s; mkdir -p %s; cd %s; printf '%%s\\n' \"$1\" > note.txt; "
            "python3 -c 'import sys; print(open(sys.argv[1]).read().strip()[::-1])' note.txt; "
            "git init -q repo; git -C repo -c user.name=e2e -c user.email=e2e@example.invalid "
            "commit -q --allow-empty -m e2e-commit; git -C repo log --format=%%s; "
            "for t in tmux rg jq gh; do command -v $t >/dev/null || { echo missing $t >&2; exit 1; }; done"
        ) % (work, work, work)
        try:
            code, out, err = self.exec(wid, ["bash", "-c", script, "sentinel", "e2e-shell-ok"], 120)
            self.check(
                "shell",
                code == 0 and "ko-llehs-e2e" in out and "e2e-commit" in out,
                " ".join((out + " " + (err or "")).split())[:300],
            )
        except Failure as e:
            self.check("shell", False, str(e))
        try:
            data = os.urandom(3 << 20)
            c = process_open(self.daemon, wid, ["bash", "-c", "cat > %s/stream.bin" % work])
            try:
                for at in range(0, len(data), 256 << 10):
                    process_input(c, data[at : at + (256 << 10)])
                process_input(c, b"")
                result = [json.loads(p) for t, p in frames(c, 120) if t == 3]
            finally:
                c.close()
            code, out, _ = self.exec(wid, ["bash", "-c", "sha256sum %s/stream.bin" % work])
            want = hashlib.sha256(data).hexdigest()
            self.check(
                "shell_stream",
                result == [{"code": 0}] and out.split()[:1] == [want],
                "%s, %s" % (result, out.split()[:1]),
            )
        except (Failure, OSError) as e:
            self.check("shell_stream", False, str(e))
        tmux = "tmux -u -f /dev/null -S %s/tmux.sock" % work
        try:
            code, _, err = self.exec(
                wid,
                [
                    "bash",
                    "-c",
                    "nohup %s new-session -d -s e2e -x 120 -y 30 >%s/tmux.log 2>&1 </dev/null & "
                    "for _ in $(seq 100); do %s has-session -t e2e 2>/dev/null && exit 0; sleep 0.1; done; "
                    "cat %s/tmux.log >&2; exit 1" % (tmux, work, tmux, work),
                ],
                60,
            )
            code2, _, err2 = self.exec(wid, ["bash", "-c", tmux + " has-session -t e2e"])
            if code != 0 or code2 != 0:
                raise Failure("tmux server: %s %s" % (err.strip()[:200], err2.strip()[:200]))
            c = process_open(
                self.daemon, wid, ["bash", "-c", "exec %s -C attach-session -t e2e" % tmux]
            )
            seen = b""
            try:
                process_input(c, b"send-keys -t e2e 'echo e2e-$((6*7))-pane; tty' Enter\n")
                deadline = time.time() + 60
                for t, payload in frames(c, 60):
                    seen += payload if t == 1 else b""
                    if b"e2e-42-pane" in seen and b"/dev/pt" in seen or time.time() > deadline:
                        break
                process_input(c, b"kill-server\n")
            finally:
                c.close()
            text = seen.decode("utf-8", "replace")
            ok = "e2e-42-pane" in text and "/dev/pt" in text
            tty = next((w for w in text.split() if w.startswith("/dev/pt")), "no tty")
            self.check("terminal", ok, "pane output over control mode, %s" % tty.split("\\")[0])
        except (Failure, OSError) as e:
            self.check("terminal", False, str(e))

    def processes(self, wid):
        if self.windows:
            code, out, _ = self.sh(wid, "tasklist /FO CSV /NH")
            return {line.split(",")[0].strip('"') for line in out.splitlines() if line}
        code, out, _ = self.sh(wid, "cat /proc/[0-9]*/comm 2>/dev/null")
        return set(out.split("\n"))

    def check_session(self, wid, name="session"):
        want = ["explorer.exe"] if self.windows else SESSION_PROCESSES[self.desktop]
        deadline = time.time() + 90
        while True:
            try:
                missing = [p for p in want if p not in self.processes(wid)]
            except Failure as e:
                missing = ["(%s)" % e]
            if not missing or time.time() > deadline:
                break
            time.sleep(2)
        self.check(
            name, not missing, "missing " + ", ".join(missing) if missing else ", ".join(want)
        )

    def check_display(self, wid):
        endpoint = self.client.request("display", workspace=wid)
        self.viewer = v = Viewer(
            self.run.args.viewer, endpoint, os.path.join(self.dir, "viewer.log")
        )
        key = v.wait_for(lambda e: e.get("type") == "frame" and e.get("key"), 20)
        closed = v.wait_for(lambda e: e.get("type") == "closed", 0)
        self.check(
            "video",
            bool(key) and v.width == 1920 and v.height == 1200,
            (
                "%sx%s" % (v.width, v.height)
                if key
                else "no key frame (%s)" % (closed or {}).get("reason", "timeout")
            ),
        )
        if not key:
            return
        width, height = v.width, v.height
        # The pointer somewhere neutral first: the desktop shows its cursor once the pointer moves.
        glide(v, [(width // 2, height // 2), (width // 2 + 200, height // 2 + 100)])
        # The session may still be drawing its first frame: up to a minute for a desktop to appear.
        t0 = time.time()
        while True:
            img, path = self.snapshot(wid, "desktop")
            colors, dark = img.stats()
            if (colors >= 6 and dark < 0.97) or time.time() - t0 > 60:
                break
            os.remove(path)
            time.sleep(3)
        self.timings["desktop_visible_s"] = round(time.time() - t0, 1)
        self.check(
            "screenshot",
            colors >= 6 and dark < 0.97,
            "%d colors, %.0f%% black after %.0f s (%s)"
            % (colors, dark * 100, time.time() - t0, os.path.basename(path)),
        )
        self.check_cursor(wid, width, height)
        if self.windows:
            self.check("pointer", None, "no guest pointer probe on Windows")
            self.check("clicks", None, "no guest pointer probe on Windows")
        else:
            self.check_pointer(wid, width, height)

    def probe(self, wid, args, display=False):
        """Runs the share's GPU probe (tests/e2e/d3d11_probe.c) with `args` where applications run, in
        the signed-in user's session (a scheduled task for INTERACTIVE, S-1-5-4): the agent's session 0
        has no display, and viogpu3d's Direct3D driver finds its adapter through the session's display
        devices. With display, Win32_VideoController's adapters and modes first. Returns (code, out).
        """
        code, out, err = self.exec(
            wid,
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_VideoController | ForEach-Object { 'display ' + $_.Name + ', '"
                    " + $_.Status + ', ' + $_.CurrentHorizontalResolution + 'x' + $_.CurrentVerticalResolution };"
                    if display
                    else ""
                )
                + " $probe = Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root +"
                " 'windows\\e2e-d3d11.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1;"
                " if (-not $probe) { 'no probe on the share'; exit 2 };"
                " $log = Join-Path $env:PUBLIC 'e2e-probe.txt'; Remove-Item $log -ErrorAction Ignore;"
                " $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument"
                " ('/v:on /c ' + $probe + ' %s > ' + $log + ' 2>&1 & echo exit !errorlevel! >> ' + $log);"
                " $principal = New-ScheduledTaskPrincipal -GroupId S-1-5-4;"
                " Register-ScheduledTask -TaskName e2e-probe -Action $action -Principal $principal -Force"
                " | Out-Null; Start-ScheduledTask -TaskName e2e-probe;"
                " $deadline = (Get-Date).AddSeconds(100);"
                " while ((Get-Date) -lt $deadline -and -not ((Test-Path $log) -and (Select-String -Path $log"
                " -Pattern '^exit ' -Quiet))) { Start-Sleep -Milliseconds 500 };"
                " Unregister-ScheduledTask -TaskName e2e-probe -Confirm:$false;"
                " $text = Get-Content $log -ErrorAction Ignore; $text | Where-Object { $_ -notmatch '^exit ' };"
                " $last = $text | Select-String '^exit (\\d+)' | Select-Object -Last 1;"
                " if (-not $last) { 'the probe did not finish in the user session'; exit 3 };"
                " exit [int]$last.Matches[0].Groups[1].Value" % args,
            ],
            120,
        )
        return code, out + err

    def check_gpu(self, wid):
        """Windows: Direct3D 11 renders on the virtio GPU's adapter, which drives the desktop
        (Win32_VideoController has its mode); then, in a process of its own, Vulkan lists Venus; then
        one process does both, Direct3D first."""
        checks = [
            ("gpu", "", True),
            ("vulkan", "--vulkan", False),
            ("d3d_vulkan", "--d3d-vulkan", False),
        ]
        with open(os.path.join(self.dir, "gpu.txt"), "w") as f:
            for name, args, display in checks:
                try:
                    code, out = self.probe(wid, args, display)
                except Failure as e:
                    self.check(name, False, str(e))
                    continue
                f.write("$ e2e-d3d11.exe %s\n%s\nexit %s\n" % (args, out, code))
                lines = [line.strip() for line in out.splitlines() if line.strip()]
                detail = "; ".join(lines)[:400] if lines else "code %s" % code
                if name == "vulkan":
                    self.check(name, code == 0 and "PASS vulkan Venus" in lines, detail)
                    continue
                if name == "d3d_vulkan":
                    ok = "PASS triangle" in " ".join(lines) and "PASS vulkan Venus" in lines
                    self.check(name, code == 0 and ok, detail)
                    continue
                virtio = [
                    line
                    for line in lines
                    if line.startswith("display ") and "VirtIO" in line and ", OK, " in line
                ]
                ok = code == 0 and any("vendor 1af4" in line for line in lines) and bool(virtio)
                self.check(name, ok and not virtio[0].endswith(", x"), detail)

    def check_computer(self, wid):
        """Windows: computer use opens Notepad (keys from the host's keyboard device), types text with
        characters no US key types (the agent's helper in the user's session) and copies it back
        (Control+A, Control+C), which the clipboard op reads; text the clipboard op writes pastes into
        it; the wallpaper op sets one of the share's designs for the signed-in user."""
        viewport = self.client.request("computer", wait=60, workspace=wid)["batch"]["viewport"]

        def batch(actions, tag):
            r = self.client.request(
                "computer", wait=120, workspace=wid, actions=actions, viewport=viewport
            )["batch"]
            if r.get("screenshot"):
                self.keep_screenshot(r, tag)
            if not r.get("ok"):
                raise Failure(
                    "%s: %s (%s actions done)" % (tag, r.get("error"), r.get("completed_actions"))
                )

        def clipboard():
            return self.client.request("clipboard", wait=30, workspace=wid, action="read")["text"]

        # Escape first: Notepad's first edit opens a tip (autosave) that takes the keyboard focus.
        copy = [
            {"type": "wait", "milliseconds": 1000},
            {"type": "keypress", "keys": ["Escape"]},
            {"type": "keypress", "keys": ["Control_L", "a"]},
            {"type": "keypress", "keys": ["Control_L", "c"]},
            {"type": "wait", "milliseconds": 500},
        ]
        try:
            batch(
                [
                    {"type": "keypress", "keys": ["Super_L", "r"]},
                    {"type": "wait", "milliseconds": 1500},
                    {"type": "type", "text": "notepad"},
                    {"type": "keypress", "keys": ["Return"]},
                ],
                "notepad",
            )
            # Typed text needs Notepad's window: on a fresh install's first sign-in Windows may take a
            # minute to draw it. The probe, in the user's session, waits until it has the focus.
            code, out = self.probe(wid, "--foreground notepad.exe")
            if code != 0:
                raise Failure("Notepad: " + " ".join(out.split())[:300])
            batch([{"type": "type", "text": TYPED}] + copy, "typed")
            text = clipboard()
            self.check("typing", text == TYPED, "%s; %s" % (out.strip()[:80], ascii(text)[:300]))
        except Failure as e:
            self.check("typing", False, str(e)[:400])
            self.check("clipboard", None, "skipped: no Notepad to paste into")
        else:
            try:
                self.client.request(
                    "clipboard", wait=30, workspace=wid, action="write", text=PASTED
                )
                back = clipboard()
                batch(
                    [
                        {"type": "keypress", "keys": ["Escape"]},
                        {"type": "keypress", "keys": ["Control_L", "a"]},
                        {"type": "keypress", "keys": ["Control_L", "v"]},
                        {"type": "type", "text": "!"},
                    ]
                    + copy,
                    "pasted",
                )
                text = clipboard()
                self.check(
                    "clipboard",
                    back == PASTED and text == PASTED + "!",
                    "read back %s, pasted %s" % (ascii(back)[:120], ascii(text)[:120]),
                )
            except Failure as e:
                self.check("clipboard", False, str(e)[:400])
        try:
            self.client.request("wallpaper", wait=60, workspace=wid, design="satin")
            code, out, err = self.exec(
                wid,
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-ChildItem Registry::HKEY_USERS | ForEach-Object { (Get-ItemProperty"
                    " ('Registry::' + $_.Name + '\\Control Panel\\Desktop') -ErrorAction Ignore).Wallpaper }",
                ],
            )
            batch(
                [
                    {"type": "keypress", "keys": ["Super_L", "d"]},
                    {"type": "wait", "milliseconds": 1500},
                ],
                "wallpaper",
            )
            self.check(
                "wallpaper",
                code == 0 and "\\Sentinel\\Wallpapers\\satin-" in out,
                " ".join((out + err).split())[:300],
            )
        except Failure as e:
            self.check("wallpaper", False, str(e)[:400])

    def edge_points(self, width, height):
        e, r, b = EDGE, width - 1 - EDGE, height - 1 - EDGE
        cx, cy = width // 2, height // 2
        return [
            ("center", cx, cy),
            ("top-left", e, e),
            ("top", cx, e),
            ("top-right", r, e),
            ("right", r, cy),
            ("bottom-right", r, b),
            ("bottom", cx, b),
            ("bottom-left", e, b),
            ("left", e, cy),
        ]

    def check_cursor(self, wid, width, height):
        """What a viewer sees of the cursor: a shape packet (hardware cursor) or the frame's pixels."""
        v = self.viewer
        t0 = time.time()
        points = [(x, y) for _, x, y in self.edge_points(width, height)] + [
            (width // 2, height // 2)
        ]
        glide(v, points)
        time.sleep(0.6)
        events = v.since(t0)
        cursors = [e for e in v.since(0) if e.get("type") == "cursor"]
        moving = [e for e in events if e.get("type") == "cursor"]
        frames = sum(1 for e in events if e.get("type") == "frame")
        latest = cursors[-1] if cursors else None
        for i, c in enumerate(c for c in cursors if not c["hidden"]):
            if i < 4 and c.get("rgba"):
                write_png(
                    os.path.join(self.dir, "cursor-%d.png" % i),
                    c["width"],
                    c["height"],
                    base64.b64decode(c["rgba"]),
                )
        self.timings["frames_while_moving"] = frames
        visible = latest and not latest["hidden"] and latest["opaque"] >= 16
        if cursors:
            self.cursor_mode = "hw"
            first = next((i for i, e in enumerate(moving) if not e["hidden"]), None)
            hides = (
                [e for e in moving[first:] if e["hidden"] or e["opaque"] < 16]
                if first is not None
                else []
            )
            shape = (
                "none"
                if not latest
                else (
                    "hidden"
                    if latest["hidden"]
                    else "%dx%d, %d opaque px"
                    % (latest["width"], latest["height"], latest["opaque"])
                )
            )
            self.check("cursor", bool(visible), "hardware cursor: " + shape)
            self.check(
                "cursor_moving",
                bool(visible) and not hides,
                "%d cursor packets while moving, %d hidden/transparent, %d frames"
                % (len(moving), len(hides), frames),
            )
            return
        # No cursor packet: the guest draws the pointer into the frame; find it there at each point.
        self.cursor_mode = "sw"
        missing = []
        far = {}
        for label, x, y in self.edge_points(width, height):
            glide(
                v,
                [(width // 2 + 300 if x < width // 2 else width // 2 - 300, height // 2), (x, y)],
                step=60,
            )
            time.sleep(0.4)
            at, _ = self.snapshot(wid, "cursor-%s" % label)
            ox = width // 2 - 300 if x < width // 2 else width // 2 + 300
            glide(v, [(x, y), (ox, height // 2)], step=60)
            time.sleep(0.4)
            away, _ = self.snapshot(wid, "away-%s" % label)
            n = changed(at, away, (x - 6, y - 6, x + 36, y + 36))
            far[label] = n
            # At the bottom and right edges the arrow is off screen but for its tip: a few pixels.
            clipped = min(height, y + 36) - y < 20 or min(width, x + 36) - x < 20
            if n < (3 if clipped else 12):
                missing.append(label)
        self.check(
            "cursor",
            len(missing) < len(far),
            "software cursor drawn in the frame (no cursor packets)",
        )
        self.check(
            "cursor_moving",
            not missing,
            (
                "cursor not found at: %s; %d frames while moving"
                % (", ".join(missing) or "-", frames)
                if missing
                else "found at all %d points; %d frames while moving" % (len(far), frames)
            ),
        )

    def check_pointer(self, wid, width, height):
        """Where the desktop puts the pointer and the clicks: a full-screen X window in the guest."""
        if self.pointer_probe(wid, width, height, "override") == "retry":
            self.notes.append(
                "the override-redirect probe window never showed: probed with a managed window"
            )
            self.pointer_probe(wid, width, height, "managed")

    def pointer_probe(self, wid, width, height, mode):
        v = self.viewer
        with open(os.path.join(HERE, "probe_x11.py")) as f:
            source = f.read()
        if self.desktop == "gnome" and mode == "override":
            # GNOME opens its session in the overview, which hides windows mapped meanwhile: a click on
            # the workspace leaves it (on the plain desktop the click does nothing).
            glide(v, [(width // 2 - 20, height // 2 - 20), (width // 2, height // 2)])
            v.button(272, True)
            v.button(272, False)
            time.sleep(1.5)
        try:
            probe = process_open(self.daemon, wid, ["python3", "-c", source, "90", mode])
        except Failure as e:
            self.check("pointer", False, str(e))
            self.check("clicks", False, "no probe")
            return
        lines, buf = [], b""
        stop = threading.Event()

        def read():
            nonlocal buf
            data = b""
            sock = probe.sock
            sock.settimeout(1)
            pending = probe.buf
            while not stop.is_set():
                try:
                    chunk = pending or sock.recv(65536)
                    pending = b""
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                data += chunk
                while len(data) >= 5:
                    kind, n = data[0], struct.unpack(">I", data[1:5])[0]
                    if len(data) < 5 + n:
                        break
                    body, data = data[5 : 5 + n], data[5 + n :]
                    if kind == 1:
                        buf += body
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            try:
                                lines.append((time.time(), json.loads(line)))
                            except ValueError:
                                lines.append(
                                    (time.time(), {"output": line.decode("utf-8", "replace")})
                                )
                    elif kind == 3:
                        lines.append((time.time(), {"exit": body.decode("utf-8", "replace")}))
                        return

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            deadline = time.time() + 20
            while time.time() < deadline and not any(
                "ready" in entry or "error" in entry or "exit" in entry for _, entry in lines
            ):
                time.sleep(0.1)
            ready = next((entry for _, entry in lines if "ready" in entry), None)
            if not ready:
                why = next(
                    (
                        entry.get("error") or entry.get("exit")
                        for _, entry in lines
                        if "error" in entry or "exit" in entry
                    ),
                    "timeout",
                )
                out = " ".join(entry.get("output", "") for _, entry in lines)[-300:]
                self.check("pointer", False, "probe did not start: %s %s" % (why, out))
                self.check("clicks", False, "no probe")
                return
            # The window must reach the screen. GNOME opens its session in the overview, which hides
            # windows: Escape leaves it.
            magenta = (255, 0, 255)
            for attempt in range(6):
                time.sleep(1.0)
                img, path = self.snapshot(wid, "probe")
                shown = img.fraction(
                    magenta, (width // 4, height // 4, 3 * width // 4, 3 * height // 4)
                )
                if shown >= 0.9 or attempt == 5:
                    break
                os.remove(path)
                if attempt % 2 == 0:  # a click on the overview's workspace, then Escape
                    glide(v, [(width // 2 - 20, height // 2 - 20), (width // 2, height // 2)])
                    v.button(272, True)
                    v.button(272, False)
                else:
                    v.key(1, True)
                    v.key(1, False)
            if shown < 0.9 and mode == "override":
                return "retry"
            if shown < 0.9:
                self.check(
                    "pointer", False, "the probe window never showed (%s)" % os.path.basename(path)
                )
                self.check("clicks", None, "no probe window")
                return
            seen_before = len(lines)
            # The probe's output arrives through the agent in chunks: events are matched in order (each
            # point's motion, then its press), not by arrival time.
            errors, misses = [], []
            seen = seen_before
            panels = []
            for label, x, y in self.edge_points(width, height):
                # A panel above every window (a layer-shell surface, which no X window covers) takes the
                # pointer there: the point moves inward, past it, onto the probe.
                x, y, moved = self.uncovered(img, x, y, width, height, magenta)
                if moved:
                    panels.append("%s at %d,%d" % (label, x, y))
                glide(
                    v,
                    [
                        (x + (40 if x < width // 2 else -40), y + (40 if y < height // 2 else -40)),
                        (x, y),
                    ],
                    step=10,
                )
                time.sleep(0.3)
                v.button(272, True)
                time.sleep(0.05)
                v.button(272, False)
                deadline = time.time() + 5
                while time.time() < deadline and not any(
                    entry.get("event") == "release" for _, entry in lines[seen:]
                ):
                    time.sleep(0.05)
                got = [entry for _, entry in lines[seen:]]
                end = next(
                    (i for i, entry in enumerate(got) if entry.get("event") == "release"),
                    len(got) - 1,
                )
                got = got[: end + 1]
                seen += len(got)
                motion = [entry for entry in got if entry.get("event") == "motion"]
                press = next((entry for entry in got if entry.get("event") == "press"), None)
                if motion:
                    m = motion[-1]
                    if abs(m["x"] - x) > 2 or abs(m["y"] - y) > 2:
                        errors.append("%s sent %d,%d at %d,%d" % (label, x, y, m["x"], m["y"]))
                else:
                    errors.append("%s: no motion" % label)
                if not press:
                    misses.append("%s: no click" % label)
                elif abs(press["x"] - x) > 2 or abs(press["y"] - y) > 2:
                    misses.append(
                        "%s sent %d,%d landed %d,%d" % (label, x, y, press["x"], press["y"])
                    )
            with open(os.path.join(self.dir, "probe.jsonl"), "w") as f:
                for t, entry in lines:
                    f.write(json.dumps(dict(entry, t=round(t, 3))) + "\n")
            under = " (panels: moved %s)" % ", ".join(panels) if panels else ""
            self.check(
                "pointer",
                not errors,
                (
                    "; ".join(errors)
                    if errors
                    else "pointer at all %d points" % len(self.edge_points(width, height))
                )
                + under,
            )
            self.check(
                "clicks", not misses, "; ".join(misses) if misses else "clicks landed at all points"
            )
        finally:
            stop.set()
            probe.close()
            reader.join(3)

    @staticmethod
    def uncovered(img, x, y, width, height, color):
        """The nearest point toward the centre where the probe window shows (at most 200 px in)."""

        def shows(px, py):
            return all(
                all(
                    abs(a - b) <= 40
                    for a, b in zip(
                        img.px(min(max(px + dx, 0), width - 1), min(max(py + dy, 0), height - 1)),
                        color,
                    )
                )
                for dx in (-2, 0, 2)
                for dy in (-2, 0, 2)
            )

        cx, cy = width // 2, height // 2
        for step in range(0, 201, 4):
            px = x + (step if x < cx else -step if x > cx else 0)
            py = y + (step if y < cy else -step if y > cy else 0)
            if shows(px, py):
                return px, py, step > 0
        return x, y, False

    def check_stop_restart(self, wid):
        if self.viewer:
            self.viewer.close()
            self.viewer = None
        try:
            seconds, rec = self.stop(wid)
            self.timings["stop_s"] = seconds
            # sentineld powers the guest off itself after the distribution's grace (shutdown_s).
            grace = self.run.recipes.get(self.distribution, {}).get("shutdown_s", 15)
            forced = seconds >= grace - 0.5
            limit = LIMITS["stop_windows_s" if self.windows else "stop_s"]
            self.check(
                "stop",
                rec["state"] == "stopped" and seconds <= limit and not forced,
                "%.1f s, %s" % (seconds, rec["state"])
                + (" (forced power off after %g s)" % grace if forced else ""),
            )
        except Failure as e:
            self.check("stop", False, str(e))
            self.evidence(wid, "stop")
            return
        # The root as the guest left it, before the next start grows it (image::growRoot needs it clean).
        root_state = None if self.windows else root_superblock(self.daemon.workspace_dir(wid))
        try:
            t = self.start(wid)
            self.timings["restart_s"] = t["start_s"]
            self.check("restart", True, "%.0f s" % t["start_s"])
        except Failure as e:
            self.check("restart", False, str(e)[:400])
            self.evidence(wid, "restart")
            return
        if self.windows:
            self.check("fs_clean", None, "Linux only")
        else:
            # "orphan cleanup on readonly fs" is not a finding: ext4 prints it at every read-only mount of
            # a file system with an orphan file (mke2fs's default since e2fsprogs 1.47), orphans or not.
            # Orphans actually found say "orphan inode(s) deleted" or "truncate(s) cleaned up".
            code, out, _ = self.sh(
                wid,
                "dmesg 2>/dev/null | grep -iE 'EXT4-fs.*(recover|orphan|error|fsck|errors)|"
                "journal.*(replay|recover)' | grep -v 'orphan cleanup on readonly fs' || true",
            )
            bad = [entry for entry in out.splitlines() if entry.strip()]
            if root_state and root_state != "clean":
                bad.insert(0, "after the stop, root superblock: " + root_state)
            self.check(
                "fs_clean",
                not bad,
                (
                    "; ".join(bad)[:300]
                    if bad
                    else "root superblock clean after the stop, no ext4 recovery in dmesg"
                ),
            )
        if self.desktop != "none":
            self.check_session(wid, "session_restart")
        try:
            seconds, rec = self.stop(wid)
            self.timings["stop2_s"] = seconds
        except Failure as e:
            self.notes.append("second stop: %s" % e)

    def check_reuse(self):
        """A second workspace like the first starts from the cached layer: no install, fast."""
        if self.run.args.no_reuse:
            self.check("reuse", None, "skipped")
            return
        if not self.disk_ok("reuse"):
            return
        wid = self.create("-2")
        try:
            t = self.start(wid)
            self.timings["reuse_start_s"] = t["start_s"]
            fresh = [k for k in ("install_s", "layer_s") if k in t]
            ok = not fresh and t["start_s"] <= LIMITS["reuse_start_s"]
            self.check(
                "reuse",
                ok,
                "%.0f s%s"
                % (t["start_s"], ", installed again: " + ", ".join(fresh) if fresh else ""),
            )
            if self.desktop != "none":
                self.check_session(wid, "session_reuse")
        except Failure as e:
            self.check("reuse", False, str(e)[:400])
            self.evidence(wid, "reuse")
        finally:
            self.delete(wid)


# ---------------------------------------------------------------------------------------------
# The run: sources seeded, the daemon up, cases scheduled within the host's means, the report.


def root_superblock(workspace_dir):
    """The ext4 root disk's state as the guest left it: "clean", or what is wrong (None: no root disk).

    Clean is what image::growRoot needs to grow it: unmounted cleanly, no errors, no journal to replay,
    and no orphan inodes left (neither the old orphan list nor the orphan file's "orphan_present").
    """
    try:
        with open(os.path.join(workspace_dir, "root.raw"), "rb") as f:
            f.seek(1024)
            sb = f.read(1024)
    except OSError:
        return None
    if len(sb) < 1024 or struct.unpack_from("<H", sb, 0x38)[0] != 0xEF53:
        return "not an ext4 superblock"
    (state,) = struct.unpack_from("<H", sb, 0x3A)
    incompat, ro_compat = struct.unpack_from("<II", sb, 0x60)
    (last_orphan,) = struct.unpack_from("<I", sb, 0xE8)
    wrong = []
    if not state & 0x1:
        wrong.append("not cleanly unmounted")
    if state & 0x2:
        wrong.append("errors")
    if incompat & 0x4:
        wrong.append("journal needs recovery")
    if last_orphan or ro_compat & 0x10000:
        wrong.append("orphan inodes")
    return ", ".join(wrong) or "clean"


def host_budget():
    """How many workspaces may run at once: free memory and disk, with headroom for the host."""
    total = (
        int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
        / GiB
    )
    # macOS's own count of what it can hand out without pressure (reclaimable file cache included).
    out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True).stdout
    try:
        mem = total * int(out.rsplit(":", 1)[1].strip().rstrip("%")) / 100
    except (IndexError, ValueError):
        mem = total / 4
    disk = shutil.disk_usage(SENTINELD).free / GiB
    cpus = os.cpu_count() or 4
    jobs = int(max(1, min(cpus // 3, (mem - 2) // 2.5, (disk - 25) // 8)))
    return jobs, mem, disk


def recipes(base):
    """Each distribution.json in the distributions folder, by distribution id."""
    out = {}
    for d in sorted(os.listdir(base)):
        try:
            with open(os.path.join(base, d, "distribution.json")) as f:
                out[d] = json.load(f)
        except (OSError, ValueError):
            pass
    return out


def pinned_sources(base, only=()):
    out = {}
    for d in sorted(os.listdir(base)):
        if only and d not in only:
            continue
        try:
            with open(os.path.join(base, d, "distribution.json")) as f:
                recipe = json.load(f).get("install") or {}
        except (OSError, ValueError):
            continue
        for name, source in (recipe.get("sources") or {}).items():
            out[source["sha256"]] = (d, name)
    return out


def clone(src, dst):
    """An APFS clone (cp -c): instant, no extra space."""
    tmp = dst + ".e2e-part"
    if subprocess.run(["cp", "-c", src, tmp], stderr=subprocess.DEVNULL).returncode != 0:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


class Run:
    def __init__(self, args):
        self.args = args
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = os.path.join(args.out, "runs", stamp)
        os.makedirs(self.run_dir, exist_ok=True)
        latest = os.path.join(args.out, "runs", "latest")
        try:
            if os.path.islink(latest):
                os.remove(latest)
            os.symlink(stamp, latest)
        except OSError:
            pass
        self.lock = threading.Lock()
        self.created = set()
        self.daemon = None
        self.cases = []
        self.pinned = pinned_sources(args.distributions, args.distros)
        self.recipes = recipes(args.distributions)
        self.mirror_stop = threading.Event()

    def seed_sources(self):
        """Pinned install sources into the run's cache: from the mirror (and --seed folders)."""
        target = os.path.join(self.args.cache, "sources")
        os.makedirs(target, exist_ok=True)
        os.makedirs(self.args.sources, exist_ok=True)
        for sha, (dist, name) in self.pinned.items():
            if os.path.exists(os.path.join(target, sha)):
                continue
            for folder in [self.args.sources] + self.args.seed:
                src = os.path.join(folder, sha)
                if os.path.isfile(src):
                    log("source %s/%s from %s" % (dist, name, folder))
                    clone(src, os.path.join(target, sha))
                    break

    def mirror_sources(self):
        """sentineld removes a source once installed: keep each verified download for the next run."""
        target = os.path.join(self.args.cache, "sources")
        while not self.mirror_stop.wait(2):
            for sha in self.pinned:
                src, dst = os.path.join(target, sha), os.path.join(self.args.sources, sha)
                if os.path.isfile(src) and not os.path.exists(dst):
                    try:
                        clone(src, dst)
                    except OSError:
                        pass

    def select(self):
        dists = self.args.distros or list(self.distributions)
        unknown = [d for d in dists if d not in self.distributions]
        if unknown:
            raise SystemExit(
                "unknown distributions: %s (have %s)"
                % (", ".join(unknown), ", ".join(self.distributions))
            )
        cases = []
        for d in dists:
            # Windows' desktop is the OS's own: no desktop-less case.
            if (
                not windows(d)
                and not self.args.no_plain
                and (not self.args.desktops or "none" in self.args.desktops)
            ):
                cases.append((d, "none"))
            cases += [
                (d, k["id"])
                for k in self.desktops
                if d in k["distributions"]
                and (not self.args.desktops or k["id"] in self.args.desktops)
            ]
        # Round-robin across distributions: each distribution builds one layer at a time.
        order, queues = [], [[c for c in cases if c[0] == d] for d in dists]
        while any(queues):
            for q in queues:
                if q:
                    order.append(q.pop(0))
        return order

    def main(self):
        args = self.args
        jobs, mem, disk = host_budget()
        if args.jobs:
            jobs = args.jobs
        log("host: %.1f GiB memory free, %.0f GiB disk free -> %d jobs" % (mem, disk, jobs))
        if disk < args.min_disk:
            log("only %.0f GiB of disk free (< %d): every case is skipped" % (disk, args.min_disk))
        self.seed_sources()
        self.daemon = Daemon(args, self.run_dir)
        interrupted = False
        try:
            self.daemon.start()
            c = client_for(self.daemon)
            catalog = c.request("catalog")
            c.close()
            self.distributions = {d["id"]: d for d in catalog["distributions"]}
            self.desktops = catalog["desktops"]
            order = self.select()
            log("cases: %s" % ", ".join("%s/%s" % c for c in order))
            threading.Thread(target=self.mirror_sources, daemon=True).start()
            queue = list(order)
            threads = []

            def worker():
                while True:
                    with self.lock:
                        if not queue:
                            return
                        d, k = queue.pop(0)
                    case = Case(self, d, k)
                    with self.lock:
                        self.cases.append(case)
                    t0 = time.time()
                    case.run_case()
                    case.timings["case_s"] = round(time.time() - t0, 1)
                    self.write_report()

            for _ in range(min(jobs, len(order))):
                t = threading.Thread(target=worker, daemon=True)
                t.start()
                threads.append(t)
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(0.5)
        except KeyboardInterrupt:
            interrupted = True
            log("interrupted: cleaning up")
        finally:
            self.mirror_stop.set()
            self.daemon.shutdown(self.created)
            self.tidy()
        return self.finish(interrupted)

    def tidy(self):
        """Keeps what the next run reuses: the sources mirror, the package caches and the installed bases
        and layers (sentineld --keep-images). The cache's sources are clones of the mirror's; failed
        install disks were for this run's evidence (copied already).
        """
        for part in ("install", "sources"):
            shutil.rmtree(os.path.join(self.args.cache, part), ignore_errors=True)
        runs = os.path.join(self.args.out, "runs")
        old = sorted(
            d for d in os.listdir(runs) if d != "latest" and os.path.isdir(os.path.join(runs, d))
        )
        for d in old[: -self.args.keep_runs]:
            shutil.rmtree(os.path.join(runs, d), ignore_errors=True)

    def results(self):
        out = []
        for c in list(self.cases):
            out.append(
                {
                    "distribution": c.distribution,
                    "desktop": c.desktop,
                    "checks": c.checks,
                    "timings": c.timings,
                    "cursor_mode": getattr(c, "cursor_mode", None),
                    "notes": c.notes,
                    "evidence": os.path.relpath(c.dir, self.run_dir),
                    "passed": not c.failed() and bool(c.checks),
                }
            )
        return out

    def write_report(self):
        with self.lock:
            report = {
                "started": os.path.basename(self.run_dir),
                "host": {"jobs": self.args.jobs},
                "limits": LIMITS,
                "cases": self.results(),
            }
            with open(os.path.join(self.run_dir, "report.json.part"), "w") as f:
                json.dump(report, f, indent=1)
            os.replace(
                os.path.join(self.run_dir, "report.json.part"),
                os.path.join(self.run_dir, "report.json"),
            )

    def finish(self, interrupted):
        self.write_report()
        rows = self.results()
        shown = [c for c in CHECKS if any(c in r["checks"] for r in rows)]
        head = (
            ["distro", "desktop"]
            + shown
            + ["install_s", "layer_s", "start_s", "restart_s", "stop_s", "reuse_s"]
        )
        table = [head]
        for r in rows:
            cells = [r["distribution"], r["desktop"]]
            for c in shown:
                ch = r["checks"].get(c)
                if c.startswith("session") and ch is not None:
                    extra = [
                        r["checks"].get(k)
                        for k in ("session_restart", "session_reuse")
                        if k in r["checks"]
                    ]
                    if any(e["ok"] is False for e in extra):
                        ch = {"ok": False}
                cells.append(
                    "-" if ch is None or ch["ok"] is None else "ok" if ch["ok"] else "FAIL"
                )
            if r["checks"].get("cursor", {}).get("ok") is not None and r["cursor_mode"]:
                i = 2 + shown.index("cursor")
                cells[i] += "(%s)" % r["cursor_mode"]
            for k in ("install_s", "layer_s", "start_s", "restart_s", "stop_s", "reuse_start_s"):
                cells.append("%.0f" % r["timings"][k] if k in r["timings"] else "-")
            table.append(cells)
        widths = [max(len(row[i]) for row in table) for i in range(len(head))]
        text = "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) for row in table)
        failures = []
        for r in rows:
            for name, ch in r["checks"].items():
                if ch["ok"] is False:
                    failures.append(
                        "%s/%s %s: %s" % (r["distribution"], r["desktop"], name, ch["detail"])
                    )
        text += "\n\ntimes in seconds; evidence and report.json in %s\n" % self.run_dir
        skipped = [
            "%s/%s %s: %s" % (r["distribution"], r["desktop"], name, ch["detail"])
            for r in rows
            for name, ch in r["checks"].items()
            if ch["ok"] is None and ch["detail"].startswith("skipped")
        ]
        if skipped:
            text += "\nskipped:\n" + "\n".join("  " + f for f in skipped) + "\n"
        if failures:
            text += "\nfailures:\n" + "\n".join("  " + f for f in failures) + "\n"
        with open(os.path.join(self.run_dir, "report.txt"), "w") as f:
            f.write(text)
        print(text)
        ok = not failures and not interrupted and rows and all(r["passed"] for r in rows)
        return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    build = os.path.join(SENTINELD, "build")
    out = os.path.join(build, "e2e")
    p.add_argument(
        "--distros", default=os.environ.get("DISTROS", ""), help="comma list (default: all)"
    )
    p.add_argument(
        "--desktops",
        default=os.environ.get("DESKTOPS", ""),
        help="comma list, 'none' for the desktop-less workspace (default: all)",
    )
    p.add_argument(
        "--jobs",
        type=int,
        default=int(os.environ.get("JOBS") or 0),
        help="workspaces at once (default: from free memory and disk)",
    )
    p.add_argument(
        "--out", default=out, help="runs/<time>/ reports and evidence, and the persistent caches"
    )
    p.add_argument(
        "--cache", default=None, help="sentineld --cache (default: OUT/cache, kept between runs)"
    )
    p.add_argument(
        "--sources", default=None, help="mirror of pinned install sources (default: OUT/sources)"
    )
    p.add_argument(
        "--seed", action="append", default=[], help="another folder of pinned sources to clone from"
    )
    p.add_argument("--sentineld", default=os.path.join(build, "bin", "sentineld"))
    p.add_argument("--share", default=os.path.join(build, "share"))
    p.add_argument(
        "--distributions",
        default=os.path.join(build, "distributions"),
        help="the distributions folder sentineld is given",
    )
    p.add_argument("--mke2fs", default=os.path.join(build, "tools", "mke2fs"))
    p.add_argument(
        "--workspace",
        default=os.path.join(REPO_PACKAGES, "sentinel-workspace", "build"),
        help="sentinel-workspace build tree",
    )
    p.add_argument("--viewer", default=os.path.join(build, "tests", "e2e-viewer"))
    p.add_argument(
        "--d3d11-probe",
        default=os.path.join(build, "tests", "e2e-d3d11.exe"),
        help="Windows GPU probe, put in the run's share",
    )
    p.add_argument(
        "--start-timeout",
        type=int,
        default=120 * 60,
        help="seconds for a first start (install + layer)",
    )
    p.add_argument("--no-reuse", action="store_true", help="skip the second-workspace check")
    p.add_argument(
        "--fresh-install",
        action="store_true",
        help="drop the cached installs first, so every distribution installs from scratch",
    )
    p.add_argument(
        "--min-disk",
        type=int,
        default=25,
        help="GiB of free disk below which no VM is built (skipped)",
    )
    p.add_argument("--keep-runs", type=int, default=5, help="evidence folders kept in OUT/runs")
    p.add_argument("--no-plain", action="store_true", help="skip the desktop-less workspaces")
    args = p.parse_args()
    args.distros = [d for d in args.distros.split(",") if d]
    args.desktops = [d for d in args.desktops.split(",") if d]
    args.cache = args.cache or os.path.join(args.out, "cache")
    args.sources = args.sources or os.path.join(args.out, "sources")
    for path in (
        args.sentineld,
        args.mke2fs,
        args.viewer,
        os.path.join(args.workspace, "bin", "sentinel-workspace"),
    ):
        if not os.path.exists(path):
            raise SystemExit(
                "missing %s: build first (make -C packages/sentineld e2e builds everything)" % path
            )
    # The workspace tree and the share are cloned for the run, so a rebuild meanwhile changes nothing
    # mid-run (a changed install recipe would install its base again).
    run = Run(args)
    snapshot = os.path.join(run.run_dir, "sentinel-workspace")
    os.makedirs(snapshot)
    for part in ("bin", "lib", "share"):
        subprocess.run(
            ["cp", "-cR", os.path.join(args.workspace, part), os.path.join(snapshot, part)],
            check=True,
        )
    subprocess.run(["cp", "-cR", args.share, os.path.join(run.run_dir, "share")], check=True)
    subprocess.run(
        ["cp", "-cR", args.distributions, os.path.join(run.run_dir, "distributions")], check=True
    )
    args.workspace, args.share = snapshot, os.path.join(run.run_dir, "share")
    args.distributions = os.path.join(run.run_dir, "distributions")
    if os.path.exists(args.d3d11_probe):  # the Windows guest finds it on its share disk
        shutil.copy(args.d3d11_probe, os.path.join(args.share, "windows", "e2e-d3d11.exe"))

    def terminate(*_):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(
        signal.SIGINT, terminate
    )  # also when started in the background, where it is ignored
    try:
        code = run.main()
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)
        shutil.rmtree(args.share, ignore_errors=True)
        shutil.rmtree(args.distributions, ignore_errors=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
