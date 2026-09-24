"""Bound one native login's output without blocking or killing its applications."""

import os
import select
import signal
import subprocess
import sys

LIMIT = 1024 * 1024
TAIL = 64 * 1024


def append(stream, data):
    if stream.tell() + len(data) > LIMIT:
        stream.seek(max(0, stream.tell() - TAIL))
        tail = stream.read(TAIL)
        stream.seek(0)
        stream.truncate()
        stream.write(tail)
    stream.write(data)


def run(path, command):
    os.umask(0o077)
    # The login owns a unique private directory. Never follow or replace an
    # existing file; greetd runs this wrapper as the ordinary desktop user.
    with open(path, "x+b", buffering=0) as log:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        def forward(signum, _frame):
            if child.poll() is None:
                child.send_signal(signum)

        previous = {
            sig: signal.signal(sig, forward)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        }
        try:
            while True:
                if not select.select([child.stdout], [], [], 0.25)[0]:
                    if child.poll() is not None:
                        break
                    continue
                data = os.read(child.stdout.fileno(), TAIL)
                if not data:
                    break
                try:
                    append(log, data)
                except OSError:
                    # A full filesystem must not deadlock the compositor on a
                    # full pipe. Keep draining; subsequent writes can recover.
                    pass
            return child.wait()
        finally:
            child.stdout.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    result = run(sys.argv[1], sys.argv[2:])
    sys.exit(result if result >= 0 else 128 - result)
