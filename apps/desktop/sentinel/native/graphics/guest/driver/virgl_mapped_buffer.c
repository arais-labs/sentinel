#include "virgl_mapped_buffer.h"
#include "virgl_context.h"
#include "virgl_resource.h"
#include "virgl_screen.h"
#include "virgl_encode.h"
#include "virgl_inline_write.h"
#include "dirty_tracker.h"
#include "mapped_mask.h"
#include "mc_merge.h"
#include "c11/threads.h"
#include "util/u_inlines.h"
#include "util/simple_mtx.h"
#include "util/os_time.h"
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>

struct read_state {
   struct read_state *next;
   struct pipe_resource *resource;
   struct dirty_tracker *tracker;
   struct mc_merge *merge;
   struct mc_merge_key key;
   struct pipe_transfer *map;
   uint64_t generation;
   size_t charged_bytes;
   bool coherent;
   bool uploading;
   bool host_written;
};
struct read_record {
   struct read_record *next;
   struct read_state *state;
   struct pipe_resource *source, *staging;
   unsigned offset, length;
   uint64_t generation;
   uint64_t ticket;
   size_t charge;
};
struct read_context {
   struct read_context *next;
   struct virgl_context *ctx;
   struct read_record *pending, **tail;
   bool failed;
   bool capturing;
   bool uploads;
};
struct read_batch {
   struct read_batch *next;
   struct virgl_context *owner;
   struct pipe_screen *screen;
   struct pipe_fence_handle *fence;
   struct read_record *records;
   uint64_t serial;
   bool failed;
};
struct virgl_mapped_screen_state {
   simple_mtx_t lock;
   mtx_t submit_lock;
   struct read_state *states;
   struct read_context *contexts;
   struct read_batch *batches;
   uint64_t next_serial;
   uint64_t next_memory_sequence, next_allocation;
   size_t retained_bytes;
   size_t shadow_bytes, state_count;
   size_t snapshot_bytes;
   size_t batch_count;
   bool failed;
   bool storage_supported;
   uint64_t start_ns, consumer_checks, upload_snapshots, upload_bytes;
   uint64_t inline_packets, read_captures, capture_bytes, published_bytes;
   struct dirty_stats tracker_stats;
};
#define READ_BUDGET (32u * 1024u * 1024u)
#define READ_SHADOW_BUDGET (32u * 1024u * 1024u)
#define READ_MAX_STATES 16u
#define READ_MAX_BATCHES 256u

static bool publish_locked(struct pipe_screen *screen);

static bool batch_publication_blocked(const struct read_batch *batch)
{
   for (const struct read_record *r = batch->records; r; r = r->next)
      if (r->ticket && r->generation == r->state->generation &&
          r->state->uploading)
         return true;
   return false;
}

/* Called with submit_lock held. Retire completed work on ordinary submissions,
 * not only when an application explicitly waits on a fence. At the hard budget
 * apply backpressure to the oldest submission; never discard valid commands. */
static bool make_read_room(struct pipe_screen *screen, size_t bytes,
                           unsigned batches)
{
   struct virgl_mapped_screen_state *s = virgl_screen(screen)->mapped_buffer;
   struct virgl_winsys *vws = virgl_screen(screen)->vws;
   if (bytes > READ_BUDGET || batches > READ_MAX_BATCHES)
      return false;
   for (;;) {
      if (!publish_locked(screen))
         return false;
      simple_mtx_lock(&s->lock);
      bool room = bytes <= READ_BUDGET - s->retained_bytes &&
                  batches <= READ_MAX_BATCHES - s->batch_count;
      struct pipe_fence_handle *fence = NULL;
      if (!room) {
         for (struct read_batch *batch = s->batches; batch; batch = batch->next) {
            if (!batch_publication_blocked(batch)) {
               vws->fence_reference(vws, &fence, batch->fence);
               break;
            }
         }
      }
      simple_mtx_unlock(&s->lock);
      if (room)
         return true;
      if (!fence)
         return false;
      bool ready = vws->fence_wait(vws, fence, UINT64_MAX);
      vws->fence_reference(vws, &fence, NULL);
      if (!ready)
         return false;
   }
}

