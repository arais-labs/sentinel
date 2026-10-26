// The display service for computer use, reached by the guest over vsock (sentineld DESIGN.md §22):
// one connection is one action batch of NDJSON requests {"action": acquire|status|input|release|
// snapshot}, answered in order with {"ok": ...}. Closing it releases whatever it holds.
#pragma once

// Serves the protocol on a unix socket at `path` (libkrun forwards the guest's vsock port to it).
void service_start(const char *path);
