#define _POSIX_C_SOURCE 200809L
#include "gpu_transport.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "%s:%d: %s (errno=%d)\n", __FILE__, __LINE__, #test, errno); \
    abort(); \
} } while (0)
#define MIB (1024u * 1024u)

struct endpoint {
    int wire, channels[2], stop[2];
    pthread_t thread;
    int result, error;
};

struct writer {
    int fd;
    size_t length;
    unsigned tag;
    pthread_t thread;
    atomic_size_t sent;
    atomic_bool done;
    int error;
};

static int64_t milliseconds(void)
{
    struct timespec value;
    CHECK(clock_gettime(CLOCK_MONOTONIC, &value) == 0);
    return (int64_t)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}

static void ready(int fd, short events, int64_t deadline)
{
    for (;;) {
        int64_t remaining = deadline - milliseconds();
        CHECK(remaining > 0);
        struct pollfd item = {.fd = fd, .events = events};
        int result = poll(&item, 1, (int)remaining);
        if (result < 0 && errno == EINTR)
            continue;
        CHECK(result > 0);
        return;
    }
}

static void socket_pair(int pair[2])
{
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
    for (int i = 0; i < 2; i++) {
        int flags = fcntl(pair[i], F_GETFL), size = 4096;
        CHECK(flags >= 0 && fcntl(pair[i], F_SETFL, flags | O_NONBLOCK) == 0);
        CHECK(setsockopt(pair[i], SOL_SOCKET, SO_SNDBUF, &size, sizeof(size)) == 0);
        CHECK(setsockopt(pair[i], SOL_SOCKET, SO_RCVBUF, &size, sizeof(size)) == 0);
#ifdef SO_NOSIGPIPE
        int enabled = 1;
        CHECK(setsockopt(pair[i], SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) == 0);
#endif
    }
}

static void write_all(int fd, const void *bytes, size_t length)
{
    const unsigned char *p = bytes;
    int64_t deadline = milliseconds() + 15000;
    while (length) {
        ssize_t n = send(fd, p, length, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            ready(fd, POLLOUT, deadline);
            continue;
        }
        CHECK(n > 0);
        p += n;
        length -= (size_t)n;
    }
}

static void read_all(int fd, void *bytes, size_t length)
{
    unsigned char *p = bytes;
    int64_t deadline = milliseconds() + 15000;
    while (length) {
        ssize_t n = recv(fd, p, length, 0);
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            ready(fd, POLLIN, deadline);
            continue;
        }
        CHECK(n > 0);
        p += n;
        length -= (size_t)n;
    }
}

static void *pump_thread(void *opaque)
{
    struct endpoint *e = opaque;
    e->result = sentinel_gpu_transport_run(e->wire, e->channels, e->stop[0]);
    e->error = errno;
    return NULL;
}

static void start(struct endpoint *e)
{
    CHECK(pipe(e->stop) == 0);
    CHECK(pthread_create(&e->thread, NULL, pump_thread, e) == 0);
}

static void cancel(struct endpoint *e)
{
    CHECK(write(e->stop[1], "!", 1) == 1);
}

static void join_close(struct endpoint *e)
{
    CHECK(pthread_join(e->thread, NULL) == 0);
    /* The caller still owns valid descriptors after the pump exits. */
    CHECK(fcntl(e->wire, F_GETFD) >= 0);
    CHECK(fcntl(e->channels[0], F_GETFD) >= 0);
    CHECK(fcntl(e->channels[1], F_GETFD) >= 0);
    close(e->wire);
    close(e->channels[0]);
    close(e->channels[1]);
    close(e->stop[0]);
    close(e->stop[1]);
}

static unsigned char pattern(size_t offset, unsigned tag)
{
    uint32_t value = (uint32_t)offset * UINT32_C(2654435761) ^
                     (uint32_t)(offset >> 8) ^ (uint32_t)(offset >> 16) ^ tag;
    return (unsigned char)(value ^ (value >> 13));
}