bool virgl_mapped_screen_init(struct pipe_screen *screen)
{
   struct virgl_mapped_screen_state *s = calloc(1, sizeof(*s));
   if (!s)
      return false;
   simple_mtx_init(&s->lock, mtx_plain);
   if (mtx_init(&s->submit_lock, mtx_timed | mtx_recursive) != thrd_success) {
      simple_mtx_destroy(&s->lock);
      free(s);
      return false;
   }
   s->next_serial = 1;
   s->start_ns = os_time_get_nano();
   struct dirty_tracker *probe = NULL;
   long page = sysconf(_SC_PAGESIZE);
   if (page > 0 && !dirty_tracker_create(page, &probe)) {
      s->storage_supported = true;
      dirty_tracker_destroy(probe);
   }
   if (!s->storage_supported) {
      fprintf(stderr, "Sentinel graphics could not allocate mapped-buffer tracking storage\n");
      mtx_destroy(&s->submit_lock);
      simple_mtx_destroy(&s->lock);
      free(s);
      return false;
   }
   virgl_screen(screen)->mapped_buffer = s;
   return true;
}

bool virgl_mapped_supports_storage(struct pipe_screen *screen)
{
   return virgl_screen(screen)->mapped_buffer->storage_supported;
}

void virgl_mapped_screen_destroy(struct pipe_screen *screen)
{
   struct virgl_mapped_screen_state *s = virgl_screen(screen)->mapped_buffer;
   if (!s)
      return;
   assert(!s->states && !s->contexts && !s->batches && !s->retained_bytes &&
          !s->shadow_bytes && !s->state_count && !s->snapshot_bytes && !s->batch_count);
   if (getenv("SENTINEL_STORAGE_REPORT"))
      fprintf(stderr, "storage_stats wall_ns=%llu consumer_checks=%llu upload_snapshots=%llu upload_bytes=%llu inline_packets=%llu read_captures=%llu capture_bytes=%llu published_bytes=%llu compared_bytes=%llu\n",
         (unsigned long long)(os_time_get_nano() - s->start_ns),
         (unsigned long long)s->consumer_checks,
         (unsigned long long)s->upload_snapshots,
         (unsigned long long)s->upload_bytes,
         (unsigned long long)s->inline_packets,
         (unsigned long long)s->read_captures,
         (unsigned long long)s->capture_bytes,
         (unsigned long long)s->published_bytes,
         (unsigned long long)s->tracker_stats.compared_bytes);
   mtx_destroy(&s->submit_lock);
   simple_mtx_destroy(&s->lock);
   free(s);
   virgl_screen(screen)->mapped_buffer = NULL;
}

void virgl_mapped_submit_lock(struct pipe_screen *screen)
{
   mtx_lock(&virgl_screen(screen)->mapped_buffer->submit_lock);
}

void virgl_mapped_submit_unlock(struct pipe_screen *screen)
{
   mtx_unlock(&virgl_screen(screen)->mapped_buffer->submit_lock);
}

bool virgl_mapped_resource(const struct pipe_resource *r)
{
   return r->target == PIPE_BUFFER &&
          (r->flags & PIPE_RESOURCE_FLAG_MAP_PERSISTENT);
}

void virgl_mapped_host_write(struct pipe_resource *resource)
{
   if (!virgl_mapped_resource(resource))
      return;
   struct virgl_mapped_screen_state *s = virgl_screen(resource->screen)->mapped_buffer;
   simple_mtx_lock(&s->lock);
   for (struct read_state *state = s->states; state; state = state->next) {
      if (state->resource == resource && state->map) {
         state->host_written = true;
         break;
      }
   }
   simple_mtx_unlock(&s->lock);
}

static struct read_context *context_locked(struct virgl_mapped_screen_state *s, struct virgl_context *ctx)
{
   for (struct read_context *c = s->contexts; c; c = c->next)
      if (c->ctx == ctx)
         return c;
   struct read_context *c = calloc(1, sizeof(*c));
   if (!c)
      s->failed = true;
   if (c) {
      c->ctx = ctx;
      c->tail = &c->pending;
      c->next = s->contexts;
      s->contexts = c;
   }
   return c;
}

