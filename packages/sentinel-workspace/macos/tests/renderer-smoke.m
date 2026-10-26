// virglrenderer on the host, without a VM: reports the capsets the guest would see, has vrend (GLES
// on ANGLE/Metal) clear a render target and reads it back, both through virglrenderer and as the
// Metal texture a display takes for a scanout, and opens a Venus and a Neptune context.
// Built and run by `make test` against build/lib.
#import <Metal/Metal.h>
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>
#include <unistd.h>

#include "virglrenderer.h"
#include "virgl_hw.h"
#include "virgl_protocol.h"

#define CAPSET_VIRGL 1
#define CAPSET_VIRGL2 2
#define CAPSET_VENUS 4
#define CAPSET_NEPTUNE 7

#define PIPE_TEXTURE_2D 2
#define PIPE_BIND_RENDER_TARGET (1 << 1)
#define PIPE_BIND_SCANOUT (1 << 18)
#define PIPE_CLEAR_COLOR0 (1 << 2)

static uint32_t last_fence;
static void write_fence(void *cookie, uint32_t fence) { (void)cookie; last_fence = fence; }

static struct virgl_renderer_callbacks callbacks = {
    .version = 3,
    .write_fence = write_fence,
};

static uint32_t f2u(float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static void report_capsets(void) {
    static const struct { uint32_t id; const char *name; } sets[] = {
        { CAPSET_VIRGL, "VIRGL" }, { CAPSET_VIRGL2, "VIRGL2" },
        { CAPSET_VENUS, "VENUS" }, { CAPSET_NEPTUNE, "NEPTUNE" },
    };
    for (size_t i = 0; i < sizeof(sets) / sizeof(sets[0]); i++) {
        uint32_t version = 0, size = 0;
        virgl_renderer_get_cap_set(sets[i].id, &version, &size);
        printf("capset %u %-7s version %u size %u\n", sets[i].id, sets[i].name, version, size);
        if (sets[i].id == CAPSET_VIRGL2) {
            assert(size >= sizeof(struct virgl_caps_v2));
            struct virgl_caps_v2 *caps = calloc(1, size);
            virgl_renderer_fill_caps(sets[i].id, version, caps);
            printf("  renderer \"%.64s\" glsl %u max_samples %u max_texture_2d %u\n",
                   caps->renderer, caps->v1.glsl_level, caps->v1.max_samples,
                   caps->max_texture_2d_size);
            free(caps);
        }
        if (sets[i].id == CAPSET_NEPTUNE && size >= 8) {
            uint32_t *caps = calloc(1, size);
            virgl_renderer_fill_caps(sets[i].id, version, caps);
            printf("  wire_format_version %u caps_flags 0x%08x\n", caps[0], caps[1]);
            free(caps);
        }
    }
}

// B=0.25, G=0.5, R=1, A=1 (unorm8 rounding: 0x40 and 0x80, one step either way)
static bool cleared(const uint8_t *pixels, size_t size) {
    for (size_t i = 0; i < size; i += 4) {
        if (abs(pixels[i] - 0x40) > 1 || abs(pixels[i + 1] - 0x80) > 1 || pixels[i + 2] != 0xff ||
            pixels[i + 3] != 0xff)
            return false;
    }
    return true;
}

// The Metal texture virglrenderer hands a display for a scanout resource, read back on the GPU.
static bool scanout_texture(uint32_t res, uint32_t size) {
    virgl_renderer_native_handle handle = NULL;
    enum virgl_renderer_native_handle_type type = virgl_renderer_create_handle_for_scanout(
        res, size, size, VIRGL_FORMAT_B8G8R8A8_UNORM, 0, size * 4, 0, &handle);
    if (type != VIRGL_NATIVE_HANDLE_METAL_TEXTURE) {
        fprintf(stderr, "scanout: no Metal texture (type %d)\n", type);
        return false;
    }
    bool ok;
    @autoreleasepool {
        id<MTLTexture> texture = (__bridge id<MTLTexture>)handle;
        id<MTLDevice> device = texture.device;
        id<MTLBuffer> buffer = [device newBufferWithLength:size * size * 4
                                                   options:MTLResourceStorageModeShared];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> commands = [queue commandBuffer];
        id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
                   sourceSize:MTLSizeMake(size, size, 1) toBuffer:buffer destinationOffset:0
       destinationBytesPerRow:size * 4 destinationBytesPerImage:size * size * 4];
        [blit endEncoding];
        [commands commit];
        [commands waitUntilCompleted];
        const uint8_t *pixels = buffer.contents;
        printf("scanout Metal texture %lux%lu pixel format %lu: first %02x%02x%02x%02x\n",
               (unsigned long)texture.width, (unsigned long)texture.height,
               (unsigned long)texture.pixelFormat, pixels[0], pixels[1], pixels[2], pixels[3]);
        ok = cleared(pixels, size * size * 4);
    }
    virgl_renderer_release_handle_for_scanout(type, handle);
    return ok;
}

