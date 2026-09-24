#include "pipe/p_screen.h"
#include "pipe/p_context.h"
#include "pipe/p_state.h"
#include "pipe/p_shader_tokens.h"
#include "util/u_inlines.h"
#include "tgsi/tgsi_text.h"
#include "virgl_drm_public.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

enum { SIZE = 256, MAP_START = 37, MAP_SIZE = 160, DATA_START = 64, WORDS = 16 };
static unsigned fence_calls, polls, ready_polls;

static struct pipe_resource *buffer(struct pipe_screen *screen, unsigned flags)
{
   struct pipe_resource desc = {
      .target = PIPE_BUFFER, .format = PIPE_FORMAT_R8_UNORM,
      .width0 = SIZE, .height0 = 1, .depth0 = 1, .array_size = 1,
      .usage = PIPE_USAGE_DEFAULT, .bind = PIPE_BIND_SHADER_BUFFER,
      .flags = flags,
   };
   return screen->resource_create(screen, &desc);
}

static int finish(struct pipe_screen *screen, struct pipe_context *ctx)
{
   struct pipe_fence_handle *fence = NULL;
   ctx->flush(ctx, &fence, 0);
   bool ready = false;
   if (fence && (++fence_calls & 1)) {
      polls++;
      ready = screen->fence_finish(screen, ctx, fence, 0);
      ready_polls += ready;
   }
   if (fence && !ready)
      ready = screen->fence_finish(screen, ctx, fence, 5000000000ull);
   screen->fence_reference(screen, &fence, NULL);
   if (!ready)
      fputs("FAIL native compute fence\n", stderr);
   return ready;
}