static void *map_internal(struct pipe_context *ctx, struct pipe_resource *resource,
                    unsigned usage, const struct pipe_box *box,
                    struct pipe_transfer **transfer)
{
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->screen)->mapped_buffer;
   if (!(usage & (PIPE_MAP_READ | PIPE_MAP_WRITE)) ||
       ((usage & PIPE_MAP_COHERENT) && !(resource->flags & PIPE_RESOURCE_FLAG_MAP_COHERENT)) ||
       box->x < 0 ||
       box->width <= 0 || (unsigned)box->x > resource->width0 ||
       (unsigned)box->width > resource->width0 - box->x)
      return NULL;
   struct virgl_context *vctx = virgl_context(ctx);
   struct virgl_winsys *vws = virgl_screen(ctx->screen)->vws;
   struct virgl_resource *res = virgl_resource(resource);
   simple_mtx_lock(&s->lock);
   struct read_state *state = s->states;
   while (state && state->resource != resource)
      state = state->next;
   if (state && state->map) {
      simple_mtx_unlock(&s->lock);
      return NULL;
   }
   if (!state) {
      long page = sysconf(_SC_PAGESIZE);
      if (page <= 0 || (size_t)resource->width0 > SIZE_MAX - (size_t)page + 1 ||
          s->state_count >= READ_MAX_STATES || s->failed) {
         simple_mtx_unlock(&s->lock);
         return NULL;
      }
      size_t size = ((size_t)resource->width0 + page - 1) / page * page;
      size_t metadata = size / page * (sizeof(size_t) + 1) + sizeof(*state);
      if (!size || size > (SIZE_MAX - metadata) / 2) {
         simple_mtx_unlock(&s->lock);
         return NULL;
      }
      size_t charged = 2 * size + metadata;
      if (charged > READ_SHADOW_BUDGET - s->shadow_bytes) {
         simple_mtx_unlock(&s->lock);
         return NULL;
      }
      state = calloc(1, sizeof(*state));
      if (!state || !size || dirty_tracker_create(size, &state->tracker)) {
         free(state);
         simple_mtx_unlock(&s->lock);
         return NULL;
      }
      state->resource = resource;
      state->key = (struct mc_merge_key){++s->next_allocation, 1, 1};
      if (mc_merge_create(state->key, resource->width0, READ_MAX_BATCHES,
                          READ_BUDGET, &state->merge)) {
         dirty_tracker_destroy(state->tracker);
         free(state);
         simple_mtx_unlock(&s->lock);
         return NULL;
      }
      struct mc_merge_stats stats;
      mc_merge_get_stats(state->merge, &stats);
      if (stats.charged_bytes > READ_SHADOW_BUDGET - s->shadow_bytes - charged) {
         mc_merge_destroy(state->merge);
         dirty_tracker_destroy(state->tracker);
         free(state);
         simple_mtx_unlock(&s->lock);
         return NULL;
      }
      charged += stats.charged_bytes;
      state->charged_bytes = charged;
      s->shadow_bytes += charged;
      s->state_count++;
      state->next = s->states;
      s->states = state;
   }
   struct read_context *c = context_locked(s, vctx);
   state->coherent = usage & PIPE_MAP_COHERENT;
   state->host_written |= res->bind_history &
      (PIPE_BIND_SHADER_BUFFER | PIPE_BIND_SHADER_IMAGE | PIPE_BIND_STREAM_OUTPUT);
   state->host_written |= resource->bind & (PIPE_BIND_SHARED | PIPE_BIND_GLOBAL);
   state->generation++;
   state->key.map_generation = state->generation;
   if (mc_merge_set_active(state->merge, state->key, resource->width0))
      s->failed = true;
   simple_mtx_unlock(&s->lock);
   if (!c)
      return NULL;
   virgl_flush_eq(vctx, NULL, NULL);
   if (vws->transfer_get(vws, res->hw_res, box, 0, 0, box->x, 0))
      return NULL;
   vws->resource_wait(vws, res->hw_res);
   void *native = vws->resource_map(vws, res->hw_res);
   if (!native || dirty_tracker_readback(state->tracker, box->x,
                                         (char *)native + box->x, box->width))
      return NULL;
   struct virgl_transfer *trans = virgl_resource_create_transfer(vctx, resource,
      &res->metadata, 0, usage | PIPE_MAP_PERSISTENT |
      (state->coherent ? PIPE_MAP_COHERENT : 0), box);
   if (!trans)
      return NULL;
   simple_mtx_lock(&s->lock);
   state->map = &trans->base;
   simple_mtx_unlock(&s->lock);
   *transfer = &trans->base;
   return (char *)dirty_tracker_mapping(state->tracker) + box->x;
}

void *virgl_mapped_map(struct pipe_context *ctx, struct pipe_resource *resource,
                    unsigned usage, const struct pipe_box *box,
                    struct pipe_transfer **transfer)
{
   virgl_mapped_submit_lock(ctx->screen);
   void *result = map_internal(ctx, resource, usage, box, transfer);
   virgl_mapped_submit_unlock(ctx->screen);
   return result;
}

