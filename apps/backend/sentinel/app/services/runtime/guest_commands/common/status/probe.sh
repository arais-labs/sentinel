#!/usr/bin/env bash
set +e

emit() {
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "${5:-}"
}

find_binary() {
  name="$1"
  path="$(command -v "$name" 2>/dev/null || true)"
  printf '%s\n' "$path"
}

root="$1"
remote_os=linux
required="bash tmux python3 git gh rg jq"
optional="chromium Xorg weston xfce4-session dbus-run-session xdpyinfo"

emit ssh_command "pass" "Remote command" "$(id -un 2>/dev/null)@$(hostname 2>/dev/null)"
if [ "$remote_os" = "linux" ] || [ "$remote_os" = "darwin" ]; then
  emit os "pass" "OS" "$remote_os"
else
  emit os "fail" "OS" "$remote_os" "$sandbox_hint"
fi

emit sandbox "pass" "Isolation" "container" ""

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
    emit "binary_$item" "fail" "$item" "not found" "Install '$item' in the workspace."
  fi
done

for item in $optional; do
  if path="$(find_binary "$item")" && [ -n "$path" ]; then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "skip" "$item" "Optional package not installed" "Add Desktop or Chromium in workspace settings if needed."
  fi
done

if [ -f /opt/sentinel/desktop/desktop-session.py ] && [ -f /etc/sentinel/desktop.json ]; then
  emit desktop_stack "pass" "Desktop" "Installed; starts on demand"
else
  emit desktop_stack "skip" "Desktop" "Not installed" "Choose a desktop in workspace settings to use graphical applications."
fi
