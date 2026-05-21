#!/usr/bin/env bash
set +e

emit() {
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4"
}

root="$1"
required="bash tmux python3 bwrap git gh rg jq"
optional="chromium vncserver startxfce4 startplasma-x11 xdpyinfo"

emit ssh_command "pass" "Remote command" "$(id -un 2>/dev/null)@$(hostname 2>/dev/null)"

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
  if path=$(command -v "$item" 2>/dev/null); then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "fail" "$item" "not found"
  fi
done

for item in $optional; do
  if path=$(command -v "$item" 2>/dev/null); then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "warn" "$item" "not found"
  fi
done

if command -v vncserver >/dev/null 2>&1 && command -v xdpyinfo >/dev/null 2>&1; then
  if command -v startxfce4 >/dev/null 2>&1 || command -v startplasma-x11 >/dev/null 2>&1; then
    emit desktop_stack "pass" "Desktop stack" "VNC and desktop command available"
  else
    emit desktop_stack "warn" "Desktop stack" "startxfce4 or startplasma-x11 not found"
  fi
else
  emit desktop_stack "warn" "Desktop stack" "vncserver or xdpyinfo not found"
fi
