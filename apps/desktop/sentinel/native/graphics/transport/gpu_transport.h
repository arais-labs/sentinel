#ifndef SENTINEL_GPU_TRANSPORT_H
#define SENTINEL_GPU_TRANSPORT_H

#include <stdint.h>

#define SENTINEL_GPU_TRANSPORT_MAGIC UINT32_C(0x53475055) /* SGPU */
#define SENTINEL_GPU_TRANSPORT_VERSION 1u
#define SENTINEL_GPU_TRANSPORT_HEADER_SIZE 12u
#define SENTINEL_GPU_TRANSPORT_STREAMS 2u
#define SENTINEL_GPU_TRANSPORT_WINDOW 65536u
#define SENTINEL_GPU_TRANSPORT_DATA_MAX 16384u

enum sentinel_gpu_transport_type {
    SENTINEL_GPU_TRANSPORT_INIT = 1,
    SENTINEL_GPU_TRANSPORT_CREDIT = 2,
    SENTINEL_GPU_TRANSPORT_DATA = 3
};

/* Wire format, explicitly serialized (never a native struct):
 * bytes 0..3: big-endian magic; byte 4: version; byte 5: type;
 * byte 6: stream (0 command, 1 resource); byte 7: zero;
 * bytes 8..11: big-endian DATA payload length or credit byte count.
 * INIT grants exactly WINDOW bytes, once per stream in each direction.
 * CREDIT grants bytes actually drained to the local consumer, never merely
 * received off the wire. DATA length is in [1, DATA_MAX].
 *
 * Runs one bounded, nonblocking, poll-driven duplex pump. Caller owns all file
 * descriptors and closes them after return. wire/channels must be distinct
 * connected stream sockets; stop_fd is an optional readable cancellation fd
 * (-1 disables cancellation). No thread, daemon, or heap allocation is created.
 * The pump sets sockets nonblocking and shuts them down on every exit, including
 * setup failure. It does not restore their flags. A GPU session is coupled:
 * there is no logical stream half-close or reconnect inside this protocol.
 *
 * Returns 0 on cancellation or clean session EOF, -1 with errno on failure.
 * Malformed/truncated frames or invalid credit accounting use EPROTO; physical
 * disconnect with buffered data uses ECONNRESET rather than silently succeeding.
 * Exiting is not a graphics completion/fence acknowledgment.
 */
int sentinel_gpu_transport_run(int wire, const int channels[2], int stop_fd);

#endif
