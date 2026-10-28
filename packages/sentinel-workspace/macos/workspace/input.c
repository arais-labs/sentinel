// Virtual input devices for libkrun (see input.h). One queue per device; a pipe byte per event
// wakes libkrun's input worker, which reads events back in order.
#include "input.h"

#include <errno.h>
#include <fcntl.h>
#include <libkrun_input.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum { EV_SYN = 0, EV_KEY = 1, EV_REL = 2, EV_ABS = 3, SYN_REPORT = 0, REL_HWHEEL = 6, REL_WHEEL = 8 };
enum { ABS_X = 0, ABS_Y = 1, BTN_LEFT = 0x110, BTN_EXTRA = 0x114 };
#define QUEUE 4096

struct queue {
  int pipe[2];
  pthread_mutex_t lock;
  struct krun_input_event events[QUEUE];
  uint32_t head, tail;
  bool pointer;
};

static struct queue keyboard = {.lock = PTHREAD_MUTEX_INITIALIZER};
static struct queue pointer = {.lock = PTHREAD_MUTEX_INITIALIZER, .pointer = true};
static _Atomic uint16_t pointer_x = 32768, pointer_y = 32768;
static atomic_bool leased;

static int32_t create(void **instance, const void *userdata, const void *reserved) {
  (void)reserved;
  *instance = (void *)userdata;
  return 0;
}

static int ready_fd(void *self) { return ((struct queue *)self)->pipe[0]; }

static int32_t next_event(void *self, struct krun_input_event *out) {
  struct queue *q = self;
  char byte;
  if (read(q->pipe[0], &byte, 1) != 1) return 0;
  pthread_mutex_lock(&q->lock);
  *out = q->events[q->head++ % QUEUE];
  pthread_mutex_unlock(&q->lock);
  return 1;
}

static int32_t copy_name(uint8_t *buf, size_t len, const char *name) {
  size_t n = strlen(name) < len ? strlen(name) : len;
  memcpy(buf, name, n);
  return (int32_t)n;
}

static int32_t device_name(void *self, uint8_t *buf, size_t len) {
  return copy_name(buf, len, ((struct queue *)self)->pointer ? "Sentinel pointer" : "Sentinel keyboard");
}

static int32_t serial_name(void *self, uint8_t *buf, size_t len) {
  (void)self;
  return copy_name(buf, len, "sentinel");
}

static int32_t device_ids(void *self, struct krun_input_device_ids *ids) {
  *ids = (struct krun_input_device_ids){
      .bustype = 0x06 /* BUS_VIRTUAL */, .vendor = 0x1af4, .product = ((struct queue *)self)->pointer ? 1 : 2, .version = 1};
  return 0;
}

static int32_t set_bits(uint8_t *buf, size_t len, uint16_t first, uint16_t last) {
  int32_t used = 0;
  memset(buf, 0, len);
  for (uint16_t bit = first; bit <= last; bit++) {
    if (bit / 8u >= len) break;
    buf[bit / 8] |= (uint8_t)(1u << (bit % 8));
    used = bit / 8 + 1;
  }
  return used;
}

static int32_t capabilities(void *self, uint8_t type, uint8_t *buf, size_t len) {
  if (((struct queue *)self)->pointer) {
    if (type == EV_KEY) return set_bits(buf, len, BTN_LEFT, BTN_EXTRA);
    if (type == EV_ABS) return set_bits(buf, len, ABS_X, ABS_Y);
    if (type == EV_REL) {
      // wheels only: relative motion would make libinput treat it as a mouse and drop ABS
      memset(buf, 0, len);
      if (len < 2) return 0;
      buf[REL_HWHEEL / 8] |= 1u << (REL_HWHEEL % 8);
      buf[REL_WHEEL / 8] |= 1u << (REL_WHEEL % 8);
      return 2;
    }
    return 0;
  }
  return type == EV_KEY ? set_bits(buf, len, 1, 255) : 0;
}

// The pointer's range: 0-32767, as QEMU's tablet. Windows reads it through HID, whose logical
// maximum is signed: 65535 would read as -1 and send positions past the middle to the screen's edge.
// Viewers send 0-65535 (input.h), halved on the way in.
enum { ABS_MAX = 32767 };

static int32_t abs_info(void *self, uint8_t axis, struct krun_input_absinfo *info) {
  (void)self, (void)axis;
  *info = (struct krun_input_absinfo){.min = 0, .max = ABS_MAX};
  return 0;
}

static int32_t properties(void *self, uint8_t *buf, size_t len) {
  (void)self;
  memset(buf, 0, len);
  return 0;
}

