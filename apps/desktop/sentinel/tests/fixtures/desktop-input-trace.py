"""Read-only evdev correlation around the unchanged disposable GUI oracle.

Never grabs an input device or changes input timing. Run as the disposable
guest's root supervisor; desktop-contract.py drops privileges for its GUI.
"""

import json
import fcntl
import os
from pathlib import Path
import selectors
import struct
import subprocess
import sys
import time

event = struct.Struct("llHHi")
poller = selectors.DefaultSelector()
devices = []
records = []
initial = []
try:
    for node in sorted(Path("/dev/input").glob("event*")):
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        name = (Path("/sys/class/input") / node.name / "device/name").read_text().strip()
        keys = bytearray(128)
        fcntl.ioctl(fd, 0x80000000 | len(keys) << 16 | ord("E") << 8 | 0x18, keys)
        initial.append(
            {
                "path": str(node),
                "name": name,
                "pressed": [
                    code for code in range(len(keys) * 8) if keys[code // 8] & 1 << (code % 8)
                ],
            }
        )
        poller.register(fd, selectors.EVENT_READ, {"path": str(node), "name": name})
        devices.append(fd)
    child = subprocess.Popen(
        [sys.executable, *sys.argv[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    os.set_blocking(child.stdout.fileno(), False)
    poller.register(child.stdout, selectors.EVENT_READ, None)
    output = bytearray()
    while poller.get_map():
        ready = poller.select(timeout=1)
        for key, _ in ready:
            try:
                data = os.read(key.fd, 65520)
            except BlockingIOError:
                continue
            if key.data is None:
                if data:
                    output.extend(data)
                else:
                    poller.unregister(key.fileobj)
                continue
            now = time.monotonic_ns()
            for offset in range(0, len(data), event.size):
                seconds, micros, kind, code, value = event.unpack_from(data, offset)
                records.append(
                    {
                        **key.data,
                        "read_monotonic_ns": now,
                        "kernel_us": seconds * 1000000 + micros,
                        "type": kind,
                        "code": code,
                        "value": value,
                    }
                )
        if child.poll() is not None:
            break
    output.extend(child.stdout.read() or b"")
    print(
        json.dumps(
            {
                "exit_code": child.wait(),
                "initial": initial,
                "events": records,
                "oracle": output.decode(errors="replace"),
            }
        ),
        flush=True,
    )
finally:
    poller.close()
    for fd in devices:
        os.close(fd)
