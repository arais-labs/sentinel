// Compile the real dispatcher into this test translation unit so its blocking
// read/poll path can be exercised without constructing a guest command stream.
#include "rvgpu-renderer/virgl/rvgpu-virgl.c"

static const uint8_t cursor_bytes[] = {
    0,0,255,255, 0,128,0,128, 0,0,0,0,
    255,0,0,255, 64,0,64,128, 0,255,255,255,
};

static void check_cursor_resource(struct rvgpu_egl_state *egl,
    const struct virtio_gpu_update_cursor *update, uint32_t w, uint32_t h,
    uint32_t format, const void *pixels)
{
    (void)egl; (void)update; (void)format;
    assert(w == 3 && h == 2);
    if (memcmp(pixels, cursor_bytes, sizeof(cursor_bytes)))
        errx(1, "Cursor transfer changed row order or alpha (format %u)", format);
}

void test_cursor_resources(struct rvgpu_egl_state *egl)
{
    struct rvgpu_pr_state state = {.egl = egl};
    const struct rvgpu_egl_callbacks *original = egl->cb;
    struct rvgpu_egl_callbacks callbacks = {.set_cursor = check_cursor_resource};
    egl->cb = &callbacks;
    assert(!virgl_renderer_init(&state, 0, &virgl_cbs));
    for (unsigned flags = 0; flags <= 1; flags++) {
        for (unsigned format = 1; format <= 2; format++) {
            struct virgl_renderer_resource_create_args args = {
                .handle = 1, .target = 2, .format = format, .bind = 2,
                .width = 3, .height = 2, .depth = 1, .array_size = 1, .flags = flags,
            };
            struct iovec iov = {.iov_base = (void *)cursor_bytes, .iov_len = sizeof(cursor_bytes)};
            struct virtio_gpu_box box = {.w = 3, .h = 2, .d = 1};
            assert(!virgl_renderer_resource_create(&args, NULL, 0));
            assert(!virgl_renderer_transfer_write_iov(1, 0, 0, 12, 0, (struct virgl_box *)&box, 0, &iov, 1));
            struct virtio_gpu_update_cursor cursor = {.resource_id = 1};
            rvgpu_serve_update_cursor(&state, &cursor);
            virgl_renderer_resource_unref(1);
        }
    }
    virgl_renderer_cleanup(&state);
    egl->cb = original;
    puts("PASS cursor resource row order and alpha, both resource origins");
}

int test_cursor_protocol(void)
{
    union virtio_gpu_cmd command = {0};
    command.hdr.type = VIRTIO_GPU_CMD_UPDATE_CURSOR;
    /* A resource-zero UPDATE hides the cursor, not an invalid resource. */
    if (sanity_check_gpu_cursor(&command, sizeof(command.cursor), true) != VIRTIO_GPU_RESP_OK_NODATA)
        return 0;
    if (sanity_check_gpu_cursor(&command, sizeof(command.cursor) - 1, true) == VIRTIO_GPU_RESP_OK_NODATA)
        return 0;
    command.cursor.pos.scanout_id = VIRTIO_GPU_MAX_SCANOUTS;
    if (sanity_check_gpu_cursor(&command, sizeof(command.cursor), true) != VIRTIO_GPU_RESP_ERR_INVALID_SCANOUT_ID)
        return 0;
    command.cursor.pos.scanout_id = 0;
    command.hdr.type = VIRTIO_GPU_CMD_MOVE_CURSOR;
    return sanity_check_gpu_cursor(&command, sizeof(command.cursor), false) == VIRTIO_GPU_RESP_OK_NODATA;
}

int test_renderer_idle_wake(struct rvgpu_egl_state *graphics, int command_fd)
{
    struct rvgpu_pr_state state = {.egl = graphics, .cmd_socket = command_fd};
    atomic_init(&state.fence_received, 0);
    atomic_init(&state.fence_sent, 0);
    return rvgpu_pr_readbuf(&state, COMMAND);
}