static void *write_pattern(void *opaque)
{
    struct writer *w = opaque;
    unsigned char bytes[16384];
    size_t offset = 0;
    int64_t deadline = milliseconds() + 30000;
    while (offset < w->length) {
        size_t length = w->length - offset;
        if (length > sizeof(bytes))
            length = sizeof(bytes);
        for (size_t n = 0; n < length; n++)
            bytes[n] = pattern(offset + n, w->tag);
        size_t written = 0;
        while (written < length) {
            ssize_t n = send(w->fd, bytes + written, length - written, MSG_NOSIGNAL);
            if (n < 0 && errno == EINTR)
                continue;
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                ready(w->fd, POLLOUT, deadline);
                continue;
            }
            if (n <= 0) {
                w->error = n ? errno : EPIPE;
                atomic_store(&w->done, 1);
                return NULL;
            }
            written += (size_t)n;
            atomic_store(&w->sent, offset + written);
        }
        offset += length;
    }
    atomic_store(&w->done, 1);
    return NULL;
}

static void read_pattern(int fd, size_t length, unsigned tag)
{
    unsigned char bytes[16384];
    size_t offset = 0;
    while (offset < length) {
        size_t count = length - offset;
        if (count > sizeof(bytes))
            count = sizeof(bytes);
        read_all(fd, bytes, count);
        for (size_t n = 0; n < count; n++)
            CHECK(bytes[n] == pattern(offset + n, tag));
        offset += count;
    }
}

static void large_independent_duplex(void)
{
    int wire[2], app[2][2];
    socket_pair(wire);
    struct endpoint e[2] = {{.wire = wire[0]}, {.wire = wire[1]}};
    struct writer writers[2][2];
    memset(writers, 0, sizeof(writers));
    for (int side = 0; side < 2; side++) {
        for (int id = 0; id < 2; id++) {
            int pair[2];
            socket_pair(pair);
            e[side].channels[id] = pair[0];
            app[side][id] = pair[1];
            writers[side][id].fd = pair[1];
            writers[side][id].length = 32 * MIB;
            writers[side][id].tag = (unsigned)(side * 17 + id * 101);
            atomic_init(&writers[side][id].sent, 0);
            atomic_init(&writers[side][id].done, 0);
        }
        start(&e[side]);
    }
    for (int side = 0; side < 2; side++)
        for (int id = 0; id < 2; id++)
            CHECK(pthread_create(&writers[side][id].thread, NULL, write_pattern,
                                 &writers[side][id]) == 0);
    /* Neither command consumer reads anything. Resource streams must both
     * finish, even though one resource direction is also initially unread. */
    read_pattern(app[1][1], 32 * MIB, writers[0][1].tag);
    read_pattern(app[0][1], 32 * MIB, writers[1][1].tag);
    for (int side = 0; side < 2; side++) {
        CHECK(!atomic_load(&writers[side][0].done));
        CHECK(atomic_load(&writers[side][0].sent) < 4 * SENTINEL_GPU_TRANSPORT_WINDOW);
    }
    read_pattern(app[1][0], 32 * MIB, writers[0][0].tag);
    read_pattern(app[0][0], 32 * MIB, writers[1][0].tag);
    for (int side = 0; side < 2; side++)
        for (int id = 0; id < 2; id++) {
            CHECK(pthread_join(writers[side][id].thread, NULL) == 0);
            CHECK(!writers[side][id].error);
        }
    cancel(&e[0]);
    cancel(&e[1]);
    for (int side = 0; side < 2; side++) {
        join_close(&e[side]);
        CHECK(e[side].result == 0 || e[side].error == ECONNRESET || e[side].error == EPIPE);
        for (int id = 0; id < 2; id++)
            close(app[side][id]);
    }
    puts("PASS four exact32MiB flows: stalled commands bounded, resources finish, commands then drain");
}

static void header(unsigned char bytes[12], unsigned type, unsigned id, uint32_t value)
{
    static const unsigned char prefix[8] = {'S', 'G', 'P', 'U', 1, 0, 0, 0};
    memcpy(bytes, prefix, sizeof(prefix));
    bytes[5] = (unsigned char)type;
    bytes[6] = (unsigned char)id;
    bytes[8] = (unsigned char)(value >> 24);
    bytes[9] = (unsigned char)(value >> 16);
    bytes[10] = (unsigned char)(value >> 8);
    bytes[11] = (unsigned char)value;
}

