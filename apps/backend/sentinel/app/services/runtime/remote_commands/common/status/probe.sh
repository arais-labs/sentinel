#!/usr/bin/env bash
set +e

emit() {
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "${5:-}"
}

find_binary() {
  name="$1"
  path="$(command -v "$name" 2>/dev/null || true)"
  if [ -z "$path" ] && [ -n "${sentinel_login_shell:-}" ] && [ -x "$sentinel_login_shell" ]; then
    path="$("$sentinel_login_shell" -lc "command -v $name" 2>/dev/null | awk 'NF { value=$0 } END { print value }' || true)"
  fi
  printf '%s\n' "$path"
}

root="$1"
remote_uname="$(uname -s 2>/dev/null || true)"
sentinel_login_shell=""
case "$remote_uname" in
  Linux)
    remote_os="linux"
    sandbox_binary="bwrap"
    sandbox_name="bubblewrap"
    sandbox_hint="Install bubblewrap (bwrap) on the Linux SSH target."
    ;;
  Darwin)
    remote_os="darwin"
    sentinel_login_shell="$(dscl . -read "/Users/$(whoami)" UserShell 2>/dev/null | sed 's/^UserShell: //' || true)"
    if [ -z "$sentinel_login_shell" ] || [ ! -x "$sentinel_login_shell" ]; then
      sentinel_login_shell="${SHELL:-}"
    fi
    sandbox_binary="sandbox-exec"
    sandbox_name="seatbelt"
    sandbox_hint="sandbox-exec is required on macOS SSH targets."
    ;;
  "")
    remote_os="unknown"
    sandbox_binary=""
    sandbox_name="unavailable"
    sandbox_hint="Could not detect the SSH target OS with uname -s."
    ;;
  *)
    remote_os="unsupported"
    sandbox_binary=""
    sandbox_name="unavailable"
    sandbox_hint="Only Linux and macOS SSH targets are supported."
    ;;
esac
required="bash tmux python3 git gh rg jq"
optional="chromium vncserver startxfce4 startplasma-x11 xdpyinfo"

emit ssh_command "pass" "Remote command" "$(id -un 2>/dev/null)@$(hostname 2>/dev/null)"
if [ "$remote_os" = "linux" ] || [ "$remote_os" = "darwin" ]; then
  emit os "pass" "OS" "$remote_os"
else
  emit os "fail" "OS" "$remote_os" "$sandbox_hint"
fi

if [ -n "$sandbox_binary" ] && path="$(find_binary "$sandbox_binary")" && [ -n "$path" ]; then
  emit sandbox "pass" "Sandbox" "$sandbox_name" ""
  emit "binary_$sandbox_binary" "pass" "$sandbox_binary" "$path"
else
  emit sandbox "fail" "Sandbox" "unavailable" "$sandbox_hint"
  if [ -n "$sandbox_binary" ]; then
    emit "binary_$sandbox_binary" "fail" "$sandbox_binary" "not found" "$sandbox_hint"
  fi
fi

if mkdir -p "$root" 2>/tmp/sentinel-runtime-status.err; then
  tmp="$root/.sentinel-health-$$"
  if ( umask 077 && : > "$tmp" ) 2>/tmp/sentinel-runtime-status.err; then
    rm -f "$tmp"
    emit workspace_writable "pass" "Workspace writable" "$root"
  else
    emit workspace_writable "fail" "Workspace writable" "$(cat /tmp/sentinel-runtime-status.err 2>/dev/null)"
  fi
else
  emit workspace_writable "fail" "Workspace writable" "$(cat /tmp/sentinel-runtime-status.err 2>/dev/null)"
fi
rm -f /tmp/sentinel-runtime-status.err

for item in $required; do
  if path="$(find_binary "$item")" && [ -n "$path" ]; then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "fail" "$item" "not found" "Install '$item' on the SSH target."
  fi
done

for item in $optional; do
  if path="$(find_binary "$item")" && [ -n "$path" ]; then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "warn" "$item" "not found" "Install '$item' to enable optional desktop/browser support."
  fi
done

if [ "$remote_os" != "linux" ]; then
  emit desktop_stack "warn" "Desktop stack" "Linux-only" "Desktop and visible browser are currently supported only on Linux SSH targets."
elif [ -n "$(find_binary vncserver)" ] && [ -n "$(find_binary xdpyinfo)" ]; then
  if [ -n "$(find_binary startxfce4)" ] || [ -n "$(find_binary startplasma-x11)" ]; then
    emit desktop_stack "pass" "Desktop stack" "VNC and desktop command available"
  else
    emit desktop_stack "warn" "Desktop stack" "startxfce4 or startplasma-x11 not found" "Install KDE Plasma or XFCE startup commands."
  fi
else
  emit desktop_stack "warn" "Desktop stack" "vncserver or xdpyinfo not found" "Install TigerVNC and X11 desktop diagnostics."
fi
