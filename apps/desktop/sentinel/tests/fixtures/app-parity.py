"""Opt-in disposable guest application test; never starts/reconfigures a desktop."""

import argparse
import base64
import contextlib
import ctypes
import hashlib
from functools import lru_cache
import io
import json
import os
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops


@lru_cache(maxsize=1)
def packaged_graphics_hashes(graphics):
    package_directory = graphics.parent / "packages/mesa"
    if (package_directory / "manifest.json").is_file():
        return native_mesa_hashes(package_directory)
    libraries = sorted(graphics.glob("libgallium-*.so"))
    if len(libraries) != 1:
        raise RuntimeError("Expected exactly one packaged virgl-only Gallium library")
    gallium = libraries[0].resolve()
    vendors = list(graphics.glob("libEGL_mesa.so*"))
    frontends = {library.resolve() for library in (vendors or list(graphics.glob("libEGL.so*")))}
    frontends.update(library.resolve() for library in graphics.glob("libGLX_mesa.so*"))
    if not vendors:
        # Alpine's non-GLVND libGL contains Mesa's X11 implementation.
        frontends.update(library.resolve() for library in graphics.glob("libGL.so*"))
    if not frontends:
        raise RuntimeError("No packaged Mesa EGL or GLX implementation")
    paths = {gallium, *frontends}
    expected = {}
    for path in paths:
        with path.open("rb") as source:
            expected[path] = hashlib.file_digest(source, "sha256").hexdigest()
    return gallium, frontends, expected, bool(vendors)


def native_mesa_hashes(directory):
    """Trust shipped APK bytes, never whatever Mesa happens to be in /usr/lib."""
    manifest = json.loads((directory / "manifest.json").read_text())
    if any(
        manifest.get(key) != value
        for key, value in (
            ("schema", 1),
            ("name", "mesa"),
            ("distribution", "alpine"),
            ("architecture", "aarch64"),
        )
    ):
        raise RuntimeError("Invalid native Mesa package manifest")
    required = {"mesa", "mesa-egl", "mesa-gl"}
    expected = {}
    seen = set()
    for entry in manifest["packages"]:
        name = entry["name"]
        if name not in required:
            continue
        filename = f"{name}-{manifest['version']}.apk"
        if name in seen or entry["apk"] != filename or Path(filename).name != filename:
            raise RuntimeError("Invalid native Mesa package identity")
        seen.add(name)
        with (directory / filename).open("rb") as archive:
            if hashlib.file_digest(archive, "sha256").hexdigest() != entry["sha256"]:
                raise RuntimeError("Native Mesa APK checksum mismatch")
            archive.seek(0)
            with tarfile.open(fileobj=archive, mode="r:gz", ignore_zeros=True) as contents:
                metadata = contents.extractfile(".PKGINFO").read().decode().splitlines()
                if (
                    f"pkgname = {name}" not in metadata
                    or f"pkgver = {manifest['version']}" not in metadata
                ):
                    raise RuntimeError("Native Mesa APK metadata mismatch")
                for member in contents:
                    path = Path(member.name)
                    if not member.isfile() or path.parent != Path("usr/lib"):
                        continue
                    if not (
                        path.name.startswith("libgallium-")
                        and path.name.endswith(".so")
                        or path.name.startswith(("libEGL.so.", "libGL.so."))
                    ):
                        continue
                    installed = Path("/") / path
                    if installed in expected:
                        raise RuntimeError("Duplicate native Mesa library ownership")
                    with contents.extractfile(member) as library:
                        expected[installed] = hashlib.file_digest(library, "sha256").hexdigest()
    gallium = [path for path in expected if path.name.startswith("libgallium-")]
    frontends = set(expected) - set(gallium)
    if (
        seen != required
        or len(gallium) != 1
        or not all(
            any(path.name.startswith(prefix) for path in frontends)
            for prefix in ("libEGL.so.", "libGL.so.")
        )
    ):
        raise RuntimeError("Incomplete packaged native Mesa implementation")
    return gallium[0], frontends, expected, False