// vrend: a 64x64 BGRA render target cleared to (1, 0.5, 0.25, 1), read back.
static bool vrend_clear(uint32_t ctx, uint32_t res, uint32_t bind, bool scanout) {
    const uint32_t surface = 1, size = 64;
    if (virgl_renderer_context_create_with_flags(ctx, CAPSET_VIRGL2, 5, "smoke")) {
        fprintf(stderr, "vrend: context creation failed\n");
        return false;
    }
    struct virgl_renderer_resource_create_args args = {
        .handle = res, .target = PIPE_TEXTURE_2D, .format = VIRGL_FORMAT_B8G8R8A8_UNORM,
        .bind = bind, .width = size, .height = size, .depth = 1,
        .array_size = 1,
    };
    if (virgl_renderer_resource_create(&args, NULL, 0)) {
        fprintf(stderr, "vrend: resource creation failed\n");
        return false;
    }
    virgl_renderer_ctx_attach_resource(ctx, res);
    uint32_t cmd[] = {
        VIRGL_CMD0(VIRGL_CCMD_CREATE_SUB_CTX, 0, 1), 1,
        VIRGL_CMD0(VIRGL_CCMD_SET_SUB_CTX, 0, 1), 1,
        VIRGL_CMD0(VIRGL_CCMD_CREATE_OBJECT, VIRGL_OBJECT_SURFACE, VIRGL_OBJ_SURFACE_SIZE),
        surface, res, VIRGL_FORMAT_B8G8R8A8_UNORM, 0, 0,
        VIRGL_CMD0(VIRGL_CCMD_SET_FRAMEBUFFER_STATE, 0, 3), 1, 0, surface,
        VIRGL_CMD0(VIRGL_CCMD_CLEAR, 0, VIRGL_OBJ_CLEAR_SIZE), PIPE_CLEAR_COLOR0,
        f2u(1.0f), f2u(0.5f), f2u(0.25f), f2u(1.0f), 0, 0, 0,
    };
    if (virgl_renderer_submit_cmd(cmd, ctx, sizeof(cmd) / 4)) {
        fprintf(stderr, "vrend: command submission failed\n");
        return false;
    }
    // A fence flushes the GL work, as the guest's fence before a page flip does.
    virgl_renderer_create_fence(ctx, 0);
    for (int i = 0; i < 1000 && last_fence != ctx; i++) {
        virgl_renderer_poll();
        usleep(1000);
    }
    if (last_fence != ctx) {
        fprintf(stderr, "vrend: fence %u never signalled\n", ctx);
        return false;
    }
    static uint8_t pixels[64 * 64 * 4];
    struct iovec iov = { pixels, sizeof(pixels) };
    struct virgl_box box = { .w = size, .h = size, .d = 1 };
    if (virgl_renderer_transfer_read_iov(res, ctx, 0, size * 4, 0, &box, 0, &iov, 1)) {
        fprintf(stderr, "vrend: readback failed\n");
        return false;
    }
    const uint8_t *last = pixels + sizeof(pixels) - 4;
    printf("vrend clear readback: first %02x%02x%02x%02x last %02x%02x%02x%02x (BGRA)\n",
           pixels[0], pixels[1], pixels[2], pixels[3], last[0], last[1], last[2], last[3]);
    bool ok = cleared(pixels, sizeof(pixels));
    if (ok && scanout)
        ok = scanout_texture(res, size);
    virgl_renderer_ctx_detach_resource(ctx, res);
    virgl_renderer_resource_unref(res);
    virgl_renderer_context_destroy(ctx);
    return ok;
}

static bool open_context(uint32_t ctx, uint32_t capset, const char *name) {
    int ret = virgl_renderer_context_create_with_flags(ctx, capset, strlen(name), name);
    printf("%s context: %s\n", name, ret ? "FAILED" : "created");
    if (!ret)
        virgl_renderer_context_destroy(ctx);
    return !ret;
}

int main(void) {
    static int cookie;
    int flags = VIRGL_RENDERER_USE_EGL | VIRGL_RENDERER_USE_GLES | VIRGL_RENDERER_USE_SURFACELESS |
                VIRGL_RENDERER_VENUS | VIRGL_RENDERER_NEPTUNE | VIRGL_RENDERER_RENDER_SERVER |
                VIRGL_RENDERER_THREAD_SYNC | VIRGL_RENDERER_NATIVE_SHARE_TEXTURE;
    if (virgl_renderer_init(&cookie, flags, &callbacks)) {
        fprintf(stderr, "virgl_renderer_init failed\n");
        return 1;
    }
    report_capsets();
    bool vrend = vrend_clear(1, 1, PIPE_BIND_RENDER_TARGET, false);
    printf("vrend clear: %s\n", vrend ? "PASS" : "FAIL");
    bool scanout = vrend_clear(2, 2, PIPE_BIND_RENDER_TARGET | PIPE_BIND_SCANOUT, true);
    printf("vrend scanout: %s\n", scanout ? "PASS" : "FAIL");
    bool venus = open_context(3, CAPSET_VENUS, "venus");
    bool neptune = open_context(4, CAPSET_NEPTUNE, "neptune");
    virgl_renderer_cleanup(&cookie);
    return vrend && scanout && venus && neptune ? 0 : 1;
}
