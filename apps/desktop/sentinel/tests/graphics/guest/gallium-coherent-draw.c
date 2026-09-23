#include "pipe/p_screen.h"
#include "pipe/p_context.h"
#include "pipe/p_state.h"
#include "pipe/p_shader_tokens.h"
#include "util/u_inlines.h"
#include "util/u_simple_shaders.h"
#include "virgl_drm_public.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

enum { SIZE = 256, START = 37, LENGTH = 160, DATA = 64, EDGE = 16 };

static struct pipe_resource *make_buffer(struct pipe_screen *screen, unsigned bind)
{
   struct pipe_resource desc = {
      .target = PIPE_BUFFER, .format = PIPE_FORMAT_R8_UNORM, .width0 = SIZE,
      .height0 = 1, .depth0 = 1, .array_size = 1, .usage = PIPE_USAGE_DEFAULT,
      .bind = bind, .flags = PIPE_RESOURCE_FLAG_MAP_PERSISTENT | PIPE_RESOURCE_FLAG_MAP_COHERENT,
   };
   return screen->resource_create(screen, &desc);
}

static int finish(struct pipe_screen *screen, struct pipe_context *ctx)
{
   struct pipe_fence_handle *fence = NULL;
   ctx->flush(ctx, &fence, 0);
   bool ready = fence && screen->fence_finish(screen, ctx, fence, 5000000000ull);
   screen->fence_reference(screen, &fence, NULL);
   if (!ready)
      fputs("FAIL draw fence\n", stderr);
   return ready;
}

static void write_bytes(volatile unsigned char *mapped, unsigned offset,
                        const void *data, unsigned length)
{
   const unsigned char *bytes = data;
   for (unsigned i = 0; i < length; i++)
      mapped[offset - START + i] = bytes[i];
}