void virgl_mapped_unmap(struct pipe_context *ctx, struct pipe_transfer *transfer)
{
   virgl_mapped_submit_lock(ctx->screen);
   if ((transfer->usage & PIPE_MAP_WRITE) &&
       !(transfer->usage & (PIPE_MAP_FLUSH_EXPLICIT | PIPE_MAP_COHERENT))) {
      struct pipe_box box = {.width = transfer->box.width, .height = 1, .depth = 1};
      virgl_mapped_flush_region(ctx, transfer, &box);
   }
   if ((transfer->usage & (PIPE_MAP_COHERENT | PIPE_MAP_WRITE)) ==
       (PIPE_MAP_COHERENT | PIPE_MAP_WRITE))
      virgl_mapped_before_consumer(ctx);
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->screen)->mapped_buffer;
   simple_mtx_lock(&s->lock);
   for (struct read_state *state = s->states; state; state = state->next)
      if (state->map == transfer)
      {
         if (transfer->usage & PIPE_MAP_WRITE) {
            if (dirty_tracker_discard_writes(state->tracker))
               s->failed = true;
         }
         state->map = NULL;
         state->generation++;
         state->key.map_generation = state->generation;
         if (mc_merge_set_active(state->merge, state->key, state->resource->width0))
            s->failed = true;
      }
   simple_mtx_unlock(&s->lock);
   virgl_resource_destroy_transfer(virgl_context(ctx), virgl_transfer(transfer));
   virgl_mapped_submit_unlock(ctx->screen);
}

struct upload_accept {
   struct dirty_tracker *tracker;
   struct dirty_snapshot *snapshot;
};

static int accept_upload(void *data)
{
   struct upload_accept *accept = data;
   return dirty_tracker_accept(accept->tracker, accept->snapshot) ? errno : 0;
}

static void upload_region(struct pipe_context *ctx,
                            struct pipe_transfer *transfer,
                            struct pipe_transfer *identity, uint64_t generation,
                            const struct pipe_box *box, bool authoritative)
{
   struct virgl_context *vctx = virgl_context(ctx);
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->screen)->mapped_buffer;
   if (!(transfer->usage & PIPE_MAP_WRITE) || box->x < 0 || box->width < 0 ||
       box->x > transfer->box.width || box->width > transfer->box.width - box->x)
      return;
   if (!box->width)
      return;
   simple_mtx_lock(&s->lock);
   struct read_context *c = context_locked(s, vctx);
   struct read_state *state = s->states;
   while (state && state->map != identity)
      state = state->next;
   if (!c || !state || (generation && state->generation != generation) ||
       c->capturing || s->failed) {
      if (c && s->failed)
         c->failed = true;
      simple_mtx_unlock(&s->lock);
      return;
   }
   size_t offset = (size_t)transfer->box.x + box->x;
   size_t charge = dirty_tracker_snapshot_reservation(state->tracker, offset, box->width);
   if (!charge || charge > READ_BUDGET - s->snapshot_bytes) {
      c->failed = true;
      simple_mtx_unlock(&s->lock);
      return;
   }
   simple_mtx_unlock(&s->lock);
   bool room = make_read_room(ctx->screen, 0, 1);
   simple_mtx_lock(&s->lock);
   if (!room) {
      c->failed = true;
      s->failed = true;
      simple_mtx_unlock(&s->lock);
      return;
   }
   s->snapshot_bytes += charge;
   c->capturing = true;
   state->uploading = true;
   simple_mtx_unlock(&s->lock);

   struct dirty_snapshot snapshot = {0};
   bool okay = !(authoritative ? dirty_tracker_snapshot_range_force(state->tracker,
      offset, box->width, &snapshot) : dirty_tracker_snapshot_range(state->tracker,
      offset, box->width, &snapshot));
   if (okay) {
      simple_mtx_lock(&s->lock);
      s->upload_snapshots++;
      s->upload_bytes += snapshot.changed_bytes;
      simple_mtx_unlock(&s->lock);
   }
   size_t range_count = 0;
   struct mc_merge_range *ranges = NULL;
   if (okay && snapshot.changed_bytes) {
      size_t count = 0;
      for (size_t p = 0; p < snapshot.page_count; p++)
         count += mapped_mask_runs(snapshot.pages[p].changed, snapshot.page_size);
      simple_mtx_lock(&s->lock);
      if (count > (READ_BUDGET - s->snapshot_bytes) / sizeof(*ranges))
         okay = false;
      else {
         size_t extra = count * sizeof(*ranges);
         s->snapshot_bytes += extra;
         charge += extra;
      }
      simple_mtx_unlock(&s->lock);
      if (okay) {
         ranges = calloc(count, sizeof(*ranges));
         if (!ranges)
            okay = false;
      }
   }
   for (size_t p = 0; okay && p < snapshot.page_count; p++) {
      struct dirty_page *entry = &snapshot.pages[p];
      for (size_t i = 0; okay && i < snapshot.page_size;) {
         i = mapped_mask_next(entry->changed, i, snapshot.page_size, true);
         if (i == snapshot.page_size)
            break;
         size_t end = mapped_mask_next(entry->changed, i, snapshot.page_size, false);
         ranges[range_count++] = (struct mc_merge_range){entry->offset + i, end - i};
         while (okay && i < end) {
            size_t capacity = virgl_inline_buffer_max_bytes(vctx);
            if (!capacity) {
               virgl_flush_eq(vctx, NULL, NULL);
               capacity = virgl_inline_buffer_max_bytes(vctx);
            }
            size_t length = MIN2(capacity, end - i);
            okay = length && virgl_inline_buffer_write(vctx, transfer->resource,
                        entry->offset + i, entry->bytes + i, length);
            if (okay) {
               simple_mtx_lock(&s->lock);
               c->uploads = true;
               s->inline_packets++;
               simple_mtx_unlock(&s->lock);
            }
            i += length;
         }
      }
   }
   if (okay && range_count) {
      virgl_flush_eq(vctx, NULL, NULL);
      simple_mtx_lock(&s->lock);
      okay = !c->failed && !s->failed;
      simple_mtx_unlock(&s->lock);
   }
   if (okay) {
      struct upload_accept accept = {state->tracker, &snapshot};
      simple_mtx_lock(&s->lock);
      okay = !mc_merge_accept_cpu(state->merge, state->key,
         ++s->next_memory_sequence, ranges, range_count, accept_upload, &accept);
      simple_mtx_unlock(&s->lock);
   }
   if (!okay && snapshot.pending_owner)
      dirty_tracker_abort(state->tracker, &snapshot);
   dirty_snapshot_free(&snapshot);
   free(ranges);
   simple_mtx_lock(&s->lock);
   s->snapshot_bytes -= charge;
   state->uploading = false;
   if (!okay) {
      c->failed = true;
      s->failed = true;
   }
   simple_mtx_unlock(&s->lock);
   simple_mtx_lock(&s->lock);
   c->capturing = false;
   simple_mtx_unlock(&s->lock);
}