static void raw_start(struct endpoint *e, int *wire, int apps[2])
{
    int pair[2];
    socket_pair(pair);
    e->wire = pair[0];
    *wire = pair[1];
    for (int id = 0; id < 2; id++) {
        socket_pair(pair);
        e->channels[id] = pair[0];
        apps[id] = pair[1];
    }
    start(e);
    unsigned char bytes[24], expected[24];
    read_all(*wire, bytes, sizeof(bytes));
    header(expected, SENTINEL_GPU_TRANSPORT_INIT, 0, SENTINEL_GPU_TRANSPORT_WINDOW);
    header(expected + 12, SENTINEL_GPU_TRANSPORT_INIT, 1, SENTINEL_GPU_TRANSPORT_WINDOW);
    CHECK(memcmp(bytes, expected, sizeof(bytes)) == 0);
}

static void raw_finish(struct endpoint *e, int wire, const int apps[2], int error)
{
    join_close(e);
    CHECK(e->result == -1 && e->error == error);
    close(wire);
    close(apps[0]);
    close(apps[1]);
}

static void malformed(void)
{
    for (int test = 0; test < 15; test++) {
        struct endpoint e;
        int wire, apps[2];
        raw_start(&e, &wire, apps);
        unsigned char bytes[24];
        header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, SENTINEL_GPU_TRANSPORT_WINDOW);
        size_t length = 12;
        switch (test) {
        case 0: bytes[0] = '!'; break;
        case 1: bytes[4] = 2; break;
        case 2: bytes[5] = 9; break;
        case 3: bytes[6] = 2; break;
        case 4: bytes[7] = 1; break;
        case 5: header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, 0); break;
        case 6: header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, UINT32_MAX); break;
        case 7: memcpy(bytes + 12, bytes, 12); length = 24; break;
        case 8: header(bytes + 12, SENTINEL_GPU_TRANSPORT_CREDIT, 0, 1); length = 24; break;
        case 9: header(bytes + 12, SENTINEL_GPU_TRANSPORT_DATA, 0,
                       SENTINEL_GPU_TRANSPORT_DATA_MAX + 1); length = 24; break;
        case 10: header(bytes, SENTINEL_GPU_TRANSPORT_CREDIT, 0, 1); break;
        case 11: header(bytes, SENTINEL_GPU_TRANSPORT_DATA, 0, 1); break;
        case 12: header(bytes + 12, SENTINEL_GPU_TRANSPORT_CREDIT, 0, 0); length = 24; break;
        case 13: header(bytes + 12, SENTINEL_GPU_TRANSPORT_CREDIT, 0, UINT32_MAX); length = 24; break;
        case 14: header(bytes + 12, SENTINEL_GPU_TRANSPORT_DATA, 0, 0); length = 24; break;
        }
        write_all(wire, bytes, length);
        raw_finish(&e, wire, apps, EPROTO);
    }
    puts("PASS malformed magic/version/type/stream/reserved/zero/overflow/duplicate INIT/overcredit/oversize");
}

static void fragmented(void)
{
    struct endpoint e;
    int wire, apps[2];
    unsigned char bytes[12], received[17];
    raw_start(&e, &wire, apps);
    header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, SENTINEL_GPU_TRANSPORT_WINDOW);
    for (size_t n = 0; n < sizeof(bytes); n++)
        write_all(wire, bytes + n, 1);
    /* Drain each byte before sending the next: parser continuation is exercised
     * deterministically, rather than relying on socket write boundaries. */
    header(bytes, SENTINEL_GPU_TRANSPORT_DATA, 0, sizeof(received));
    for (size_t n = 0; n < sizeof(bytes); n++)
        write_all(wire, bytes + n, 1);
    for (size_t n = 0; n < sizeof(received); n++) {
        unsigned char expected = pattern(n, 47);
        write_all(wire, &expected, 1);
        read_all(apps[0], received + n, 1);
        CHECK(received[n] == expected);
    }
    cancel(&e);
    join_close(&e);
    CHECK(e.result == 0);
    close(wire);
    close(apps[0]);
    close(apps[1]);
    puts("PASS bytewise fragmented control/DATA with per-byte delivery acknowledgement");
}

