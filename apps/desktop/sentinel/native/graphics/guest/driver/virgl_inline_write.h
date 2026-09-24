#ifndef VIRGL_INLINE_WRITE_H
#define VIRGL_INLINE_WRITE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct virgl_context;
struct pipe_resource;

size_t virgl_inline_buffer_max_bytes(const struct virgl_context *ctx);
bool virgl_inline_buffer_write(struct virgl_context *ctx,
                              struct pipe_resource *resource,
                              uint32_t offset, const void *data, size_t length);
#endif