def drm_driver_name(descriptor):
    class Version(ctypes.Structure):
        _fields_ = [
            ("major", ctypes.c_int),
            ("minor", ctypes.c_int),
            ("patch", ctypes.c_int),
            ("name_len", ctypes.c_size_t),
            ("name", ctypes.c_void_p),
            ("date_len", ctypes.c_size_t),
            ("date", ctypes.c_void_p),
            ("desc_len", ctypes.c_size_t),
            ("desc", ctypes.c_void_p),
        ]

    name = ctypes.create_string_buffer(64)
    version = Version(name_len=len(name), name=ctypes.addressof(name))
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    libc.ioctl.restype = ctypes.c_int
    request = (3 << 30) | (ctypes.sizeof(Version) << 16) | (ord("d") << 8)
    fd = os.open(descriptor, os.O_RDONLY | os.O_CLOEXEC)
    try:
        if libc.ioctl(fd, request, ctypes.byref(version)):
            raise OSError(ctypes.get_errno(), "DRM_IOCTL_VERSION failed")
        if version.name_len >= len(name):
            raise RuntimeError("DRM driver name exceeds evidence buffer")
        return name.raw[: version.name_len].decode("ascii")
    finally:
        os.close(fd)


def browser_kind(executable):
    path = executable.lower()
    if "firefox" in Path(path).name:
        return "firefox"
    if "chromium" in path:
        return "chromium"
    if "/opt/google/chrome/" in path or "google-chrome" in path:
        return "chrome"
    return None


def process_confinement(base, executable):
    status = dict(
        line.split(":", 1) for line in (base / "status").read_text().splitlines() if ":" in line
    )
    try:
        apparmor = (base / "attr/current").read_text().strip()
    except OSError:
        apparmor = None  # Native Alpine processes do not require AppArmor.
    confinement = {
        "uid": [int(uid) for uid in status["Uid"].split()],
        "seccomp": int(status["Seccomp"]),
        "apparmor": apparmor,
    }
    assert all(uid > 0 for uid in confinement["uid"]), confinement
    if executable.startswith("/snap/"):
        profile = (
            "snap.firefox.firefox"
            if browser_kind(executable) == "firefox"
            else "snap.chromium.chromium"
        )
        assert (
            apparmor and apparmor.startswith(profile + " ") and apparmor.endswith("(enforce)")
        ), confinement
    return confinement


def sandbox_thread_evidence(base):
    threads = []
    for task in sorted((base / "task").iterdir()):
        try:
            status = dict(
                line.split(":", 1)
                for line in (task / "status").read_text().splitlines()
                if ":" in line
            )
        except FileNotFoundError:
            continue  # A worker may finish during the snapshot.
        item = {
            "tid": int(task.name),
            "seccomp": int(status["Seccomp"]),
            "uid": [int(uid) for uid in status["Uid"].split()],
        }
        assert item["seccomp"] == 2 and all(uid > 0 for uid in item["uid"]), item
        threads.append(item)
    assert any(item["tid"] == int(base.name) for item in threads), "Process leader disappeared"
    return threads


