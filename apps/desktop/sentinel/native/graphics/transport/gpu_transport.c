#include "gpu_transport.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stddef.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

struct stream {
    unsigned char bytes[SENTINEL_GPU_TRANSPORT_WINDOW];
    size_t head, used;
    uint32_t send_credit, outstanding, receive_credit, return_credit;
    int initialized, announced;
};

struct pump {
    struct stream streams[SENTINEL_GPU_TRANSPORT_STREAMS];
    unsigned char input[SENTINEL_GPU_TRANSPORT_HEADER_SIZE];
    size_t input_used;
    uint32_t input_remaining;
    unsigned input_stream;
    unsigned char output[SENTINEL_GPU_TRANSPORT_HEADER_SIZE + SENTINEL_GPU_TRANSPORT_DATA_MAX];
    size_t output_used, output_size;
    unsigned output_type, output_stream;
    uint32_t output_value;
    unsigned next_data, next_control, credit_burst;
};

_Static_assert(sizeof(struct pump) <= 2 * SENTINEL_GPU_TRANSPORT_WINDOW +
               SENTINEL_GPU_TRANSPORT_DATA_MAX + 256,
               "GPU transport storage must remain fixed and bounded");

static uint32_t get32(const unsigned char *p)
{
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 |
           (uint32_t)p[2] << 8 | (uint32_t)p[3];
}

static void put32(unsigned char *p, uint32_t value)
{
    p[0] = (unsigned char)(value >> 24);
    p[1] = (unsigned char)(value >> 16);
    p[2] = (unsigned char)(value >> 8);
    p[3] = (unsigned char)value;
}

static int fail(int error)
{
    errno = error;
    return -1;
}

static int nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        return -1;
#ifdef SO_NOSIGPIPE
    int enabled = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) < 0)
        return -1;
#endif
    return 0;
}

static void output_header(struct pump *p, unsigned type, unsigned stream, uint32_t value)
{
    put32(p->output, SENTINEL_GPU_TRANSPORT_MAGIC);
    p->output[4] = SENTINEL_GPU_TRANSPORT_VERSION;
    p->output[5] = (unsigned char)type;
    p->output[6] = (unsigned char)stream;
    p->output[7] = 0;
    put32(p->output + 8, value);
    p->output_used = 0;
    p->output_size = SENTINEL_GPU_TRANSPORT_HEADER_SIZE;
    p->output_type = type;
    p->output_stream = stream;
    p->output_value = value;
}

/* Control takes the next whole-frame slot, never interleaves into DATA. */
static void prepare_control(struct pump *p, int allow_credit)
{
    if (p->output_size)
        return;
    for (unsigned n = 0; n < SENTINEL_GPU_TRANSPORT_STREAMS; n++) {
        unsigned id = (p->next_control + n) % SENTINEL_GPU_TRANSPORT_STREAMS;
        struct stream *s = &p->streams[id];
        if (!s->announced) {
            output_header(p, SENTINEL_GPU_TRANSPORT_INIT, id, SENTINEL_GPU_TRANSPORT_WINDOW);
        } else if (allow_credit && s->return_credit) {
            output_header(p, SENTINEL_GPU_TRANSPORT_CREDIT, id, s->return_credit);
            s->return_credit = 0;
        } else {
            continue;
        }
        p->next_control = (id + 1) % SENTINEL_GPU_TRANSPORT_STREAMS;
        return;
    }
}

