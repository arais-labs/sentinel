// The desktop: libkrun's scanout -> H.264 (VideoToolbox) -> the media channel to sentineld-media
// (sentineld DESIGN.md §6 packet format), viewer input -> input.c, and snapshots for computer use.
#pragma once
#import <Foundation/Foundation.h>
#include <libkrun.h>

// The GPU device's display backend, advertising one display of this size and refresh rate.
KrunDisplayBackend display_backend(uint32_t width, uint32_t height, uint32_t hz);
// Starts writing video to and reading viewer requests from the media channel.
void display_media(int fd);
// Sends one packet of `type` (sentineld DESIGN.md §6) to the viewer channel, if there is one.
void display_packet(uint8_t type, const void *payload, uint32_t size);
// The current scanout size (0x0 before the guest sets one up).
void display_viewport(uint32_t *width, uint32_t *height);
// PNG (JPEG at falling quality past 1.4 MB) of the newest frame; nil when there is none.
NSData *display_snapshot(void);
