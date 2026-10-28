// libslirp behind libkrun's virtio-net (see net.h). libkrun exchanges raw Ethernet frames over one
// end of a datagram socket pair; one thread owns libslirp (it is not thread-safe): it polls that
// socket and libslirp's sockets, and runs libslirp's timers.
#include "net.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <slirp/libslirp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define MAX_FDS 1024
#define MAX_TIMERS 64

struct timer {
  SlirpTimerCb cb;
  void *opaque;
  int64_t expire_ms;  // -1: not armed
  bool used;
};

static struct {
  int fd;  // our end of the frame socket pair
  Slirp *slirp;
  struct pollfd fds[MAX_FDS];
  nfds_t count;
  struct timer timers[MAX_TIMERS];
} net = {.fd = -1};

static int64_t now_ns(void) { return (int64_t)clock_gettime_nsec_np(CLOCK_MONOTONIC); }

static slirp_ssize_t send_packet(const void *buf, size_t len, void *opaque) {
  (void)opaque;
  // A full socket buffer drops the frame, as a busy NIC would; TCP retransmits.
  ssize_t n = send(net.fd, buf, len, MSG_DONTWAIT);
  return n < 0 ? (slirp_ssize_t)len : n;
}

static void guest_error(const char *message, void *opaque) {
  (void)opaque;
  fprintf(stderr, "sentineld vm: network: %s\n", message);
}

static int64_t clock_get_ns(void *opaque) {
  (void)opaque;
  return now_ns();
}

static void *timer_new(SlirpTimerCb cb, void *cb_opaque, void *opaque) {
  (void)opaque;
  for (unsigned i = 0; i < MAX_TIMERS; i++) {
    if (net.timers[i].used) continue;
    net.timers[i] = (struct timer){.cb = cb, .opaque = cb_opaque, .expire_ms = -1, .used = true};
    return &net.timers[i];
  }
  fprintf(stderr, "sentineld vm: network: out of timers\n");
  abort();
}

static void timer_free(void *timer, void *opaque) {
  (void)opaque;
  *(struct timer *)timer = (struct timer){0};
}

static void timer_mod(void *timer, int64_t expire_ms, void *opaque) {
  (void)opaque;
  ((struct timer *)timer)->expire_ms = expire_ms;
}

static void register_socket(slirp_os_socket socket, void *opaque) { (void)socket, (void)opaque; }
static void notify(void *opaque) { (void)opaque; }

static int add_poll(slirp_os_socket fd, int events, void *opaque) {
  (void)opaque;
  if (net.count == MAX_FDS) return -1;
  short e = 0;
  if (events & SLIRP_POLL_IN) e |= POLLIN;
  if (events & SLIRP_POLL_OUT) e |= POLLOUT;
  if (events & SLIRP_POLL_PRI) e |= POLLPRI;
  net.fds[net.count] = (struct pollfd){.fd = fd, .events = e};
  return (int)net.count++;
}

static int get_revents(int index, void *opaque) {
  (void)opaque;
  if (index < 1 || (nfds_t)index >= net.count) return 0;
  short r = net.fds[index].revents;
  int e = 0;
  if (r & POLLIN) e |= SLIRP_POLL_IN;
  if (r & POLLOUT) e |= SLIRP_POLL_OUT;
  if (r & POLLPRI) e |= SLIRP_POLL_PRI;
  if (r & POLLERR) e |= SLIRP_POLL_ERR;
  if (r & POLLHUP) e |= SLIRP_POLL_HUP;
  return e;
}

static const SlirpCb callbacks = {
    .send_packet = send_packet,
    .guest_error = guest_error,
    .clock_get_ns = clock_get_ns,
    .timer_new = timer_new,
    .timer_free = timer_free,
    .timer_mod = timer_mod,
    .notify = notify,
    .register_poll_socket = register_socket,
    .unregister_poll_socket = register_socket,
};