static int check(volatile const unsigned char *actual, const unsigned char *expected,
                 unsigned size, const char *label, unsigned round)
{
   for (unsigned i = 0; i < size; i++) {
      if (actual[i] != expected[i]) {
         fprintf(stderr, "FAIL %s round=%u byte=%u actual=%02x expected=%02x\n",
                 label, round, i, actual[i], expected[i]);
         return 0;
      }
   }
   return 1;
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
   printf("compute driver=%s public_cap=%u\n", screen->get_name(screen),
          screen->caps.buffer_map_persistent_coherent);
   struct pipe_context *ctx = screen->context_create(screen, NULL, 0);
   if (!ctx) {
      screen->destroy(screen);
      return 2;
   }
   struct pipe_resource *resources[2] = {
      buffer(screen, PIPE_RESOURCE_FLAG_MAP_PERSISTENT | PIPE_RESOURCE_FLAG_MAP_COHERENT),
      buffer(screen, PIPE_RESOURCE_FLAG_MAP_PERSISTENT | PIPE_RESOURCE_FLAG_MAP_COHERENT),
   };
   struct pipe_resource *staging = buffer(screen, 0);
   struct pipe_transfer *transfers[2] = {NULL, NULL};
   volatile unsigned char *pointers[2] = {NULL, NULL};
   unsigned char expected[2][SIZE];
   int status = 1;
   void *shader = NULL;
   if (!resources[0] || !resources[1] || !staging)
      goto out;
   memset(expected, 0xa5, sizeof(expected));
   ctx->buffer_subdata(ctx, staging, 0, 0, SIZE, expected[0]);
   struct pipe_box full = {.width = SIZE, .height = 1, .depth = 1};
   for (unsigned i = 0; i < 2; i++)
      ctx->resource_copy_region(ctx, resources[i], 0, 0, 0, 0, staging, 0, &full);
   if (!finish(screen, ctx))
      goto out;
   struct pipe_box region = {.x = MAP_START, .width = MAP_SIZE, .height = 1, .depth = 1};
   for (unsigned i = 0; i < 2; i++) {
      pointers[i] = ctx->buffer_map(ctx, resources[i], 0,
         PIPE_MAP_READ | PIPE_MAP_WRITE | PIPE_MAP_PERSISTENT | PIPE_MAP_COHERENT,
         &region, &transfers[i]);
      if (!pointers[i]) {
         puts("SKIP actual coherent SSBO map rejected");
         status = 77;
         goto out;
      }
   }
   const char *text =
      "COMP\n"
      "PROPERTY CS_FIXED_BLOCK_WIDTH 8\n"
      "PROPERTY CS_FIXED_BLOCK_HEIGHT 1\n"
      "PROPERTY CS_FIXED_BLOCK_DEPTH 1\n"
      "DCL SV[0], THREAD_ID\n"
      "DCL SV[1], BLOCK_ID\n"
      "DCL BUFFER[0]\n"
      "DCL BUFFER[1]\n"
      "DCL TEMP[0..1]\n"
      "IMM[0] UINT32 { 8, 4, 3, 17 }\n"
      "IMM[1] UINT32 { 64, 0, 0, 0 }\n"
      "UMAD TEMP[0].x, SV[1].xxxx, IMM[0].xxxx, SV[0].xxxx\n"
      "UMAD TEMP[0].x, TEMP[0].xxxx, IMM[0].yyyy, IMM[1].xxxx\n"
      "LOAD TEMP[1].x, BUFFER[0], TEMP[0].xxxx\n"
      "UMAD TEMP[1].x, TEMP[1].xxxx, IMM[0].zzzz, IMM[0].wwww\n"
      "STORE BUFFER[1].x, TEMP[0].xxxx, TEMP[1].xxxx\n"
      "END\n";
   struct tgsi_token tokens[512];
   if (!tgsi_text_translate(text, tokens, 512)) {
      fputs("FAIL TGSI translate\n", stderr);
      goto out;
   }
   struct pipe_compute_state state = {.ir_type = PIPE_SHADER_IR_TGSI, .prog = tokens};
   shader = ctx->create_compute_state(ctx, &state);
   if (!shader) {
      fputs("FAIL compute creation\n", stderr);
      goto out;
   }
   ctx->bind_compute_state(ctx, shader);
   struct pipe_shader_buffer bindings[2] = {
      {.buffer = resources[0], .buffer_size = SIZE},
      {.buffer = resources[1], .buffer_size = SIZE},
   };
   ctx->set_shader_buffers(ctx, MESA_SHADER_COMPUTE, 0, 2, bindings, 2);
   struct pipe_grid_info grid = {.work_dim = 1, .block = {8, 1, 1}, .grid = {2, 1, 1}};
   for (unsigned round = 0; round < 24; round++) {
      for (unsigned word = 0; word < WORDS; word++) {
         uint32_t input = 0x12345600u + round * 31 + word * 11;
         uint32_t output = input * 3 + 17;
         unsigned offset = DATA_START + word * sizeof(input);
         memcpy(expected[0] + offset, &input, sizeof(input));
         memcpy(expected[1] + offset, &output, sizeof(output));
         for (unsigned byte = 0; byte < sizeof(input); byte++)
            pointers[0][offset - MAP_START + byte] = expected[0][offset + byte];
      }
      ctx->launch_grid(ctx, &grid);
      ctx->memory_barrier(ctx, PIPE_BARRIER_SHADER_BUFFER | PIPE_BARRIER_UPDATE_BUFFER);
      if (!finish(screen, ctx))
         goto out;
      for (unsigned i = 0; i < 2; i++) {
         if (!check(pointers[i], expected[i] + MAP_START, MAP_SIZE,
                    i ? "shader output original pointer" : "input original pointer", round))
            goto out;
         ctx->resource_copy_region(ctx, staging, 0, 0, 0, 0, resources[i], 0, &full);
         if (!finish(screen, ctx))
            goto out;
         struct pipe_transfer *read_transfer = NULL;
         const void *read = ctx->buffer_map(ctx, staging, 0, PIPE_MAP_READ, &full, &read_transfer);
         if (!read)
            goto out;
         int okay = check(read, expected[i], SIZE, "full resource guards", round);
         ctx->buffer_unmap(ctx, read_transfer);
         if (!okay)
            goto out;
      }
   }
   puts("PASS actual GPU coherent compute SSBO rounds=24 invocations=384 byte_checks=19968 original_pointer=yes");
   printf("native_fences=%u zero_timeout_polls=%u ready_polls=%u\n", fence_calls, polls, ready_polls);
   status = 0;
out:
   ctx->set_shader_buffers(ctx, MESA_SHADER_COMPUTE, 0, 2, NULL, 0);
   if (shader) {
      ctx->bind_compute_state(ctx, NULL);
      ctx->delete_compute_state(ctx, shader);
   }
   for (unsigned i = 0; i < 2; i++) {
      if (transfers[i])
         ctx->buffer_unmap(ctx, transfers[i]);
      pipe_resource_reference(&resources[i], NULL);
   }
   pipe_resource_reference(&staging, NULL);
   ctx->destroy(ctx);
   screen->destroy(screen);
   return status;
}