static KrunInputDevice make(struct queue *q) {
  if (pipe(q->pipe) || fcntl(q->pipe[0], F_SETFL, O_NONBLOCK) || fcntl(q->pipe[0], F_SETFD, FD_CLOEXEC) ||
      fcntl(q->pipe[1], F_SETFD, FD_CLOEXEC)) {
    perror("sentinel-workspace: input pipe");
    exit(1);
  }
  struct krun_input_config config = {
      .features = KRUN_INPUT_CONFIG_FEATURE_QUERY,
      .create_userdata = q,
      .create = create,
      .vtable = {.query_device_name = device_name, .query_serial_name = serial_name, .query_device_ids = device_ids,
                 .query_event_capabilities = capabilities, .query_abs_info = abs_info, .query_properties = properties}};
  struct krun_input_event_provider provider = {.features = KRUN_INPUT_EVENT_PROVIDER_FEATURE_QUEUE,
                                               .create_userdata = q,
                                               .create = create,
                                               .vtable = {.get_ready_efd = ready_fd, .next_event = next_event}};
  KrunError err = NULL;
  KrunInputDevice device = krun_input_device_new(&config, sizeof config, &provider, sizeof provider, &err);
  if (err) {
    fprintf(stderr, "sentinel-workspace: input device failed\n");
    exit(1);
  }
  return device;
}

KrunInputDevice input_keyboard(void) { return make(&keyboard); }
KrunInputDevice input_pointer(void) { return make(&pointer); }

// Events the devices declared; anything else is dropped.
static bool allowed(uint16_t device, const struct input_event_be *e) {
  switch (e->type) {
    case EV_SYN: return e->code == SYN_REPORT;
    case EV_REL:
      return device == INPUT_WHEEL && (e->code == REL_HWHEEL || e->code == REL_WHEEL) && e->value >= -120 && e->value <= 120;
    case EV_ABS: return device == INPUT_POINTER && e->code <= ABS_Y && e->value >= 0 && e->value <= 65535;
    case EV_KEY:
      if (e->value < 0 || e->value > (device == INPUT_KEYBOARD ? 2 : 1)) return false;
      return device == INPUT_KEYBOARD ? e->code >= 1 && e->code <= 255
                                      : device == INPUT_POINTER && e->code >= BTN_LEFT && e->code <= BTN_EXTRA;
    default: return false;
  }
}

bool input_inject(uint16_t device, const struct input_event_be *events, unsigned count, bool *held) {
  if (device > INPUT_KEYBOARD || !count || count > INPUT_MAX_EVENTS) return false;
  for (unsigned i = 0; i < count; i++)
    if (!allowed(device, &events[i])) return false;
  if (!held && atomic_load(&leased)) return true;  // a computer-use batch controls input
  struct queue *q = device == INPUT_KEYBOARD ? &keyboard : &pointer;
  pthread_mutex_lock(&q->lock);
  bool synced = false;
  for (unsigned i = 0; i < count && q->tail - q->head < QUEUE; i++) {
    const struct input_event_be *e = &events[i];
    if (e->type == EV_ABS) atomic_store(e->code == ABS_X ? &pointer_x : &pointer_y, (uint16_t)e->value);
    if (held && e->type == EV_KEY) held[e->code < 256 ? e->code : 256 + e->code - BTN_LEFT] = e->value != 0;
    const uint32_t value = e->type == EV_ABS ? (uint32_t)e->value >> 1 : (uint32_t)e->value;
    q->events[q->tail++ % QUEUE] = (struct krun_input_event){e->type, e->code, value};
    synced = e->type == EV_SYN;
    (void)!write(q->pipe[1], "", 1);
  }
  if (!synced && q->tail - q->head < QUEUE) {
    q->events[q->tail++ % QUEUE] = (struct krun_input_event){EV_SYN, SYN_REPORT, 0};
    (void)!write(q->pipe[1], "", 1);
  }
  pthread_mutex_unlock(&q->lock);
  return true;
}

bool input_packet(const uint8_t *p, uint32_t size, bool *held) {
  if (size < 4) return false;
  uint16_t device = (uint16_t)(p[0] << 8 | p[1]), count = (uint16_t)(p[2] << 8 | p[3]);
  if (!count || count > INPUT_MAX_EVENTS || size != 4u + count * 8u) return false;
  struct input_event_be events[INPUT_MAX_EVENTS];
  for (unsigned i = 0; i < count; i++) {
    const uint8_t *e = p + 4 + i * 8;
    events[i] = (struct input_event_be){(uint16_t)(e[0] << 8 | e[1]), (uint16_t)(e[2] << 8 | e[3]),
                                        (int32_t)((uint32_t)e[4] << 24 | (uint32_t)e[5] << 16 | (uint32_t)e[6] << 8 | e[7])};
  }
  return input_inject(device, events, count, held);
}

void input_release(bool *held) {
  for (unsigned i = 0; i < INPUT_HELD; i++) {
    if (!held[i]) continue;
    struct input_event_be up = {EV_KEY, (uint16_t)(i < 256 ? i : BTN_LEFT + i - 256), 0};
    input_inject(i < 256 ? INPUT_KEYBOARD : INPUT_POINTER, &up, 1, held);
  }
}

void input_pointer_position(uint16_t *x, uint16_t *y) {
  *x = atomic_load(&pointer_x);
  *y = atomic_load(&pointer_y);
}

void input_set_leased(bool value) { atomic_store(&leased, value); }