void virgl_mapped_flush_region(struct pipe_context *ctx,
                            struct pipe_transfer *transfer,
                            const struct pipe_box *box)
{
   virgl_mapped_submit_lock(ctx->screen);
   upload_region(ctx, transfer, transfer, 0, box, true);
   virgl_mapped_submit_unlock(ctx->screen);
}

void virgl_mapped_before_consumer(struct pipe_context *ctx)
{
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->screen)->mapped_buffer;
   struct {
      struct pipe_transfer transfer;
      struct pipe_transfer *identity;
      struct pipe_resource *resource;
      uint64_t generation;
   } maps[READ_MAX_STATES];
   unsigned count = 0;
   virgl_mapped_submit_lock(ctx->screen);
   simple_mtx_lock(&s->lock);
   struct read_context *c = context_locked(s, virgl_context(ctx));
   s->consumer_checks++;
   if (!c || c->capturing || s->failed) {
      simple_mtx_unlock(&s->lock);
      virgl_mapped_submit_unlock(ctx->screen);
      return;
   }
   for (struct read_state *state = s->states; state; state = state->next) {
      if (!state->map || !state->coherent || !(state->map->usage & PIPE_MAP_WRITE))
         continue;
      maps[count].transfer = *state->map;
      maps[count].identity = state->map;
      maps[count].generation = state->generation;
      maps[count].resource = NULL;
      pipe_resource_reference(&maps[count].resource, state->resource);
      count++;
   }
   simple_mtx_unlock(&s->lock);
   for (unsigned i = 0; i < count; i++) {
      struct pipe_box box = {.width = maps[i].transfer.box.width, .height = 1, .depth = 1};
      upload_region(ctx, &maps[i].transfer, maps[i].identity, maps[i].generation,
                    &box, false);
      pipe_resource_reference(&maps[i].resource, NULL);
   }
   virgl_mapped_submit_unlock(ctx->screen);
}

void virgl_mapped_resource_destroy(struct pipe_resource *resource)
{
   struct virgl_mapped_screen_state *s = virgl_screen(resource->screen)->mapped_buffer;
   simple_mtx_lock(&s->lock);
   struct read_state **p = &s->states;
   while (*p && (*p)->resource != resource)
      p = &(*p)->next;
   struct read_state *state = *p;
   if (state)
      *p = state->next;
   simple_mtx_unlock(&s->lock);
   if (state) {
      struct dirty_stats stats = {0};
      dirty_tracker_stats(state->tracker, &stats);
      mc_merge_destroy(state->merge);
      dirty_tracker_destroy(state->tracker);
      simple_mtx_lock(&s->lock);
      s->shadow_bytes -= state->charged_bytes;
      s->state_count--;
      s->tracker_stats.compared_bytes += stats.compared_bytes;
      simple_mtx_unlock(&s->lock);
      free(state);
   }
}

