// The guest's network: libslirp, user-mode NAT with DHCP and a DNS proxy (no root, no
// entitlement, the same code on every platform). Guest 10.0.2.15 via DHCP, gateway 10.0.2.2,
// DNS 10.0.2.3.
#pragma once
#include <libkrun.h>
#include <stdint.h>

// A virtio-net device whose frames go through libslirp on its own thread.
KrunNetDevice net_device(const uint8_t mac[6]);
