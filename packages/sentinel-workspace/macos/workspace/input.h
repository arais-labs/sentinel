// The guest's virtual input: a keyboard and a tablet-style pointer (absolute 0-65535 from viewers,
// 0-32767 to the guest; five buttons, wheels; the model QEMU's virtio-tablet uses), as virtio-input
// devices. Viewer input and computer use both write here; a computer-use batch holding the lease
// silences the viewer.
#pragma once
#include <libkrun.h>
#include <stdbool.h>
#include <stdint.h>

// Media and computer-use input packets: device u16be, count u16be, then count x (type u16be,
// code u16be, value i32be).
enum { INPUT_WHEEL, INPUT_POINTER, INPUT_KEYBOARD };
#define INPUT_MAX_EVENTS 16
// Keys (1-255) and pointer buttons (256 + button - BTN_LEFT) a computer-use batch holds.
#define INPUT_HELD (256 + 8)

struct input_event_be {
  uint16_t type, code;
  int32_t value;
};

// The two devices, to attach to the VM.
KrunInputDevice input_keyboard(void);
KrunInputDevice input_pointer(void);

// Delivers one batch (a SYN_REPORT is appended); false if any event is not one the devices
// declared (nothing is delivered). `held` (INPUT_HELD entries) tracks what a batch holds; NULL for
// the viewer, whose input is dropped while a computer-use batch holds the lease.
bool input_inject(uint16_t device, const struct input_event_be *events, unsigned count, bool *held);
// Parses a packet payload (see above) and delivers it.
bool input_packet(const uint8_t *payload, uint32_t size, bool *held);
// Releases every key and button in `held`.
void input_release(bool *held);
// The pointer's last absolute position (0-65535 each).
void input_pointer_position(uint16_t *x, uint16_t *y);

// The computer-use lease: while set, viewer input is ignored.
void input_set_leased(bool leased);