static void buffered_disconnect(void)
{
    for (int mode = 0; mode < 3; mode++) {
        struct endpoint e;
        int wire, apps[2];
        unsigned char bytes[12], payload[SENTINEL_GPU_TRANSPORT_DATA_MAX];
        memset(payload, 0x71, sizeof(payload));
        raw_start(&e, &wire, apps);
        header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, SENTINEL_GPU_TRANSPORT_WINDOW);
        write_all(wire, bytes, sizeof(bytes));
        for (unsigned n = 0; n < SENTINEL_GPU_TRANSPORT_WINDOW / sizeof(payload); n++) {
            header(bytes, SENTINEL_GPU_TRANSPORT_DATA, 0, sizeof(payload));
            write_all(wire, bytes, sizeof(bytes));
            write_all(wire, payload, sizeof(payload));
        }
        /* Consumer socket capacity is deliberately much smaller than the full
         * receive window. No application drains it before disconnecting. */
        if (mode == 2) {
            int capacity;
            socklen_t size = sizeof(capacity);
            CHECK(getsockopt(e.channels[0], SOL_SOCKET, SO_SNDBUF, &capacity, &size) == 0);
            CHECK(capacity < (int)sizeof(payload));
            /* At most the tiny consumer socket capacity can have earned new
             * credit. Another entire frame exceeds the remaining grant. */
            write_all(wire, bytes, sizeof(bytes));
            raw_finish(&e, wire, apps, EPROTO);
        } else {
            CHECK(shutdown(mode ? apps[0] : wire, SHUT_WR) == 0);
            raw_finish(&e, wire, apps, ECONNRESET);
        }
    }
    puts("PASS receive-window overrun rejected; local/wire EOF cannot silently drop buffered data");
}

static void truncated(void)
{
    for (int payload = 0; payload < 2; payload++) {
        struct endpoint e;
        int wire, apps[2];
        unsigned char bytes[24];
        raw_start(&e, &wire, apps);
        header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, SENTINEL_GPU_TRANSPORT_WINDOW);
        write_all(wire, bytes, 12);
        header(bytes, SENTINEL_GPU_TRANSPORT_DATA, 0, 8);
        write_all(wire, bytes, payload ? 12 : 7);
        if (payload)
            write_all(wire, "abc", 3);
        CHECK(shutdown(wire, SHUT_WR) == 0);
        raw_finish(&e, wire, apps, EPROTO);
    }
    puts("PASS truncated header and DATA payload EOF");
}

static void cancellation(void)
{
    struct endpoint e;
    int wire, apps[2];
    raw_start(&e, &wire, apps);
    unsigned char bytes[12];
    header(bytes, SENTINEL_GPU_TRANSPORT_INIT, 0, SENTINEL_GPU_TRANSPORT_WINDOW);
    write_all(wire, bytes, sizeof(bytes));
    /* Make a local producer block after exhausting wire credit. No wire reader
     * is active; cancellation must release both pump and producer. */
    struct writer w = {.fd = apps[0], .length = 32 * MIB, .tag = 17};
    atomic_init(&w.sent, 0);
    atomic_init(&w.done, 0);
    CHECK(pthread_create(&w.thread, NULL, write_pattern, &w) == 0);
    ready(wire, POLLIN, milliseconds() + 5000);
    cancel(&e);
    join_close(&e);
    CHECK(e.result == 0);
    CHECK(pthread_join(w.thread, NULL) == 0);
    CHECK(w.error == EPIPE || w.error == ECONNRESET);
    close(wire);
    close(apps[0]);
    close(apps[1]);
    puts("PASS cancellation releases backpressured producer; caller retains FD ownership");
}

int main(void)
{
    large_independent_duplex();
    malformed();
    truncated();
    fragmented();
    buffered_disconnect();
    cancellation();
    puts("ALL GPU CREDIT TRANSPORT TESTS PASSED");
    return 0;
}
