#include "pipe/p_screen.h"
#include "pipe/p_context.h"
#include "pipe/p_state.h"
#include "util/u_inlines.h"
#include "virgl_drm_public.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

enum { SIZE = 256 };

static int complete(struct pipe_screen *screen, struct pipe_context *ctx)
{
   struct pipe_fence_handle *fence = NULL;
   ctx->flush(ctx, &fence, 0);
   bool ready = fence && screen->fence_finish(screen, ctx, fence, 5000000000ull);
   screen->fence_reference(screen, &fence, NULL);
   if (!ready)
      fputs("FAIL query-consumer fence\n", stderr);
   return ready;
}

static int check_destination(struct pipe_context *ctx, struct pipe_resource *dst,
                             const uint8_t *expected, unsigned round)
{
   const struct pipe_box box = {.width = SIZE, .height = 1, .depth = 1};
   struct pipe_transfer *transfer = NULL;
   const uint8_t *bytes = ctx->buffer_map(ctx, dst, 0, PIPE_MAP_READ, &box, &transfer);
   if (!bytes || !transfer)
      return 0;
   bool okay = !memcmp(bytes, expected, SIZE);
   if (!okay)
      fprintf(stderr, "FAIL query round%u full destination guards\n", round);
   ctx->buffer_unmap(ctx, transfer);
   return okay;
}