static void record_free(struct virgl_mapped_screen_state *s, struct read_record *r)
{
   simple_mtx_lock(&s->lock);
   if (r->ticket)
      mc_merge_discard(r->state->merge, r->ticket);
   s->retained_bytes -= r->charge;
   simple_mtx_unlock(&s->lock);
   pipe_resource_reference(&r->staging, NULL);
   pipe_resource_reference(&r->source, NULL);
   free(r);
}

static void capture_reads(struct virgl_context *ctx, bool coherent)
{
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->base.screen)->mapped_buffer;
   struct read_record *records = NULL;
   simple_mtx_lock(&s->lock);
   struct read_context *c = context_locked(s, ctx);
   if (!c || c->capturing) {
      simple_mtx_unlock(&s->lock);
      return;
   }
   c->capturing = true;
   size_t required = 0;
   for (struct read_state *state = s->states; state; state = state->next) {
      if (!state->map || !state->host_written ||
          ((state->coherent || (state->map->usage & PIPE_MAP_WRITE)) != coherent))
         continue;
      size_t length = state->map->box.width;
      required += length + (length + 7u) / 8u + sizeof(struct read_record);
   }
   simple_mtx_unlock(&s->lock);
   bool room = make_read_room(ctx->base.screen, required, 1);
   simple_mtx_lock(&s->lock);
   if (!room) {
      c->failed = true;
      c->capturing = false;
      simple_mtx_unlock(&s->lock);
      return;
   }
   for (struct read_state *state = s->states; state; state = state->next) {
      if (!state->map || !state->host_written ||
          ((state->coherent || (state->map->usage & PIPE_MAP_WRITE)) != coherent))
         continue;
      unsigned length = state->map->box.width;
      size_t charge = length + (length + 7u) / 8u + sizeof(struct read_record);
      if (charge > READ_BUDGET - s->retained_bytes) {
         c->failed = true;
         break;
      }
      struct read_record *r = calloc(1, sizeof(*r));
      if (!r) {
         c->failed = true;
         break;
      }
      r->state = state;
      r->generation = state->generation;
      r->offset = state->map->box.x;
      r->length = length;
      r->charge = charge;
      if (mc_merge_capture(state->merge, state->key, ++s->next_memory_sequence,
                           r->offset, r->length, NULL, NULL, &r->ticket)) {
         c->failed = true;
         free(r);
         break;
      }
      pipe_resource_reference(&r->source, state->resource);
      s->retained_bytes += charge;
      s->read_captures++;
      s->capture_bytes += length;
      r->next = records;
      records = r;
   }
   simple_mtx_unlock(&s->lock);
   if (records)
      virgl_encode_memory_barrier(ctx, PIPE_BARRIER_UPDATE_BUFFER);
   while (records) {
      struct read_record *r = records;
      records = records->next;
      r->next = NULL;
      struct pipe_resource desc = {
         .target = PIPE_BUFFER, .format = PIPE_FORMAT_R8_UNORM,
         .width0 = r->length, .height0 = 1, .depth0 = 1, .array_size = 1,
         .usage = PIPE_USAGE_STAGING, .bind = PIPE_BIND_VERTEX_BUFFER,
      };
      r->staging = ctx->base.screen->resource_create(ctx->base.screen, &desc);
      if (!r->staging) {
         simple_mtx_lock(&s->lock);
         c->failed = true;
         simple_mtx_unlock(&s->lock);
         record_free(s, r);
         continue;
      }
      struct pipe_box box = {.x = r->offset, .width = r->length, .height = 1, .depth = 1};
      ctx->base.resource_copy_region(&ctx->base, r->staging, 0, 0, 0, 0,
                                     r->source, 0, &box);
      simple_mtx_lock(&s->lock);
      *c->tail = r;
      c->tail = &r->next;
      simple_mtx_unlock(&s->lock);
   }
   simple_mtx_lock(&s->lock);
   c->capturing = false;
   simple_mtx_unlock(&s->lock);
}

void virgl_mapped_barrier(struct virgl_context *ctx)
{
   virgl_mapped_submit_lock(ctx->base.screen);
   virgl_mapped_before_consumer(&ctx->base);
   capture_reads(ctx, false);
   virgl_flush_eq(ctx, NULL, NULL);
   virgl_mapped_submit_unlock(ctx->base.screen);
}

void virgl_mapped_prepare_flush(struct virgl_context *ctx)
{
   virgl_mapped_before_consumer(&ctx->base);
   capture_reads(ctx, true);
}

