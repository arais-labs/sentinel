"""Qualify cross-CPU namespace persistence without changing the guest session."""

import ctypes
import errno
import json
import os
import tempfile
from pathlib import Path

libc = ctypes.CDLL(None, use_errno=True)
CLONE_NEWNS = 0x20000
MS_BIND = 4096
MS_PRIVATE_RECURSIVE = (1 << 18) | (1 << 14)


def checked(result, operation):
    if result != 0:
        raise OSError(ctypes.get_errno(), operation)


# Native login mounts must reach application namespaces created before login.
# Check the boot-owned /run before isolating this fixture's test mounts.
runtime_mount = next(
    line.split()
    for line in Path("/proc/self/mountinfo").read_text().splitlines()
    if line.split()[4] == "/run"
)
assert any(
    field.startswith("shared:") for field in runtime_mount[6 : runtime_mount.index("-")]
), "Workspace /run must propagate native session mounts to child namespaces"

# All test mounts live in this exec process's private namespace, never the
# desktop/init namespace. A failing assertion cannot leave mounts in the VM.
checked(libc.unshare(CLONE_NEWNS), "isolate test namespace")
checked(libc.mount(None, b"/", None, MS_PRIVATE_RECURSIVE, None), "isolate propagation")
tested = []
for cpu in sorted(os.sched_getaffinity(0)):
    ready_r, ready_w = os.pipe()
    done_r, done_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        os.close(done_w)
        try:
            os.sched_setaffinity(0, {cpu})
            checked(libc.unshare(CLONE_NEWNS), "create descendant namespace")
            os.write(ready_w, b"1")
            os.close(ready_w)
            os.read(done_r, 1)
        finally:
            os._exit(0)
    os.close(ready_w)
    os.close(done_r)
    try:
        assert os.read(ready_r, 1) == b"1", f"CPU {cpu}: child setup failed"
        with tempfile.NamedTemporaryFile(prefix="sentinel-namespace-") as target:
            checked(
                libc.mount(
                    f"/proc/{pid}/ns/mnt".encode(), target.name.encode(), None, MS_BIND, None
                ),
                f"CPU {cpu}: preserve descendant namespace",
            )
            checked(libc.umount2(target.name.encode(), 0), "release descendant namespace")
            # The repair must not remove kernel cycle protection.
            result = libc.mount(b"/proc/self/ns/mnt", target.name.encode(), None, MS_BIND, None)
            assert result == -1 and ctypes.get_errno() == errno.EINVAL, "namespace cycle accepted"
        tested.append(cpu)
    finally:
        os.close(done_w)  # EOF releases the child even after a failed mount.
        os.close(ready_r)
        os.waitpid(pid, 0)
print(
    json.dumps(
        {
            "namespace_persistence_cpus": tested,
            "cycle_protection": True,
            "runtime_mount_shared": True,
        }
    )
)
