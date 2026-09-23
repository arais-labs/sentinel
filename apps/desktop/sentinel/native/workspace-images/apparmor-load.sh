#!/bin/sh
# Native boot integration for one workspace per dedicated VM kernel only.
# Its root AppArmor namespace is not shared with macOS or another workspace.
# Load existing distro/Snap policy without changing confinement or detection.
set -eu

fail() { printf 'Sentinel AppArmor: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'policy loading requires root'
[ -f /usr/lib/sentinel/workspace-image.json ] || fail 'not a native workspace image'
[ -x /sbin/apparmor_parser ] || fail 'apparmor_parser is not installed'
[ -w /sys/kernel/security/apparmor/.load ] || fail 'AppArmor policy interface is unavailable'

case "${1:-}" in
    system)
        [ -d /etc/apparmor.d ] || fail 'system profile directory is missing'
        # The distro parser owns directory filtering, parser.conf,
        # disable/force-complain handling and feature-keyed cache selection.
        exec /sbin/apparmor_parser --replace --write-cache -- /etc/apparmor.d
        ;;
    snap)
        # Match snapd-apparmor's selection of generated profiles. Snapd still
        # owns generation, refresh, removal and the policy contents themselves.
        set --
        for profile in /var/lib/snapd/apparmor/profiles/*; do
            [ -e "$profile" ] || continue
            case "$profile" in *~) continue ;; esac
            set -- "$@" "$profile"
        done
        [ "$#" -gt 0 ] || exit 0
        exec /sbin/apparmor_parser --replace --write-cache \
            --cache-loc=/var/cache/apparmor -- "$@"
        ;;
    *) fail 'expected system or snap' ;;
esac