static int send_output(struct pump *p, int wire)
{
    while (p->output_used < p->output_size) {
        ssize_t n = send(wire, p->output + p->output_used,
                         p->output_size - p->output_used, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return 0;
        if (n <= 0)
            return n == 0 ? fail(EPIPE) : -1;
        size_t before = p->output_used;
        p->output_used += (size_t)n;
        if (p->output_type == SENTINEL_GPU_TRANSPORT_DATA) {
            size_t old_payload = before > SENTINEL_GPU_TRANSPORT_HEADER_SIZE ?
                before - SENTINEL_GPU_TRANSPORT_HEADER_SIZE : 0;
            size_t new_payload = p->output_used > SENTINEL_GPU_TRANSPORT_HEADER_SIZE ?
                p->output_used - SENTINEL_GPU_TRANSPORT_HEADER_SIZE : 0;
            p->streams[p->output_stream].outstanding += (uint32_t)(new_payload - old_payload);
        }
    }
    if (!p->output_size)
        return 0;
    struct stream *s = &p->streams[p->output_stream];
    if (p->output_type == SENTINEL_GPU_TRANSPORT_INIT) {
        s->announced = 1;
        s->receive_credit = p->output_value;
    } else if (p->output_type == SENTINEL_GPU_TRANSPORT_CREDIT) {
        s->receive_credit += p->output_value;
        if (p->credit_burst < SENTINEL_GPU_TRANSPORT_STREAMS)
            p->credit_burst++;
    } else {
        p->credit_burst = 0;
    }
    p->output_size = p->output_used = 0;
    return 0;
}

static int parse_header(struct pump *p)
{
    unsigned type = p->input[5], id = p->input[6];
    uint32_t value = get32(p->input + 8);
    if (get32(p->input) != SENTINEL_GPU_TRANSPORT_MAGIC ||
        p->input[4] != SENTINEL_GPU_TRANSPORT_VERSION || p->input[7] ||
        id >= SENTINEL_GPU_TRANSPORT_STREAMS || !value)
        return fail(EPROTO);
    struct stream *s = &p->streams[id];
    switch (type) {
    case SENTINEL_GPU_TRANSPORT_INIT:
        if (s->initialized || value != SENTINEL_GPU_TRANSPORT_WINDOW)
            return fail(EPROTO);
        s->initialized = 1;
        s->send_credit = value;
        break;
    case SENTINEL_GPU_TRANSPORT_CREDIT:
        if (!s->initialized || value > s->outstanding ||
            value > SENTINEL_GPU_TRANSPORT_WINDOW - s->send_credit)
            return fail(EPROTO);
        s->outstanding -= value;
        s->send_credit += value;
        break;
    case SENTINEL_GPU_TRANSPORT_DATA:
        if (!s->initialized || !s->announced || value > SENTINEL_GPU_TRANSPORT_DATA_MAX ||
            value > s->receive_credit)
            return fail(EPROTO);
        s->receive_credit -= value;
        p->input_stream = id;
        p->input_remaining = value;
        break;
    default:
        return fail(EPROTO);
    }
    p->input_used = 0;
    return 0;
}

static int buffered(const struct pump *p)
{
    return p->output_size || p->input_used || p->input_remaining ||
           p->streams[0].used || p->streams[1].used;
}

/* 0: drained until EAGAIN, 1: clean EOF, -1: malformed/disconnected. */
static int receive_wire(struct pump *p, int wire)
{
    for (;;) {
        unsigned char *target;
        size_t capacity;
        struct stream *s = &p->streams[p->input_stream];
        if (p->input_remaining) {
            size_t tail = (s->head + s->used) % SENTINEL_GPU_TRANSPORT_WINDOW;
            capacity = SENTINEL_GPU_TRANSPORT_WINDOW - tail;
            if (capacity > p->input_remaining)
                capacity = p->input_remaining;
            if (capacity > SENTINEL_GPU_TRANSPORT_WINDOW - s->used)
                return fail(EPROTO);
            target = s->bytes + tail;
        } else {
            target = p->input + p->input_used;
            capacity = SENTINEL_GPU_TRANSPORT_HEADER_SIZE - p->input_used;
        }
        ssize_t n = recv(wire, target, capacity, 0);
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return 0;
        if (n < 0)
            return -1;
        if (!n) {
            if (p->input_used || p->input_remaining)
                return fail(EPROTO);
            return buffered(p) ? fail(ECONNRESET) : 1;
        }
        if (p->input_remaining) {
            s->used += (size_t)n;
            p->input_remaining -= (uint32_t)n;
        } else {
            p->input_used += (size_t)n;
            if (p->input_used == SENTINEL_GPU_TRANSPORT_HEADER_SIZE && parse_header(p) < 0)
                return -1;
        }
    }
}

static int drain_consumer(struct stream *s, int fd)
{
    while (s->used) {
        size_t count = SENTINEL_GPU_TRANSPORT_WINDOW - s->head;
        if (count > s->used)
            count = s->used;
        ssize_t n = send(fd, s->bytes + s->head, count, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return 0;
        if (n <= 0)
            return n == 0 ? fail(EPIPE) : -1;
        s->head = (s->head + (size_t)n) % SENTINEL_GPU_TRANSPORT_WINDOW;
        s->used -= (size_t)n;
        s->return_credit += (uint32_t)n;
    }
    return 0;
}

static int take_source(struct pump *p, const int channels[2])
{
    if (p->output_size)
        return 0;
    for (unsigned n = 0; n < SENTINEL_GPU_TRANSPORT_STREAMS; n++) {
        unsigned id = (p->next_data + n) % SENTINEL_GPU_TRANSPORT_STREAMS;
        struct stream *s = &p->streams[id];
        if (!s->send_credit)
            continue;
        size_t count = s->send_credit;
        if (count > SENTINEL_GPU_TRANSPORT_DATA_MAX)
            count = SENTINEL_GPU_TRANSPORT_DATA_MAX;
        ssize_t received = recv(channels[id], p->output + SENTINEL_GPU_TRANSPORT_HEADER_SIZE, count, 0);
        if (received < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK))
            continue;
        if (received < 0)
            return -1;
        if (!received)
            return buffered(p) ? fail(ECONNRESET) : 1;
        output_header(p, SENTINEL_GPU_TRANSPORT_DATA, id, (uint32_t)received);
        p->output_size += (size_t)received;
        s->send_credit -= (uint32_t)received;
        p->next_data = (id + 1) % SENTINEL_GPU_TRANSPORT_STREAMS;
        return 0;
    }
    return 0;
}

static int prepare_output(struct pump *p, const int channels[2])
{
    /* INIT always wins. Bound CREDIT bursts so sustained incoming traffic
     * cannot starve ready outbound DATA. If neither local source is ready,
     * immediately resume credits; never wait for DATA to become available. */
    prepare_control(p, p->credit_burst < SENTINEL_GPU_TRANSPORT_STREAMS);
    int result = take_source(p, channels);
    if (result)
        return result;
    prepare_control(p, 1);
    return 0;
}

static int run(struct pump *p, int wire, const int channels[2], int stop_fd)
{
    if (wire < 0 || channels[0] < 0 || channels[1] < 0 || stop_fd < -1 ||
        wire == channels[0] || wire == channels[1] || channels[0] == channels[1] ||
        (stop_fd >= 0 && (stop_fd == wire || stop_fd == channels[0] || stop_fd == channels[1])))
        return fail(EINVAL);
    if (nonblocking(wire) < 0 || nonblocking(channels[0]) < 0 || nonblocking(channels[1]) < 0)
        return -1;
    for (;;) {
        int result = prepare_output(p, channels);
        if (result)
            return result == 1 ? 0 : -1;
        struct pollfd fds[4] = {
            {.fd = wire, .events = POLLIN | (p->output_size ? POLLOUT : 0)},
            {.fd = channels[0]}, {.fd = channels[1]},
            {.fd = stop_fd, .events = POLLIN}
        };
        for (unsigned id = 0; id < SENTINEL_GPU_TRANSPORT_STREAMS; id++) {
            if (p->streams[id].used)
                fds[id + 1].events |= POLLOUT;
            if (!p->output_size && p->streams[id].send_credit)
                fds[id + 1].events |= POLLIN;
        }
        int ready = poll(fds, 4, -1);
        if (ready < 0 && errno == EINTR)
            continue;
        if (ready < 0)
            return -1;
        if (fds[3].revents & (POLLIN | POLLHUP))
            return 0;
        for (unsigned i = 0; i < 4; i++)
            if (fds[i].revents & POLLNVAL)
                return fail(EBADF);
        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            int result = receive_wire(p, wire);
            if (result)
                return result == 1 ? 0 : -1;
        }
        for (unsigned id = 0; id < SENTINEL_GPU_TRANSPORT_STREAMS; id++) {
            if (fds[id + 1].revents & (POLLERR | POLLHUP))
                return fail(ECONNRESET);
            if ((fds[id + 1].revents & POLLOUT) && drain_consumer(&p->streams[id], channels[id]) < 0)
                return -1;
        }
        if ((fds[0].revents & POLLOUT) && send_output(p, wire) < 0)
            return -1;
    }
}

int sentinel_gpu_transport_run(int wire, const int channels[2], int stop_fd)
{
    if (!channels)
        return fail(EINVAL);
    struct pump p;
    memset(&p, 0, sizeof(p));
    int result = run(&p, wire, channels, stop_fd);
    int error = errno;
    shutdown(wire, SHUT_RDWR);
    shutdown(channels[0], SHUT_RDWR);
    shutdown(channels[1], SHUT_RDWR);
    errno = error;
    return result;
}