static int query_case(struct pipe_screen *screen, struct pipe_context *ctx, unsigned mode)
{
   const unsigned map_offset = 32, map_length = 160, at64 = 64, at32 = 104;
   unsigned flags = mode ? PIPE_RESOURCE_FLAG_MAP_PERSISTENT : 0;
   if (mode >= 2) flags |= PIPE_RESOURCE_FLAG_MAP_COHERENT;
   const struct pipe_resource desc = {
      .target = PIPE_BUFFER, .format = PIPE_FORMAT_R8_UNORM,
      .width0 = SIZE, .height0 = 1, .depth0 = 1, .array_size = 1,
      .usage = PIPE_USAGE_DEFAULT,
      /* Match Mesa GL_QUERY_BUFFER allocation: virgl's wire validation accepts
       * singleton buffer binds, not QUERY_BUFFER|VERTEX_BUFFER combinations. */
      .bind = PIPE_BIND_QUERY_BUFFER, .flags = flags,
   };
   struct pipe_resource *dst = screen->resource_create(screen, &desc);
   struct pipe_transfer *mapping = NULL;
   struct pipe_query *query = NULL;
   uint8_t *original = NULL, expected[SIZE];
   int status = 1;
   if (!dst) goto out;
   printf("phase mode%u initialize query buffer\n", mode);
   memset(expected, 0xa5, sizeof(expected));
   ctx->buffer_subdata(ctx, dst, 0, 0, SIZE, expected);
   if (!complete(screen, ctx)) goto out;
   if (mode) {
      const struct pipe_box box = {.x = map_offset, .width = map_length, .height = 1, .depth = 1};
      unsigned usage = PIPE_MAP_READ | PIPE_MAP_PERSISTENT;
      if (mode >= 2) usage |= PIPE_MAP_COHERENT;
      if (mode == 3) usage |= PIPE_MAP_WRITE;
      original = ctx->buffer_map(ctx, dst, 0, usage, &box, &mapping);
      if (!original || !mapping) { fputs("FAIL query persistent mapping\n", stderr); goto out; }
   }
   unsigned kind = screen->caps.query_timestamp ? PIPE_QUERY_TIMESTAMP : PIPE_QUERY_OCCLUSION_COUNTER;
   if (kind == PIPE_QUERY_OCCLUSION_COUNTER && !screen->caps.occlusion_query) {
      fputs("SKIP no actual timestamp or occlusion query capability\n", stderr);
      status = 77; goto out;
   }
   for (unsigned round = 0; round < 4; round++) {
      printf("phase mode%u round%u issue actual query\n", mode, round);
      query = ctx->create_query(ctx, kind, 0);
      if (!query) { fputs("FAIL create actual query\n", stderr); goto out; }
      if (kind != PIPE_QUERY_TIMESTAMP && !ctx->begin_query(ctx, query)) goto out;
      /* Empty occlusion interval is real zero-sample GPU query if timestamps
       * are not supported. No injected query value is used. */
      if (!ctx->end_query(ctx, query)) goto out;
      if (mode == 3) {
         /* Same-byte CPU writes must reach host BEFORE query overwrites.
          * Last round's fence completed before rewriting these bytes. */
         memset(original + at64 - map_offset, 0x5c, sizeof(uint64_t));
         memset(original + at32 - map_offset, 0x6d, sizeof(uint32_t));
         original[7] = (uint8_t)(0x20 + round);
         expected[map_offset + 7] = (uint8_t)(0x20 + round);
      }
      ctx->get_query_result_resource(ctx, query, PIPE_QUERY_WAIT,
                                     PIPE_QUERY_TYPE_U64, 0, dst, at64);
      ctx->get_query_result_resource(ctx, query, PIPE_QUERY_WAIT,
                                     PIPE_QUERY_TYPE_U32, 0, dst, at32);
      if (mode == 1) ctx->memory_barrier(ctx, PIPE_BARRIER_MAPPED_BUFFER);
      if (!complete(screen, ctx)) goto out;
      union pipe_query_result result;
      memset(&result, 0, sizeof(result));
      if (!ctx->get_query_result(ctx, query, true, &result)) {
         fputs("FAIL ordinary real query result\n", stderr); goto out;
      }
      if (kind == PIPE_QUERY_TIMESTAMP && !result.u64) {
         fputs("FAIL real timestamp unexpectedly zero\n", stderr); goto out;
      }
      /* Query U32 conversion clamps, unlike an ordinary C narrowing cast. */
      uint32_t low = result.u64 > UINT32_MAX ? UINT32_MAX : (uint32_t)result.u64;
      memcpy(expected + at64, &result.u64, sizeof(result.u64));
      memcpy(expected + at32, &low, sizeof(low));
      if (mapping && memcmp(original, expected + map_offset, map_length)) {
         for (unsigned i = 0; i < map_length; i++) if (original[i] != expected[map_offset + i]) {
            fprintf(stderr, "FAIL query mode%u round%u original byte%u got%02x expected%02x\n",
                    mode, round, map_offset + i, original[i], expected[map_offset + i]); break;
         }
         goto out;
      }
      if (!check_destination(ctx, dst, expected, round)) goto out;
      printf("PASS query mode%u round%u kind=%s actual=%llu U64/U32 original-pointer+fullguards\n",
             mode, round, kind == PIPE_QUERY_TIMESTAMP ? "timestamp" : "occlusion",
             (unsigned long long)result.u64);
      ctx->destroy_query(ctx, query); query = NULL;
   }
   status = 0;
out:
   if (query) ctx->destroy_query(ctx, query);
   if (mapping) ctx->buffer_unmap(ctx, mapping);
   pipe_resource_reference(&dst, NULL);
   return status;
}

int main(int argc, char **argv)
{
   setvbuf(stdout, NULL, _IONBF, 0); alarm(25);
   if (argc != 2) return 2;
   int fd = open(argv[1], O_RDWR | O_CLOEXEC);
   if (fd < 0) return 2;
   struct pipe_screen *screen = virgl_drm_screen_create(fd, NULL);
   close(fd);
   if (!screen) return 2;
   printf("driver=%s persistent_coherent_cap=%u query_buffer=%u timestamp=%u occlusion=%u (not modified)\n",
          screen->get_name(screen), screen->caps.buffer_map_persistent_coherent,
          screen->caps.query_buffer_object, screen->caps.query_timestamp, screen->caps.occlusion_query);
   if (!screen->caps.query_buffer_object) { screen->destroy(screen); return 77; }
   struct pipe_context *ctx = screen->context_create(screen, NULL, 0);
   if (!ctx) { screen->destroy(screen); return 2; }
   int status = 0;
   for (unsigned mode = 0; mode < 4 && !status; mode++) status = query_case(screen, ctx, mode);
   ctx->destroy(ctx); screen->destroy(screen); return status;
}