def browser_graphics_evidence(
    root_pid, app, proc=Path("/proc"), graphics=Path("/opt/sentinel/graphics/lib")
):
    parents = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            parents[int(entry.name)] = int(
                (entry / "stat").read_text().rsplit(")", 1)[1].split()[1]
            )
        except (OSError, ValueError, IndexError):
            continue
    family = {root_pid}
    while True:
        children = {pid for pid, parent in parents.items() if parent in family}
        if children <= family:
            break
        family.update(children)
    gallium, frontends, expected, glvnd = packaged_graphics_hashes(graphics)
    evidence = []
    inaccessible = []
    sandboxed_content = []
    for pid in sorted(family):
        base = proc / str(pid)
        try:
            rows = (base / "maps").read_text().splitlines()
            arguments = [arg for arg in (base / "cmdline").read_bytes().split(b"\0") if arg]
            # Chromium's setproctitle joins argv with literal spaces, without
            # shell quoting. Preserve real argument boundaries when available.
            if len(arguments) == 1:
                arguments = arguments[0].split()
            command = b" ".join(arguments).decode(errors="replace").strip()
            executable = str((base / "exe").resolve(strict=True))
        except OSError as error:
            inaccessible.append({"pid": pid, "error": str(error)})
            continue
        kind = browser_kind(executable)
        confinement = process_confinement(base, executable) if kind else None
        if kind and {"--no-sandbox", "--disable-gpu-sandbox"}.intersection(command.split()):
            raise AssertionError(f"Browser sandbox bypass: {command}")
        if kind and kind != app:
            raise AssertionError(f"Requested {app}, but launched {kind}: {executable}")
        # Gecko appends its process type as the last launch argument. Other
        # helpers (RDD/socket/GPU) also use -contentproc and are not web content.
        if (
            kind == "firefox"
            and "-contentproc" in command.split()
            and command.split()[-1] == "tab"
            and confinement["seccomp"] == 2
        ):
            confinement["threads"] = sandbox_thread_evidence(base)
            sandboxed_content.append(
                {"pid": pid, "executable": executable, "confinement": confinement}
            )
        mappings = {
            parts[5]: parts
            for row in rows
            if len(parts := row.split(None, 5)) == 6 and parts[5].startswith("/")
        }
        paths = set(mappings)
        forbidden = (
            "swiftshader",
            "swrast_dri",
            "libvulkan_lvp",
            "libosmesa",
            "llvmpipe",
            "softpipe",
        )
        if any(any(name in path.lower() for name in forbidden) for path in paths):
            raise AssertionError(f"Browser loaded a software graphics library: {sorted(paths)}")
        loaded = {}
        dispatch = []
        for path in paths:
            name = Path(path).name
            if "libgallium" not in name and not name.startswith(
                ("libEGL.so", "libEGL_mesa.so", "libGL.so", "libGLX_mesa.so")
            ):
                continue
            if glvnd and name.startswith(("libEGL.so", "libGL.so")):
                dispatch.append(path)
                continue  # Distribution GLVND dispatch is not the Mesa implementation.
            # Resolve in the application's mount namespace (including Snap), and
            # verify the file is still the mapped inode before hashing its bytes.
            mapped = base / "root" / path.lstrip("/")
            with mapped.open("rb") as source:
                metadata = os.fstat(source.fileno())
                parts = mappings[path]
                major, minor = (int(value, 16) for value in parts[3].split(":"))
                if (metadata.st_ino, os.major(metadata.st_dev), os.minor(metadata.st_dev)) != (
                    int(parts[4]),
                    major,
                    minor,
                ):
                    raise AssertionError(f"Mapped graphics library changed: {path}")
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            candidates = {
                candidate: digest_value
                for candidate, digest_value in expected.items()
                if ("libgallium" in candidate.name) == ("libgallium" in name)
            }
            matches = [candidate for candidate, wanted in candidates.items() if wanted == digest]
            if not matches:
                raise AssertionError(f"Browser loaded an unqualified graphics library: {path}")
            loaded[str(matches[0])] = {"sha256": digest, "mapped_path": path}
        devices = []
        try:
            descriptors = list((base / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
                if not target.startswith("/dev/dri/"):
                    continue
                driver = drm_driver_name(descriptor)
                if driver != "virtio_gpu":
                    raise AssertionError(f"Unexpected browser DRM driver: {driver}")
                devices.append({"fd": int(descriptor.name), "path": target, "driver": driver})
            except (OSError, ValueError):
                continue
        if (
            str(gallium) in loaded
            and any(str(path) in loaded for path in frontends)
            and devices
            and kind
            and (
                kind == "firefox"
                or [arg for arg in arguments if arg.startswith(b"--type=")]
                == [b"--type=gpu-process"]
            )
        ):
            # Firefox can keep graphics in its unsandboxed parent. Its content
            # sandbox is checked separately. Chromium's parent and brokers can
            # also map Mesa/open DRM, but only its GPU process qualifies here.
            if app != "firefox":
                assert confinement["seccomp"] == 2, {
                    "pid": pid,
                    "command": command,
                    "confinement": confinement,
                }
                confinement["threads"] = sandbox_thread_evidence(base)
            evidence.append(
                {
                    "pid": pid,
                    "executable": executable,
                    "command": command,
                    "libraries": loaded,
                    "graphics_dispatch": dispatch,
                    "drm_devices": devices,
                    "confinement": confinement,
                }
            )
    if not evidence:
        raise RuntimeError(
            "No live browser process has packaged Mesa EGL/GLX, virgl-only Gallium and a virtio_gpu DRM descriptor; "
            f"inaccessible processes: {inaccessible}"
        )
    if app == "firefox" and not sandboxed_content:
        raise AssertionError("No live Firefox content process with an active seccomp sandbox")
    return {
        "method": "live-process-maps-and-drm-device",
        "processes": evidence,
        "sandboxed_content": sandboxed_content,
        "scope": "Packaged Gallium is built with virgl only; this is native process evidence, not an unmasked JavaScript renderer string",
    }


def main():
    def interrupted(number, _frame):
        raise TimeoutError(f"Application test interrupted by signal {number}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", choices=("blender", "firefox", "chromium", "chrome"))
    parser.add_argument("backend", choices=("x11", "wayland"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--browser-start", type=Path)
    parser.add_argument("--browser-stop", type=Path)
    args = parser.parse_args()
    automation = args.app in {"chromium", "chrome"}
    if automation and not (args.browser_start and args.browser_stop):
        parser.error("Chromium/Chrome require production --browser-start and --browser-stop")
    if automation:
        selection = Path("/etc/sentinel/browser-selection").read_text().strip()
        expected = "chrome" if selection == "chrome" else "chromium"
        if args.app != expected:
            parser.error(
                f"Production automation selection is {expected}, not {args.app}; provision a matching disposable workspace"
            )
    sys.path.insert(0, "/opt/sentinel/desktop")
    from sentinel_display import Desktop

    root = (args.output or Path(tempfile.mkdtemp(prefix="sentinel-app-parity-"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).resolve().parent
    session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
    user = session["user"]
    assert user["uid"] > 0 and user["gid"] > 0, "Desktop must publish an unprivileged user"
    environment = dict(session["environment"])
    assert environment.get("HOME"), "Desktop must publish the user's HOME"
    for key in (
        "MESA_GL_VERSION_OVERRIDE",
        "MESA_GLSL_VERSION_OVERRIDE",
        "MESA_EXTENSION_OVERRIDE",
        "LIBGL_ALWAYS_SOFTWARE",
        "MOZ_DISABLE_CONTENT_SANDBOX",
        "MOZ_DISABLE_RDD_SANDBOX",
        "MOZ_DISABLE_GPU_SANDBOX",
    ):
        if environment.get(key):
            raise RuntimeError(f"Forbidden graphics override: {key}")
    assert session["protocol"] == args.backend, "Requested backend differs from production session"
    if args.backend == "wayland":
        assert session["protocol"] == "wayland" and environment.get("WAYLAND_DISPLAY")
        environment.pop("DISPLAY", None)
    else:
        assert environment.get("DISPLAY"), "Session did not publish its X11 display"
        environment.pop("WAYLAND_DISPLAY", None)
    if args.app == "firefox":
        environment["MOZ_ENABLE_WAYLAND"] = "1" if args.backend == "wayland" else "0"
    credentials = {}
    if os.geteuid() == 0:
        os.chown(root, user["uid"], user["gid"])
        credentials = dict(user=user["uid"], group=user["gid"], extra_groups=user["groups"])
    else:
        assert (
            os.geteuid() == user["uid"] and os.getegid() == user["gid"]
        ), "Run as root or the desktop user"
        assert set(os.getgroups()) == set(
            user["groups"]
        ), "Supplementary groups differ from the desktop session"
    token = secrets.token_hex(16)
    condition = threading.Condition()
    state = {"sequence": 0, "report": {}, "advance": False, "done": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != "/browser-parity.html":
                self.send_error(404)
                return
            body = (fixtures / "browser-parity.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/report" or not 0 < length <= 16384:
                    raise ValueError("Invalid report size/path")
                value = json.loads(self.rfile.read(length))
                if value.pop("token", None) != token:
                    raise ValueError("Invalid test token")
                with condition:
                    state["report"] = value
                    state["sequence"] += 1
                    reply = json.dumps(
                        {"advance": state["advance"], "done": state["done"]}
                    ).encode()
                    condition.notify_all()
                self.send_response(200)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except (ValueError, OSError) as error:
                self.send_error(400, str(error))

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever)
    url = f"http://127.0.0.1:{server.server_port}"
    environment.update(
        {
            "SENTINEL_APP_ENDPOINT": url + "/report",
            "SENTINEL_APP_TOKEN": token,
            "SENTINEL_APP_OUTPUT": str(root),
        }
    )
    if args.app == "blender":
        command = [
            "blender",
            "--factory-startup",
            "--window-fullscreen",
            "--python",
            str(fixtures / "blender-parity.py"),
        ]
    elif not automation:
        # HOME is visible to confined browsers, unlike a private Snap /tmp.
        profile = Path(tempfile.mkdtemp(prefix="sentinel-app-parity-", dir=environment["HOME"]))
        # Mozilla's recommended automation preference suppresses first-run UI;
        # it does not record acceptance or change sandbox/graphics preferences.
        # Scope it to this disposable test profile, never the user's profile.
        preferences = profile / "user.js"
        preferences.write_text('user_pref("termsofuse.bypassNotification", true);\n')
        if os.geteuid() == 0:
            os.chown(profile, user["uid"], user["gid"])
            os.chown(preferences, user["uid"], user["gid"])
        page = url + "/browser-parity.html?token=" + token
        binary = shutil.which("firefox", path=environment.get("PATH")) or shutil.which(
            "firefox-esr", path=environment.get("PATH")
        )
        if not binary:
            raise RuntimeError("Firefox is not installed in this disposable workspace")
        command = [binary, "--no-remote", "--profile", str(profile), "--kiosk", page]
    desktop = None
    process = None
    browser = None
    descriptor = None
    request = None
    stopped = False
    result = {"app": args.app, "backend": args.backend, "status": "fail"}
    if args.app == "firefox":
        result["profile"] = str(profile)

    def worker(path):
        reply = subprocess.run(
            [sys.executable, str(path), json.dumps(request)],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if reply.returncode:
            raise RuntimeError(f"Browser worker failed: {reply.stderr} {reply.stdout}")
        payload = json.loads(reply.stdout)
        if payload.get("ok") is not True:
            raise RuntimeError(f"Browser worker rejected operation: {payload}")
        return payload

    def browser_exited():
        if automation:
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            return bool(poller.poll(0))
        return process.poll() is not None

    deadline = time.monotonic() + 90
    server_thread.start()
    try:
        if automation:
            control = Path("/var/lib/sentinel/control")
            control.mkdir(parents=True, exist_ok=True)
            owned = Path(tempfile.mkdtemp(prefix="qualification-pixels-", dir=control))
            request = {
                "browser": str(owned / "browser"),
                "runtime": str(owned / "runtime"),
                "logs": str(owned / "logs"),
                "display": "native",
                "geometry": "1280x800",
            }
            result["control"] = str(owned)
            browser = worker(args.browser_start)
            assert not browser["reused"] and browser["uid"] == user["uid"] > 0, browser
            descriptor = os.pidfd_open(browser["pid"])
            result["browser"] = browser
            page = url + "/browser-parity.html?token=" + token + "&fullscreen=gesture"
            endpoint = f"http://127.0.0.1:{browser['port']}"
            navigation = urllib.request.Request(
                endpoint + "/json/new?" + urllib.parse.quote(page, safe=""), method="PUT"
            )
            with urllib.request.urlopen(navigation, timeout=5) as response:
                target = json.load(response)
            with urllib.request.urlopen(endpoint + "/json/activate/" + target["id"], timeout=5):
                pass
            result["target"] = target["id"]
        else:
            with (root / "application.log").open("wb") as log:
                process = subprocess.Popen(
                    command,
                    env=environment,
                    stdout=log,
                    stderr=log,
                    cwd=environment["HOME"],
                    start_new_session=True,
                    **credentials,
                )
        desktop = Desktop()
        desktop.execute({"type": "move", "x": 8, "y": 8})
        if automation:
            with condition:
                if not condition.wait_for(
                    lambda: state["report"].get("visible") == "visible"
                    or "error" in state["report"],
                    timeout=20,
                ):
                    raise TimeoutError(
                        "Browser target never reported visible before fullscreen gesture"
                    )
                if "error" in state["report"]:
                    raise RuntimeError(state["report"]["error"])

        def capture(phase, fullscreen=True):
            stage = f"display-{phase}" if fullscreen else "fullscreen-setup"
            seen = -1
            last_mismatch = None
            last_report = None
            while time.monotonic() < deadline:
                with condition:
                    if not condition.wait_for(
                        lambda: state["sequence"] != seen,
                        min(5, max(0, deadline - time.monotonic())),
                    ):
                        if browser_exited():
                            raise RuntimeError("Application exited before pixel gate")
                        continue
                    seen = state["sequence"]
                    report = dict(state["report"])
                last_report = report
                if "error" in report:
                    raise AssertionError(report["error"])
                if report.get("phase") != phase:
                    continue
                renderer = report.get("renderer", "").lower()
                assert not any(
                    x in renderer for x in ("llvmpipe", "softpipe", "swiftshader")
                ), report
                if args.app != "blender":
                    result["native_graphics"] = browser_graphics_evidence(
                        browser["pid"] if automation else process.pid, args.app
                    )
                else:
                    assert "virgl" in renderer, report
                shot = desktop.screenshot()
                image = Image.open(
                    io.BytesIO(base64.b64decode(shot["screenshot"].split(",", 1)[1]))
                ).convert("RGB")
                width, height = image.size
                if args.app != "blender":
                    assert report["visible"] == "visible", report
                    if automation and fullscreen and not report.get("fullscreen"):
                        last_mismatch = (
                            report,
                            image,
                            [],
                            "Browser must enter fullscreen after setup click",
                        )
                        continue
                    if fullscreen and report.get("size") != [width, height]:
                        last_mismatch = (
                            report,
                            image,
                            [],
                            "Fullscreen viewport must match desktop",
                        )
                        continue
                    expected = [
                        ((255, 0, 0), (0, 255, 0)),
                        ((0, 0, 255), (255, 0, 0)),
                        ((0, 255, 0), (0, 0, 255)),
                    ][phase]
                    samples = [image.getpixel((width * x // 4, height // 2)) for x in (1, 3)]
                else:
                    x, y, rw, rh = report["region"]
                    ww, wh = report["window"]
                    point = (int((x + 48) * width / ww), int((wh - y - 48) * height / wh))
                    samples = [image.getpixel(point)]
                    expected = [(255, 0, 0) if phase == 0 else (0, 255, 0)]
                if all(
                    all(abs(a - b) <= 8 for a, b in zip(pixel, wanted))
                    for pixel, wanted in zip(samples, expected)
                ):
                    image.save(root / f"{stage}.png")
                    return report, image
                last_mismatch = (report, image, samples, expected)
            if last_mismatch:
                report, image, samples, expected = last_mismatch
                image.save(root / f"{stage}-failed.png")
                (root / f"{stage}-failed.json").write_text(
                    json.dumps(
                        {
                            "report": report,
                            "size": list(image.size),
                            "samples": samples,
                            "expected": expected,
                        },
                        indent=2,
                    )
                )
            elif last_report:
                (root / f"{stage}-failed.json").write_text(
                    json.dumps({"expected_phase": phase, "report": last_report}, indent=2)
                )
            if not last_mismatch:
                # A lock screen, startup dialog, or failed navigation can prevent
                # any page report. Preserve what was actually displayed too.
                shot = desktop.screenshot()
                Image.open(io.BytesIO(base64.b64decode(shot["screenshot"].split(",", 1)[1]))).save(
                    root / f"{stage}-failed.png"
                )
            raise TimeoutError(f"No correct presented pixels for phase {phase}")

        if automation:
            # DOM visibility and GPU readback can precede compositor presentation.
            # Wait for this page's real desktop pixels before the single setup
            # click; otherwise it can hit the wallpaper behind an unmapped window.
            # The production worker opens a desktop-sized window, so both sample
            # points lie inside its canvas even before removing browser chrome.
            ready, image = capture(0, fullscreen=False)
            result["fullscreen_setup"] = ready
            desktop.execute({"type": "click", "x": image.width // 2, "y": image.height // 2})

        first, image0 = capture(0)
        if args.app != "blender":
            desktop.execute({"type": "click", "x": image0.width // 8, "y": image0.height // 4})
            capture(1)
            desktop.execute({"type": "keypress", "keys": ["space"]})
            latest, _image = capture(2)
            result.update(click=True, key=True, exact_browser_pixels=latest["pixels"])
            result["input_events"] = latest.get("inputEvents", [])
        else:
            with condition:
                state["advance"] = True
            latest, image1 = capture(1)
            x, y, w, h = first["region"]
            ww, wh = first["window"]
            crop = (
                int((x + w * 0.25) * image0.width / ww),
                int((wh - y - h * 0.75) * image0.height / wh),
                int((x + w * 0.75) * image0.width / ww),
                int((wh - y - h * 0.25) * image0.height / wh),
            )
            difference = ImageChops.difference(image0.crop(crop), image1.crop(crop))
            changed = sum(max(pixel) > 16 for pixel in difference.getdata())
            assert changed >= 100, f"Viewport rotation changed only {changed} significant pixels"
            rendered = [Image.open(root / f"workbench-{i}.png").convert("RGB") for i in range(2)]
            assert all(
                any(low != high for low, high in img.getextrema()) for img in rendered
            ), "Uniform Workbench image"
            difference = ImageChops.difference(*rendered)
            assert (
                sum(max(pixel) > 16 for pixel in difference.getdata()) >= 100
            ), "Workbench rotation pixels unchanged"
            assert first["exact_gpu_pixels"] == 3844, first
            result.update(
                exact_gpu_pixels=first["exact_gpu_pixels"],
                viewport_rotation=True,
                workbench_rotation=True,
            )
            with condition:
                state["done"] = True
            assert process.wait(timeout=10) == 0, "Blender did not exit cleanly"
        assert args.app == "blender" or not browser_exited(), "Browser crashed after pixel gate"
        result.update(status="pass", renderer=latest["renderer"])
    except BaseException as error:
        result["error"] = str(error)
        if desktop:
            try:
                shot = desktop.screenshot()
                Image.open(io.BytesIO(base64.b64decode(shot["screenshot"].split(",", 1)[1]))).save(
                    root / "failure.png"
                )
            except Exception as diagnostic_error:
                result["screenshot_error"] = str(diagnostic_error)
        raise
    finally:
        original_error = sys.exception()
        cleanup_errors = []

        def cleanup(action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append(error)

        if automation and request:
            metadata = Path(request["runtime"]) / "browser.json"
            if metadata.exists():
                try:
                    worker(args.browser_stop)
                    stopped = True
                except Exception as error:
                    cleanup_errors.append(error)
            elif browser:
                cleanup_errors.append(
                    RuntimeError("Browser metadata disappeared before confirmed stop")
                )
            for log in Path(request["logs"]).glob("*.log"):
                cleanup(lambda log=log: shutil.copyfile(log, root / log.name))
            result["stopped"] = stopped
            # Preserve owned profiles/logs for qualification evidence. Only the
            # disposable workspace's eventual teardown removes them.
        if descriptor is not None:
            cleanup(lambda: os.close(descriptor))
        if desktop:
            cleanup(desktop.close)
        if process:

            def stop_process():
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

            cleanup(stop_process)
        cleanup(server.shutdown)
        cleanup(server.server_close)
        cleanup(lambda: server_thread.join(timeout=5))
        if cleanup_errors:
            result.update(status="fail", cleanup_errors=[str(error) for error in cleanup_errors])
        (root / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({**result, "output": str(root)}), flush=True)
        if cleanup_errors:
            if original_error is not None:
                original_error.add_note("Cleanup failures: " + "; ".join(map(str, cleanup_errors)))
            else:
                raise cleanup_errors[0]


if __name__ == "__main__":
    main()
