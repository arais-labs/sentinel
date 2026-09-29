/* Deterministic scheduler test: replenish drained credits before EVERY frame,
 * including the frame where outbound DATA must win. No timing-dependent flood
 * or production-only test hook is needed; compile the real core in this TU. */
#include "gpu_transport.c"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    int local[2][2], channels[2], wire[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, wire) == 0);
    struct pump p = {0};
    for (unsigned id = 0; id < 2; id++) {
        assert(socketpair(AF_UNIX, SOCK_STREAM, 0, local[id]) == 0);
        channels[id] = local[id][0];
        assert(nonblocking(channels[id]) == 0);
        p.streams[id].announced = p.streams[id].initialized = 1;
        p.streams[id].send_credit = SENTINEL_GPU_TRANSPORT_WINDOW;
        p.streams[id].receive_credit = SENTINEL_GPU_TRANSPORT_WINDOW - 8;
        assert(send(local[id][1], "xy", 2, 0) == 2);
    }
    for (unsigned frame = 0; frame < 6; frame++) {
        for (unsigned id = 0; id < 2; id++)
            if (!p.streams[id].return_credit)
                p.streams[id].return_credit = 1;
        assert(prepare_output(&p, channels) == 0);
        if (frame % 3 == 2) {
            assert(p.output_type == SENTINEL_GPU_TRANSPORT_DATA);
            assert(p.output_stream == frame / 3);
        } else {
            assert(p.output_type == SENTINEL_GPU_TRANSPORT_CREDIT);
        }
        size_t length = p.output_size;
        assert(send_output(&p, wire[0]) == 0);
        unsigned char bytes[SENTINEL_GPU_TRANSPORT_HEADER_SIZE + 2];
        assert(recv(wire[1], bytes, length, MSG_WAITALL) == (ssize_t)length);
    }
    /* Exhausted local sources must NOT inhibit credits after a full burst. */
    p.credit_burst = SENTINEL_GPU_TRANSPORT_STREAMS;
    p.streams[0].return_credit = 1;
    assert(prepare_output(&p, channels) == 0);
    assert(p.output_type == SENTINEL_GPU_TRANSPORT_CREDIT);
    for (unsigned id = 0; id < 2; id++) {
        close(local[id][0]);
        close(local[id][1]);
    }
    close(wire[0]);
    close(wire[1]);
    puts("PASS continuously replenished CREDIT cannot starve either DATA stream");
    return 0;
}
