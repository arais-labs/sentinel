#!/bin/sh
# Releases the firmware's boot screen (a framebuffer the kernel keeps beside the GPU), run before the
# desktop starts: a compositor that drives every GPU (GNOME, Plasma on Wayland) would otherwise add a
# second, unseen monitor beside the one shown, and absolute pointer clicks would land across both.
# The console moves to the GPU's framebuffer.
for fb in /sys/bus/platform/drivers/simple-framebuffer/simple-framebuffer.* \
	/sys/bus/platform/drivers/efi-framebuffer/efi-framebuffer.*; do
	[ -e "$fb" ] && echo "${fb##*/}" >"${fb%/*}/unbind"
done
exit 0