static int merge_readback(void *data, size_t offset, const void *bytes,
                          size_t length, const unsigned char *superseded)
{
   struct read_state *state = data;
   return dirty_tracker_merge(state->tracker, offset, bytes, length, superseded) ? errno : 0;
}

bool virgl_mapped_flush(struct virgl_context *ctx, struct pipe_fence_handle **fence)
{
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->base.screen)->mapped_buffer;
   bool room = make_read_room(ctx->base.screen, 0, 1);
   simple_mtx_lock(&s->lock);
   struct read_context *c = s->contexts;
   while (c && c->ctx != ctx)
      c = c->next;
   if (!c || (!c->pending && !c->failed && !c->uploads)) {
      simple_mtx_unlock(&s->lock);
      return false;
   }
   if (!room) {
      c->failed = true;
      s->failed = true;
      simple_mtx_unlock(&s->lock);
      return true;
   }
   struct read_batch *batch = calloc(1, sizeof(*batch));
   if (!batch) {
      c->failed = true;
      s->failed = true;
      simple_mtx_unlock(&s->lock);
      return true;
   }
   batch->records = c->pending;
   s->batch_count++;
   c->pending = NULL;
   c->tail = &c->pending;
   c->uploads = false;
   batch->failed = c->failed;
   batch->screen = ctx->base.screen;
   batch->owner = ctx;
   simple_mtx_unlock(&s->lock);
   struct virgl_winsys *vws = virgl_screen(ctx->base.screen)->vws;
   if (!batch->failed && vws->submit_cmd(vws, ctx->cbuf, NULL))
      batch->failed = true;
   for (struct read_record *r = batch->records; r && !batch->failed; r = r->next) {
      struct pipe_box box = {.width = r->length, .height = 1, .depth = 1};
      if (vws->transfer_get(vws, virgl_resource(r->staging)->hw_res,
                             &box, 0, 0, 0, 0))
         batch->failed = true;
   }
   if (!batch->failed) {
      virgl_encoder_set_sub_ctx(ctx, ctx->hw_sub_ctx_id);
      if (vws->submit_cmd(vws, ctx->cbuf, &batch->fence) || !batch->fence)
         batch->failed = true;
   }
   if (fence && !batch->failed)
      vws->fence_reference(vws, fence, batch->fence);
   simple_mtx_lock(&s->lock);
   if (batch->failed) {
      c->failed = true;
      s->failed = true;
   }
   batch->serial = s->next_serial++;
   struct read_batch **tail = &s->batches;
   while (*tail)
      tail = &(*tail)->next;
   *tail = batch;
   simple_mtx_unlock(&s->lock);
   return true;
}

static bool publish_locked(struct pipe_screen *screen)
{
   struct virgl_mapped_screen_state *s = virgl_screen(screen)->mapped_buffer;
   struct virgl_winsys *vws = virgl_screen(screen)->vws;
   for (;;) {
      simple_mtx_lock(&s->lock);
      if (s->failed) {
         simple_mtx_unlock(&s->lock);
         return false;
      }
      struct read_batch **p = &s->batches;
      while (*p && ((*p)->screen != screen || batch_publication_blocked(*p) ||
             ((*p)->fence && !vws->fence_wait(vws, (*p)->fence, 0))))
         p = &(*p)->next;
      struct read_batch *batch = *p;
      if (!batch) {
         simple_mtx_unlock(&s->lock);
         return true;
      }
      if (batch->failed) {
         simple_mtx_unlock(&s->lock);
         return false;
      }
      for (struct read_record *r = batch->records; r; r = r->next) {
         if (!r->ticket)
            continue;
         if (r->generation != r->state->generation)
            continue;
         void *native = vws->resource_map(vws, virgl_resource(r->staging)->hw_res);
         if (!native || mc_merge_publish(r->state->merge, r->ticket, native,
                                         r->length, merge_readback, r->state)) {
            batch->failed = true;
            simple_mtx_unlock(&s->lock);
            return false;
         }
         r->ticket = 0;
         s->published_bytes += r->length;
      }
      *p = batch->next;
      s->batch_count--;
      simple_mtx_unlock(&s->lock);
      while (batch->records) {
         struct read_record *r = batch->records;
         batch->records = r->next;
         record_free(s, r);
      }
      vws->fence_reference(vws, &batch->fence, NULL);
      free(batch);
   }
}

