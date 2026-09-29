#!/bin/sh
# Disposable qualification only. WLR_XWAYLAND is wlroots' stock server hook.
# Preserve all compositor-supplied arguments and the real Xwayland executable.
umask 077
exec env WAYLAND_DEBUG=client /usr/bin/Xwayland "$@" 2>"${XDG_RUNTIME_DIR:?}/sentinel-xwayland-protocol.log"
