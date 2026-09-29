#ifndef SENTINEL_GPU_CONNECTION_H
#define SENTINEL_GPU_CONNECTION_H

struct sentinel_gpu_connection;

/* Creates blocking command/resource endpoints for the renderer, backed by one
 * credit-controlled VM connection. The caller owns the returned endpoints. */
struct sentinel_gpu_connection *sentinel_gpu_connection_open(const char *path,
                                                             int channels[2]);
/* Nonblocking pipe writer, suitable for an async-signal-safe shutdown wake. */
int sentinel_gpu_connection_stop_fd(const struct sentinel_gpu_connection *connection);
/* Cancels and joins the pump, closes its descriptors, and frees the connection.
 * Returns a transport error number, or zero for orderly close/cancellation. */
int sentinel_gpu_connection_close(struct sentinel_gpu_connection *connection);

#endif