bool virgl_mapped_publish_timeout(struct pipe_screen *screen, uint64_t timeout)
{
   struct virgl_mapped_screen_state *s = virgl_screen(screen)->mapped_buffer;
   int locked;
   if (!timeout)
      locked = mtx_trylock(&s->submit_lock);
   else if (timeout == UINT64_MAX)
      locked = mtx_lock(&s->submit_lock);
   else {
      struct timespec deadline;
      if (clock_gettime(CLOCK_REALTIME, &deadline))
         return false;
      uint64_t ns = (uint64_t)deadline.tv_nsec + timeout % 1000000000ull;
      deadline.tv_sec += timeout / 1000000000ull + ns / 1000000000ull;
      deadline.tv_nsec = ns % 1000000000ull;
      locked = mtx_timedlock(&s->submit_lock, &deadline);
   }
   if (locked != thrd_success)
      return false;
   bool result = publish_locked(screen);
   mtx_unlock(&s->submit_lock);
   return result;
}

bool virgl_mapped_publish(struct pipe_screen *screen)
{
   return virgl_mapped_publish_timeout(screen, UINT64_MAX);
}

int virgl_mapped_fence_get_fd(struct pipe_screen *screen,
                              struct pipe_fence_handle *fence)
{
   struct virgl_mapped_screen_state *s = virgl_screen(screen)->mapped_buffer;
   struct virgl_winsys *vws = virgl_screen(screen)->vws;
   int fd = -1;

   /* An external sync-file waiter cannot run our mapped-memory publication.
    * Serialize against submissions and finish pending readbacks before handing
    * out the native fence. With no readbacks this remains an asynchronous native
    * export: do not wait for ordinary draws or upload-only submissions.
    * Conservatively include all submitted readbacks, including other contexts.
    */
   mtx_lock(&s->submit_lock);
   for (;;) {
      if (!publish_locked(screen))
         break;
      simple_mtx_lock(&s->lock);
      struct read_batch *batch = s->batches;
      while (batch && !batch->records)
         batch = batch->next;
      if (!batch) {
         simple_mtx_unlock(&s->lock);
         fd = vws->fence_get_fd(vws, fence);
         break;
      }
      /* A recursive export during an upload cannot safely publish or wait for
       * that upload. Fail closed rather than exporting premature completion. */
      if (batch->failed || batch_publication_blocked(batch) || !batch->fence) {
         simple_mtx_unlock(&s->lock);
         break;
      }
      struct pipe_fence_handle *pending = NULL;
      vws->fence_reference(vws, &pending, batch->fence);
      simple_mtx_unlock(&s->lock);
      bool ready = vws->fence_wait(vws, pending, UINT64_MAX);
      vws->fence_reference(vws, &pending, NULL);
      if (!ready)
         break;
   }
   mtx_unlock(&s->submit_lock);
   return fd;
}

void virgl_mapped_context_destroy(struct virgl_context *ctx)
{
   struct virgl_mapped_screen_state *s = virgl_screen(ctx->base.screen)->mapped_buffer;
   struct virgl_winsys *vws = virgl_screen(ctx->base.screen)->vws;
   simple_mtx_lock(&s->lock);
   struct read_context **p = &s->contexts;
   while (*p && (*p)->ctx != ctx)
      p = &(*p)->next;
   struct read_context *c = *p;
   simple_mtx_unlock(&s->lock);
   if (!c)
      return;
   struct pipe_fence_handle *fence = NULL;
   virgl_flush_eq(ctx, NULL, &fence);
   if (fence) {
      vws->fence_wait(vws, fence, UINT64_MAX);
      virgl_mapped_publish(ctx->base.screen);
      vws->fence_reference(vws, &fence, NULL);
   }
   struct read_batch *discard = NULL;
   simple_mtx_lock(&s->lock);
   struct read_batch **b = &s->batches;
   while (*b) {
      if ((*b)->owner != ctx) {
         b = &(*b)->next;
         continue;
      }
      struct read_batch *batch = *b;
      *b = batch->next;
      s->batch_count--;
      batch->next = discard;
      discard = batch;
   }
   p = &s->contexts;
   while (*p && *p != c)
      p = &(*p)->next;
   if (*p)
      *p = c->next;
   simple_mtx_unlock(&s->lock);
   while (discard) {
      struct read_batch *batch = discard;
      discard = batch->next;
      if (batch->fence) {
         vws->fence_wait(vws, batch->fence, UINT64_MAX);
         vws->fence_reference(vws, &batch->fence, NULL);
      }
      while (batch->records) {
         struct read_record *r = batch->records;
         batch->records = r->next;
         record_free(s, r);
      }
      free(batch);
   }
   while (c->pending) {
      struct read_record *r = c->pending;
      c->pending = r->next;
      record_free(s, r);
   }
   free(c);
}
