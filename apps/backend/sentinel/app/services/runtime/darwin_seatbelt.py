from __future__ import annotations

from pathlib import PurePosixPath
from shlex import quote

from app.services.runtime.workspace import RemoteWorkspacePaths


def _sb_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unique(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        normalized = path.rstrip("/") or "/"
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _path_aliases(path: str) -> list[str]:
    aliases = [path]
    for source, target in (
        ("/tmp", "/private/tmp"),
        ("/var", "/private/var"),
        ("/etc", "/private/etc"),
    ):
        if path == source or path.startswith(f"{source}/"):
            aliases.append(f"{target}{path[len(source):]}")
    return _unique(aliases)


def _path_ancestors(paths: list[str]) -> list[str]:
    ancestors: list[str] = []
    for path in paths:
        current = PurePosixPath(path)
        for parent in reversed(current.parents):
            parent_text = parent.as_posix()
            if parent_text != "/":
                ancestors.append(parent_text)
    return _unique(ancestors)


def build_append_seatbelt_tool_roots_script(
    paths: RemoteWorkspacePaths,
    profile_path: str,
    *,
    tools: list[str] | None = None,
) -> str:
    tool_names = tools or ["/bin/bash", "bash", "tmux", "git", "gh", "ssh"]
    output_path = (PurePosixPath(paths.runtime) / "seatbelt-tool-roots").as_posix()
    tool_list = " ".join(quote(tool) for tool in tool_names)
    return "\n".join(
        [
            f": > {quote(output_path)}",
            "sentinel_login_shell=$(dscl . -read \"/Users/$(whoami)\" UserShell 2>/dev/null | sed 's/^UserShell: //' || true)",
            'if [ -z "$sentinel_login_shell" ] || [ ! -x "$sentinel_login_shell" ]; then',
            "  sentinel_login_shell=${SHELL:-}",
            "fi",
            "sentinel_resolve_tool() {",
            "  sentinel_name=$1",
            '  sentinel_path=$(command -v "$sentinel_name" 2>/dev/null || true)',
            '  if [ -z "$sentinel_path" ] && [ -n "$sentinel_login_shell" ] && [ -x "$sentinel_login_shell" ]; then',
            '    sentinel_path=$("$sentinel_login_shell" -lc "command -v $sentinel_name" 2>/dev/null | awk \'NF { value=$0 } END { print value }\' || true)',
            "  fi",
            '  if [ "$sentinel_name" = git ]; then',
            "    sentinel_xcrun_git=$(xcrun --find git 2>/dev/null || true)",
            '    if [ -n "$sentinel_xcrun_git" ] && [ -x "$sentinel_xcrun_git" ]; then',
            "      sentinel_path=$sentinel_xcrun_git",
            "    fi",
            "  fi",
            '  if [ -z "$sentinel_path" ] && [ "$sentinel_name" = tmux ]; then',
            "    for sentinel_candidate in /opt/homebrew/bin/tmux /usr/local/bin/tmux /opt/local/bin/tmux /nix/var/nix/profiles/default/bin/tmux; do",
            '      if [ -x "$sentinel_candidate" ]; then',
            "        sentinel_path=$sentinel_candidate",
            "        break",
            "      fi",
            "    done",
            "  fi",
            "  printf '%s\\n' \"$sentinel_path\"",
            "}",
            f"for sentinel_tool in {tool_list}; do",
            '  sentinel_path=$(sentinel_resolve_tool "$sentinel_tool")',
            '  if [ -n "$sentinel_path" ]; then',
            '    sentinel_real=$(cd "$(dirname "$sentinel_path")" && pwd -P)/$(basename "$sentinel_path")',
            '    case "$sentinel_real" in',
            "      /Library/Developer/*) printf '%s\\n' /Library/Developer ;;",
            "      /opt/homebrew/*) printf '%s\\n' /opt/homebrew ;;",
            "      /usr/local/*) printf '%s\\n' /usr/local ;;",
            "      /opt/local/*) printf '%s\\n' /opt/local ;;",
            "      /nix/*) printf '%s\\n' /nix ;;",
            "      /usr/bin/*) printf '%s\\n' /usr/bin ;;",
            "      /usr/sbin/*) printf '%s\\n' /usr/sbin ;;",
            "      /usr/libexec/*) printf '%s\\n' /usr/libexec ;;",
            "      /bin/*) printf '%s\\n' /bin ;;",
            "      /sbin/*) printf '%s\\n' /sbin ;;",
            '      *) dirname "$sentinel_real" ;;',
            "    esac",
            "  fi",
            f"done | sort -u >> {quote(output_path)}",
            "sentinel_tool_path=",
            f"while IFS= read -r sentinel_root; do",
            '  [ -n "$sentinel_root" ] || continue',
            '  sentinel_tool_path="$sentinel_tool_path:$sentinel_root/bin:$sentinel_root/sbin"',
            '  if [ "$sentinel_root" = /Library/Developer ]; then',
            '    sentinel_tool_path="$sentinel_tool_path:/Library/Developer/CommandLineTools/usr/bin"',
            "  fi",
            f"done < {quote(output_path)}",
            'export PATH="${sentinel_tool_path#:}:$PATH"',
            "sentinel_sb_escape() {",
            "  printf '%s' \"$1\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g'",
            "}",
            f"while IFS= read -r sentinel_root; do",
            '  [ -n "$sentinel_root" ] || continue',
            '  sentinel_escaped=$(sentinel_sb_escape "$sentinel_root")',
            f'  printf \'(allow file-read* file-test-existence (subpath "%s"))\\n\' "$sentinel_escaped" >> {quote(profile_path)}',
            f"done < {quote(output_path)}",
        ]
    )


def build_seatbelt_profile(paths: RemoteWorkspacePaths) -> str:
    session_paths = _path_aliases(paths.session_root)
    session_ancestors = _path_ancestors(session_paths)
    system_read_roots = [
        "/Library/Apple",
        "/Library/Developer",
        "/Library/Filesystems/NetFSPlugins",
        "/Library/Preferences",
        "/Library/Preferences/Logging",
        "/System/Library/CoreServices",
        "/System/Library/Extensions",
        "/System/Library/Frameworks",
        "/System/Library/PrivateFrameworks",
        "/System/Library/SubFrameworks",
        "/System/iOSSupport/System/Library/Frameworks",
        "/System/iOSSupport/System/Library/PrivateFrameworks",
        "/System/iOSSupport/System/Library/SubFrameworks",
        "/private/etc",
        "/private/var/db",
        "/usr/lib",
        "/usr/share",
    ]
    executable_roots = [
        "/bin",
        "/sbin",
        "/usr/bin",
        "/usr/sbin",
        "/usr/libexec",
    ]
    lines = [
        "(version 1)",
        "; Sentinel macOS runtime sandbox.",
        "; Based on the same Chrome-inspired platform baseline used by Codex.",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow process-info* (target same-sandbox))",
        "(allow system-socket (socket-domain AF_UNIX))",
        "(allow network-outbound)",
        "(allow network-bind (local unix-socket))",
        "(allow network-outbound (remote unix-socket))",
        "(allow file-write-data",
        "  (require-all",
        '    (path "/dev/null")',
        "    (vnode-type CHARACTER-DEVICE)))",
        "(allow sysctl-read",
        '  (sysctl-name "hw.activecpu")',
        '  (sysctl-name "hw.busfrequency_compat")',
        '  (sysctl-name "hw.byteorder")',
        '  (sysctl-name "hw.cacheconfig")',
        '  (sysctl-name "hw.cachelinesize_compat")',
        '  (sysctl-name "hw.cpufamily")',
        '  (sysctl-name "hw.cpufrequency_compat")',
        '  (sysctl-name "hw.cputype")',
        '  (sysctl-name "hw.l1dcachesize_compat")',
        '  (sysctl-name "hw.l1icachesize_compat")',
        '  (sysctl-name "hw.l2cachesize_compat")',
        '  (sysctl-name "hw.l3cachesize_compat")',
        '  (sysctl-name "hw.logicalcpu_max")',
        '  (sysctl-name "hw.machine")',
        '  (sysctl-name "hw.model")',
        '  (sysctl-name "hw.memsize")',
        '  (sysctl-name "hw.ncpu")',
        '  (sysctl-name "hw.nperflevels")',
        '  (sysctl-name-prefix "hw.optional.arm.")',
        '  (sysctl-name-prefix "hw.optional.armv8_")',
        '  (sysctl-name "hw.packages")',
        '  (sysctl-name "hw.pagesize_compat")',
        '  (sysctl-name "hw.pagesize")',
        '  (sysctl-name "hw.physicalcpu")',
        '  (sysctl-name "hw.physicalcpu_max")',
        '  (sysctl-name "hw.logicalcpu")',
        '  (sysctl-name "hw.cpufrequency")',
        '  (sysctl-name "hw.tbfrequency_compat")',
        '  (sysctl-name "hw.vectorunit")',
        '  (sysctl-name "machdep.cpu.brand_string")',
        '  (sysctl-name "kern.argmax")',
        '  (sysctl-name "kern.hostname")',
        '  (sysctl-name "kern.maxfilesperproc")',
        '  (sysctl-name "kern.maxproc")',
        '  (sysctl-name "kern.osproductversion")',
        '  (sysctl-name "kern.osrelease")',
        '  (sysctl-name "kern.ostype")',
        '  (sysctl-name "kern.osvariant_status")',
        '  (sysctl-name "kern.osversion")',
        '  (sysctl-name "kern.secure_kernel")',
        '  (sysctl-name "kern.usrstack64")',
        '  (sysctl-name "kern.version")',
        '  (sysctl-name "sysctl.proc_cputype")',
        '  (sysctl-name "vm.loadavg")',
        '  (sysctl-name-prefix "hw.perflevel")',
        '  (sysctl-name-prefix "kern.proc.pgrp.")',
        '  (sysctl-name-prefix "kern.proc.pid.")',
        '  (sysctl-name-prefix "net.routetable."))',
        '(allow sysctl-write (sysctl-name "kern.grade_cputype"))',
        '(allow iokit-open (iokit-registry-entry-class "RootDomainUserClient"))',
        "(allow ipc-posix-sem)",
        "(allow ipc-posix-shm-read-data",
        "  ipc-posix-shm-write-create",
        "  ipc-posix-shm-write-unlink",
        '  (ipc-posix-name-regex #"^/__KMP_REGISTERED_LIB_[0-9]+$"))',
        "(allow pseudo-tty)",
        '(allow file-read* file-write* file-ioctl (literal "/dev/ptmx"))',
        "(allow file-read* file-write*",
        "  (require-all",
        '    (regex #"^/dev/ttys[0-9]+")',
        '    (extension "com.apple.sandbox.pty")))',
        '(allow file-read* file-write* file-ioctl (regex #"^/dev/ttys[0-9]+"))',
        '(allow file-ioctl (regex #"^/dev/ttys[0-9]+"))',
        '(allow ipc-posix-shm-read* (ipc-posix-name-prefix "apple.cfprefs."))',
        "(allow user-preference-read)",
        '(allow system-mac-syscall (mac-policy-name "vnguard"))',
        "(allow system-mac-syscall",
        "  (require-all",
        '    (mac-policy-name "Sandbox")',
        "    (mac-syscall-number 67)))",
        "(allow system-fsctl (fsctl-command FSIOC_CAS_BSDFLAGS))",
        "(allow file-read-metadata file-test-existence",
        '  (literal "/etc")',
        '  (literal "/private")',
        '  (literal "/tmp")',
        '  (literal "/var")',
        '  (literal "/private/etc/localtime"))',
    ]
    if session_ancestors:
        lines.append("(allow file-read-metadata file-test-existence")
        lines.extend(f"  (literal {_sb_string(path)})" for path in session_ancestors)
        lines.append(")")
    lines.extend(
        [
            "(allow file-read-metadata file-test-existence",
            '  (path-ancestors "/System/Volumes/Data/private"))',
            '(allow file-read* file-test-existence (literal "/"))',
            "(allow file-read* file-test-existence",
            '  (literal "/dev/autofs_nowait")',
            '  (literal "/dev/random")',
            '  (literal "/dev/urandom")',
            '  (literal "/private/etc/master.passwd")',
            '  (literal "/private/etc/passwd")',
            '  (literal "/private/etc/protocols")',
            '  (literal "/private/etc/services"))',
            "(allow file-read* file-test-existence file-write-data",
            '  (literal "/dev/null")',
            '  (literal "/dev/zero"))',
            '(allow file-read-data file-test-existence file-write-data (subpath "/dev/fd"))',
            "(allow file-read* file-test-existence file-write-data file-ioctl",
            '  (literal "/dev/dtracehelper"))',
            '(allow file-read* (regex #"^/dev/fd/(0|1|2)$"))',
            '(allow file-write* (regex #"^/dev/fd/(1|2)$"))',
            '(allow file-read* file-write* (literal "/dev/null"))',
            '(allow file-read* file-write* (literal "/dev/tty"))',
            '(allow file-read-metadata (literal "/dev"))',
            '(allow file-read-metadata (regex #"^/dev/.*$"))',
            '(allow file-read-metadata (literal "/dev/stdin"))',
            '(allow file-read-metadata (literal "/dev/stdout"))',
            '(allow file-read-metadata (literal "/dev/stderr"))',
            '(allow file-read-metadata (regex #"^/dev/tty[^/]*$"))',
            '(allow file-read-metadata (subpath "/var"))',
            '(allow file-read-metadata (subpath "/private/var"))',
            "(allow mach-lookup",
            '  (global-name "com.apple.analyticsd")',
            '  (global-name "com.apple.analyticsd.messagetracer")',
            '  (global-name "com.apple.appsleep")',
            '  (global-name "com.apple.bsd.dirhelper")',
            '  (global-name "com.apple.cfprefsd.agent")',
            '  (global-name "com.apple.cfprefsd.daemon")',
            '  (global-name "com.apple.diagnosticd")',
            '  (global-name "com.apple.dt.automationmode.reader")',
            '  (global-name "com.apple.espd")',
            '  (global-name "com.apple.logd")',
            '  (global-name "com.apple.logd.events")',
            '  (global-name "com.apple.runningboard")',
            '  (global-name "com.apple.secinitd")',
            '  (global-name "com.apple.system.DirectoryService.libinfo_v1")',
            '  (global-name "com.apple.system.logger")',
            '  (global-name "com.apple.system.notification_center")',
            '  (global-name "com.apple.system.opendirectoryd.libinfo")',
            '  (global-name "com.apple.system.opendirectoryd.membership")',
            '  (global-name "com.apple.trustd")',
            '  (global-name "com.apple.trustd.agent")',
            '  (global-name "com.apple.xpc.activity.unmanaged")',
            '  (global-name "com.apple.PowerManagement.control")',
            '  (global-name "com.apple.audio.audiohald")',
            '  (global-name "com.apple.audio.AudioComponentRegistrar")',
            '  (local-name "com.apple.cfprefsd.agent"))',
            '(allow network-outbound (literal "/private/var/run/syslog"))',
            '(allow ipc-posix-shm-read* (ipc-posix-name "apple.shm.notification_center"))',
            '(allow file-read* (literal "/private/var/db/eligibilityd/eligibility.plist"))',
            "(allow file-map-executable",
        ]
    )
    lines.extend(
        f"  (subpath {_sb_string(path)})" for path in _unique(system_read_roots + executable_roots)
    )
    lines.extend(
        [
            ")",
            "(allow file-read* file-test-existence",
        ]
    )
    lines.extend(
        f"  (subpath {_sb_string(path)})"
        for path in _unique(system_read_roots + executable_roots + session_paths)
    )
    lines.extend([")"])
    lines.extend(["(allow file-write*"])
    lines.extend(f"  (subpath {_sb_string(path)})" for path in session_paths)
    lines.extend(
        [
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def build_seatbelt_command(
    paths: RemoteWorkspacePaths, profile_path: str, command: list[str]
) -> str:
    sandbox_command = " ".join(
        ["sandbox-exec", "-f", quote(profile_path), *(quote(part) for part in command)]
    )
    return " ".join(
        [
            "/bin/sh",
            "-c",
            quote(f"cd {quote(paths.workspace)} && exec {sandbox_command}"),
        ]
    )