int main(int argc, char **argv)
{
   setbuf(stdout, NULL);
   alarm(60);
   int fd = open(argc > 1 ? argv[1] : "/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
   if (fd < 0)
      return 2;
   struct pipe_screen *screen = virgl_drm_screen_create(fd, NULL);
   close(fd);
   if (!screen)
      return 2;
   struct pipe_context *ctx = screen->context_create(screen, NULL, 0);
   if (!ctx) {
      screen->destroy(screen);
      return 2;
   }
   printf("draw driver=%s public_cap=%u\n", screen->get_name(screen),
          screen->caps.buffer_map_persistent_coherent);
   struct pipe_resource *vertex = make_buffer(screen, PIPE_BIND_VERTEX_BUFFER);
   struct pipe_resource *command = make_buffer(screen, PIPE_BIND_COMMAND_ARGS_BUFFER);
   struct pipe_resource target_desc = {
      .target = PIPE_TEXTURE_2D, .format = PIPE_FORMAT_R8G8B8A8_UNORM,
      .width0 = EDGE, .height0 = EDGE, .depth0 = 1, .array_size = 1,
      .bind = PIPE_BIND_RENDER_TARGET | PIPE_BIND_SAMPLER_VIEW,
   };
   struct pipe_resource *target = screen->resource_create(screen, &target_desc);
   struct pipe_transfer *vt = NULL, *ct = NULL;
   volatile unsigned char *vm = NULL, *cm = NULL;
   void *vs = NULL, *fs = NULL, *blend = NULL, *dsa = NULL, *raster = NULL, *ve = NULL;
   int status = 1;
   if (!vertex || !command || !target)
      goto out;
   unsigned char guards[SIZE];
   memset(guards, 0xa5, sizeof(guards));
   ctx->buffer_subdata(ctx, vertex, 0, 0, SIZE, guards);
   ctx->buffer_subdata(ctx, command, 0, 0, SIZE, guards);
   if (!finish(screen, ctx))
      goto out;
   struct pipe_box map_region = {.x = START, .width = LENGTH, .height = 1, .depth = 1};
   unsigned usage = PIPE_MAP_READ | PIPE_MAP_WRITE | PIPE_MAP_PERSISTENT | PIPE_MAP_COHERENT;
   vm = ctx->buffer_map(ctx, vertex, 0, usage, &map_region, &vt);
   cm = ctx->buffer_map(ctx, command, 0, usage, &map_region, &ct);
   if (!vm || !cm) {
      puts("SKIP actual coherent draw buffer map rejected");
      status = 77;
      goto out;
   }
   const enum tgsi_semantic semantics[2] = {TGSI_SEMANTIC_POSITION, TGSI_SEMANTIC_GENERIC};
   const unsigned indices[2] = {0, 0};
   vs = util_make_vertex_passthrough_shader(ctx, 2, semantics, indices, false);
   fs = util_make_fragment_passthrough_shader(ctx, TGSI_SEMANTIC_GENERIC,
                                             TGSI_INTERPOLATE_LINEAR, false);
   struct pipe_blend_state bs = {0};
   bs.rt[0].colormask = PIPE_MASK_RGBA;
   blend = ctx->create_blend_state(ctx, &bs);
   struct pipe_depth_stencil_alpha_state ds = {0};
   dsa = ctx->create_depth_stencil_alpha_state(ctx, &ds);
   struct pipe_rasterizer_state rs = {
      .half_pixel_center = 1, .bottom_edge_rule = 1,
      .depth_clip_near = 1, .depth_clip_far = 1, .scissor = 1,
   };
   raster = ctx->create_rasterizer_state(ctx, &rs);
   struct pipe_vertex_element elements[2] = {
      {.src_offset = 0, .src_format = PIPE_FORMAT_R32G32B32A32_FLOAT, .src_stride = 32},
      {.src_offset = 16, .src_format = PIPE_FORMAT_R32G32B32A32_FLOAT, .src_stride = 32},
   };
   ve = ctx->create_vertex_elements_state(ctx, 2, elements);
   if (!vs || !fs || !blend || !dsa || !raster || !ve)
      goto out;
   ctx->bind_vs_state(ctx, vs);
   ctx->bind_fs_state(ctx, fs);
   ctx->bind_blend_state(ctx, blend);
   ctx->bind_depth_stencil_alpha_state(ctx, dsa);
   ctx->bind_rasterizer_state(ctx, raster);
   ctx->bind_vertex_elements_state(ctx, ve);
   struct pipe_vertex_buffer vb = {.buffer_offset = DATA, .buffer.resource = vertex};
   ctx->set_vertex_buffers(ctx, 1, &vb);
   struct pipe_framebuffer_state fb = {.width = EDGE, .height = EDGE, .nr_cbufs = 1};
   fb.cbufs[0].texture = target;
   fb.cbufs[0].format = target->format;
   ctx->set_framebuffer_state(ctx, &fb);
   struct pipe_viewport_state viewport = {
      .scale = {8, 8, 1}, .translate = {8, 8, 0},
      .swizzle_x = PIPE_VIEWPORT_SWIZZLE_POSITIVE_X,
      .swizzle_y = PIPE_VIEWPORT_SWIZZLE_POSITIVE_Y,
      .swizzle_z = PIPE_VIEWPORT_SWIZZLE_POSITIVE_Z,
      .swizzle_w = PIPE_VIEWPORT_SWIZZLE_POSITIVE_W,
   };
   ctx->set_viewport_states(ctx, 0, 1, &viewport);
   struct pipe_scissor_state scissor = {.minx = 4, .miny = 4, .maxx = 12, .maxy = 12};
   ctx->set_scissor_states(ctx, 0, 1, &scissor);
   ctx->set_sample_mask(ctx, ~0u);
   struct pipe_draw_info info = {.mode = MESA_PRIM_TRIANGLES, .instance_count = 1};
   struct pipe_draw_start_count_bias draw = {.count = 3};
   struct pipe_draw_indirect_info indirect = {.buffer = command, .offset = DATA, .stride = 16, .draw_count = 1};
   unsigned checks = 0;
   for (unsigned round = 0; round < 36; round++) {
      unsigned color = round % 3;
      bool use_indirect = round >= 18;
      bool empty = use_indirect && round % 4 == 0;
      float vertices[3][8] = {{-1, -1, 0, 1, 0, 0, 0, 1},
                              {3, -1, 0, 1, 0, 0, 0, 1},
                              {-1, 3, 0, 1, 0, 0, 0, 1}};
      for (unsigned i = 0; i < 3; i++)
         vertices[i][4 + color] = 1;
      write_bytes(vm, DATA, vertices, sizeof(vertices));
      uint32_t args[4] = {empty ? 0 : 3, 1, 0, 0};
      write_bytes(cm, DATA, args, sizeof(args));
      union pipe_color_union clear = {.f = {0, 0, 0, 1}};
      ctx->clear(ctx, PIPE_CLEAR_COLOR0, 0xf, 0xff, NULL, &clear, 0, 0);
      ctx->draw_vbo(ctx, &info, 0, use_indirect ? &indirect : NULL, &draw, 1);
      if (!finish(screen, ctx))
         goto out;
      struct pipe_box pixels = {.width = EDGE, .height = EDGE, .depth = 1};
      struct pipe_transfer *read_transfer = NULL;
      const unsigned char *read = ctx->texture_map(ctx, target, 0, PIPE_MAP_READ, &pixels, &read_transfer);
      if (!read)
         goto out;
      bool okay = true;
      for (unsigned y = 0; y < EDGE; y++) {
         for (unsigned x = 0; x < EDGE; x++) {
            bool drawn = !empty && x >= 4 && x < 12 && y >= 4 && y < 12;
            for (unsigned component = 0; component < 4; component++) {
               unsigned expected = component == 3 || (drawn && component == color) ? 255 : 0;
               unsigned actual = read[y * read_transfer->stride + x * 4 + component];
               checks++;
               if (actual != expected) {
                  fprintf(stderr, "FAIL round=%u indirect=%u empty=%u pixel=%u,%u component=%u got=%u expected=%u\n",
                          round, use_indirect, empty, x, y, component, actual, expected);
                  okay = false;
                  goto pixels_done;
               }
            }
         }
      }
pixels_done:
      ctx->texture_unmap(ctx, read_transfer);
      if (!okay)
         goto out;
      for (unsigned i = 0; i < LENGTH; i++) {
         unsigned absolute = START + i;
         if ((absolute < DATA || absolute >= DATA + sizeof(vertices)) && vm[i] != 0xa5) {
            fprintf(stderr, "FAIL vertex guard round=%u absolute_byte=%u actual=%02x\n", round, absolute, vm[i]);
            goto out;
         }
         if ((absolute < DATA || absolute >= DATA + sizeof(args)) && cm[i] != 0xa5) {
            fprintf(stderr, "FAIL command guard round=%u absolute_byte=%u actual=%02x\n", round, absolute, cm[i]);
            goto out;
         }
      }
   }
   printf("PASS coherent draw direct=18 indirect=18 pixel_bytes=%u vertex_rebinds_after_initial=0 map_offset=37\n", checks);
   status = 0;
out:
   ctx->set_vertex_buffers(ctx, 0, NULL);
   ctx->bind_vs_state(ctx, NULL);
   ctx->bind_fs_state(ctx, NULL);
   ctx->bind_vertex_elements_state(ctx, NULL);
   ctx->bind_blend_state(ctx, NULL);
   ctx->bind_depth_stencil_alpha_state(ctx, NULL);
   ctx->bind_rasterizer_state(ctx, NULL);
   struct pipe_framebuffer_state empty_fb = {0};
   ctx->set_framebuffer_state(ctx, &empty_fb);
   if (ve) ctx->delete_vertex_elements_state(ctx, ve);
   if (raster) ctx->delete_rasterizer_state(ctx, raster);
   if (dsa) ctx->delete_depth_stencil_alpha_state(ctx, dsa);
   if (blend) ctx->delete_blend_state(ctx, blend);
   if (fs) ctx->delete_fs_state(ctx, fs);
   if (vs) ctx->delete_vs_state(ctx, vs);
   if (vt) ctx->buffer_unmap(ctx, vt);
   if (ct) ctx->buffer_unmap(ctx, ct);
   pipe_resource_reference(&vertex, NULL);
   pipe_resource_reference(&command, NULL);
   pipe_resource_reference(&target, NULL);
   ctx->destroy(ctx);
   screen->destroy(screen);
   return status;
}