static void *run(void *arg) {
  (void)arg;
  static uint8_t frame[65536];
  for (;;) {
    // The earliest timer bounds the wait (libslirp may lower it further).
    uint32_t timeout = 1000;
    const int64_t now_ms = now_ns() / 1000000;
    for (unsigned i = 0; i < MAX_TIMERS; i++) {
      const struct timer *t = &net.timers[i];
      if (!t->used || t->expire_ms < 0) continue;
      const int64_t wait = t->expire_ms - now_ms;
      timeout = wait <= 0 ? 0 : (uint32_t)(wait < timeout ? wait : timeout);
    }
    net.fds[0] = (struct pollfd){.fd = net.fd, .events = POLLIN};
    net.count = 1;
    slirp_pollfds_fill_socket(net.slirp, &timeout, add_poll, NULL);
    const int ready = poll(net.fds, net.count, (int)timeout);
    if (ready < 0 && errno != EINTR) {
      perror("sentineld vm: network poll");
      return NULL;
    }
    if (ready > 0 && (net.fds[0].revents & POLLIN)) {
      for (;;) {
        ssize_t n = recv(net.fd, frame, sizeof frame, MSG_DONTWAIT);
        if (n <= 0) break;
        slirp_input(net.slirp, frame, (int)n);
      }
    }
    slirp_pollfds_poll(net.slirp, ready < 0, get_revents, NULL);
    const int64_t after_ms = now_ns() / 1000000;
    for (unsigned i = 0; i < MAX_TIMERS; i++) {
      struct timer *t = &net.timers[i];
      if (!t->used || t->expire_ms < 0 || t->expire_ms > after_ms) continue;
      t->expire_ms = -1;
      t->cb(t->opaque);
    }
  }
}

KrunNetDevice net_device(const uint8_t mac[6]) {
  int pair[2];
  if (socketpair(AF_UNIX, SOCK_DGRAM, 0, pair)) {
    perror("sentineld vm: network socket pair");
    exit(1);
  }
  // Room for bursts of full frames in both directions.
  const int size = 4 << 20;
  for (unsigned i = 0; i < 2; i++) {
    setsockopt(pair[i], SOL_SOCKET, SO_SNDBUF, &size, sizeof size);
    setsockopt(pair[i], SOL_SOCKET, SO_RCVBUF, &size, sizeof size);
    fcntl(pair[i], F_SETFD, FD_CLOEXEC);
  }
  net.fd = pair[0];

  // Version 6: libslirp registers sockets through register_poll_socket (older versions call the
  // deprecated fd callbacks, which are not set).
  SlirpConfig config = {
      .version = 6,
      .in_enabled = true,
      .if_mtu = 1500,
      .if_mru = 1500,
      .vhostname = "sentinel",
      // The guest is untrusted: the gateway must not lead to services bound to the host's loopback.
      .disable_host_loopback = true,
  };
  inet_pton(AF_INET, "10.0.2.0", &config.vnetwork);
  inet_pton(AF_INET, "255.255.255.0", &config.vnetmask);
  inet_pton(AF_INET, "10.0.2.2", &config.vhost);
  inet_pton(AF_INET, "10.0.2.15", &config.vdhcp_start);
  inet_pton(AF_INET, "10.0.2.3", &config.vnameserver);
  net.slirp = slirp_new(&config, &callbacks, NULL);
  if (!net.slirp) {
    fprintf(stderr, "sentineld vm: network setup failed\n");
    exit(1);
  }
  pthread_t thread;
  if (pthread_create(&thread, NULL, run, NULL)) {
    perror("sentineld vm: network thread");
    exit(1);
  }
  pthread_detach(thread);

  KrunError err = NULL;
  // No offloads: libslirp wants complete frames with their checksums.
  KrunNetDevice device = krun_net_device_new_unixgram_fd(
      (KrunStr){.data = "net0", .len = 4}, pair[1], (KrunBytes){.data = mac, .len = 6}, 0, 0, &err);
  if (err) {
    fprintf(stderr, "sentineld vm: network device failed\n");
    exit(1);
  }
  return device;
}
