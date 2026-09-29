#ifndef VIRGL_MAPPED_BUFFER_H
#define VIRGL_MAPPED_BUFFER_H
#include <stdbool.h>
#include <stdint.h>
struct pipe_resource;
struct pipe_context;
struct pipe_screen;
struct pipe_transfer;
struct pipe_box;
struct pipe_fence_handle;
struct virgl_context;
bool virgl_mapped_screen_init(struct pipe_screen *screen);
bool virgl_mapped_supports_storage(struct pipe_screen *screen);
void virgl_mapped_screen_destroy(struct pipe_screen *screen);
void virgl_mapped_submit_lock(struct pipe_screen *screen);
void virgl_mapped_submit_unlock(struct pipe_screen *screen);
bool virgl_mapped_resource(const struct pipe_resource *resource);
void virgl_mapped_host_write(struct pipe_resource *resource);
void *virgl_mapped_map(struct pipe_context *ctx, struct pipe_resource *resource,
                    unsigned usage, const struct pipe_box *box,
                    struct pipe_transfer **transfer);
void virgl_mapped_unmap(struct pipe_context *ctx, struct pipe_transfer *transfer);
void virgl_mapped_flush_region(struct pipe_context *ctx,
                            struct pipe_transfer *transfer,
                            const struct pipe_box *box);
void virgl_mapped_resource_destroy(struct pipe_resource *resource);
void virgl_mapped_barrier(struct virgl_context *ctx);
void virgl_mapped_prepare_flush(struct virgl_context *ctx);
void virgl_mapped_before_consumer(struct pipe_context *ctx);
bool virgl_mapped_flush(struct virgl_context *ctx, struct pipe_fence_handle **fence);
bool virgl_mapped_publish(struct pipe_screen *screen);
bool virgl_mapped_publish_timeout(struct pipe_screen *screen, uint64_t timeout);
int virgl_mapped_fence_get_fd(struct pipe_screen *screen, struct pipe_fence_handle *fence);
void virgl_mapped_context_destroy(struct virgl_context *ctx);
#endif
