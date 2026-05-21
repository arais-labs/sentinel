from __future__ import annotations

from shlex import quote

from app.services.runtime.workspace import RemoteWorkspacePaths


def build_bubblewrap_argv(paths: RemoteWorkspacePaths, command: list[str]) -> list[str]:
    return [
        "bwrap",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--ro-bind",
        "/etc",
        "/etc",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/run",
        "--dir",
        "/run/systemd",
        "--dir",
        "/run/systemd/resolve",
        "--ro-bind-try",
        "/run/systemd/resolve/stub-resolv.conf",
        "/run/systemd/resolve/stub-resolv.conf",
        "--ro-bind-try",
        "/run/systemd/resolve/resolv.conf",
        "/run/systemd/resolve/resolv.conf",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/sentinel",
        "--bind",
        paths.workspace,
        "/workspace",
        "--bind",
        paths.state,
        "/state",
        "--bind",
        paths.tmp,
        "/tmp/sentinel",
        "--setenv",
        "HOME",
        "/state/home",
        "--setenv",
        "TMPDIR",
        "/tmp/sentinel",
        "--setenv",
        "XDG_RUNTIME_DIR",
        "/state/runtime",
        "--setenv",
        "SENTINEL_SANDBOX",
        "bubblewrap",
        "--chdir",
        "/workspace",
        *command,
    ]


def build_bubblewrap_command(paths: RemoteWorkspacePaths, command: list[str]) -> str:
    return " ".join(quote(part) for part in build_bubblewrap_argv(paths, command))


def build_require_workspace_script(paths: RemoteWorkspacePaths) -> str:
    return (
        f"test -d {quote(paths.workspace)} && "
        f"test -d {quote(paths.state)} && "
        f"test -d {quote(paths.tmp)}"
    )
