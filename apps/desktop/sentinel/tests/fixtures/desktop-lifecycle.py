"""Linux-only real descendant ownership/reaping regression; no desktop required."""

import fcntl
import importlib.machinery
import importlib.util
import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path

loader = importlib.machinery.SourceFileLoader("desktop_session", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)
session.own_descendants()

grandchild = """
import fcntl,os,signal,sys
f=open(sys.argv[1], 'w')
fcntl.flock(f,fcntl.LOCK_EX)
if sys.argv[3]=='ignore':signal.signal(signal.SIGTERM,signal.SIG_IGN)
os.write(int(sys.argv[2]),str(os.getpid()).encode()+b'\\n')
os.close(int(sys.argv[2]))
signal.pause()
"""
launcher = """
import signal,subprocess,sys
subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],
                 start_new_session=True,pass_fds=(int(sys.argv[3]),))
if sys.argv[4]=='late':signal.pause()
"""

for disposition in ("default", "ignore", "late"):
    with tempfile.TemporaryDirectory() as directory:
        lock_path = Path(directory) / "desktop.lock"
        read_fd, write_fd = os.pipe()
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                launcher,
                grandchild,
                str(lock_path),
                str(write_fd),
                disposition,
            ],
            start_new_session=True,
            pass_fds=(write_fd,),
        )
        os.close(write_fd)
        try:
            assert select.select([read_fd], [], [], 5)[0], "Grandchild startup timed out"
            pid = int(os.read(read_fd, 128))
            if disposition != "late":
                process.wait(timeout=5)
            status = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            expected_parent = process.pid if disposition == "late" else os.getpid()
            assert int(status[1]) == expected_parent, "Unexpected descendant ownership"
            assert (
                int(status[2]) == pid and int(status[3]) == pid
            ), "Fixture must escape launcher's group/session"
            with lock_path.open("r+") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("Fixture did not retain its desktop lock")
            session.stop_children([process], grace=0.1, kill_timeout=2)
            assert not Path(f"/proc/{pid}").exists(), "Descendant was not reaped"
            with lock_path.open("r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pass
            else:
                raise AssertionError("Owner still has unreaped children")
            print(
                json.dumps(
                    {
                        "disposition": disposition,
                        "adopted_sets_id_child": True,
                        "lock_released": True,
                        "reaped": True,
                    }
                ),
                flush=True,
            )
        finally:
            os.close(read_fd)
            session.stop_children([process], grace=0, kill_timeout=2)
