#include "dirty_tracker.h"
#include <errno.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>

/* Capture at GL consumption boundaries. Application synchronization must keep
 * consumed bytes stable; speculative unrelated bytes may tear and must be
 * recaptured before use. Publication supports disjoint CPU writes, not same-byte
 * CPU/GPU races. Tracker operations and baseline ownership are mutex-serialized. */
struct dirty_tracker {
    size_t size, page_size;
    unsigned char *app, *baseline;
    struct dirty_snapshot *pending;
    struct dirty_stats stats;
    pthread_mutex_t mutex;
};
#if !defined(__aarch64__)
#error Sentinel mapped buffers require the supported ARM64 target
#endif
/* Clang/GCC extended asm implementation boundary, not ISO C atomics.
 * Ordinary coherent Normal memory only. No whole-snapshot atomicity. */
static inline unsigned char machine_load_byte(const unsigned char *p)
{
    unsigned value;
    __asm__ volatile("ldrb %w0, [%1]" : "=r"(value) : "r"(p) : "memory");
    return (unsigned char)value;
}
static void machine_capture(unsigned char *dst, const unsigned char *src, size_t n)
{
    size_t i = 0;
    for (; n - i >= 16; i += 16)
        __asm__ volatile("ldr q0, [%0]\n\tstr q0, [%1]"
                         : : "r"(src + i), "r"(dst + i) : "v0", "memory");
    for (; i < n; ++i) dst[i] = machine_load_byte(src + i);
}
static inline void machine_store_byte(unsigned char *p, unsigned char value)
{
    __asm__ volatile("strb %w0, [%1]" : : "r"((unsigned)value), "r"(p) : "memory");
}
static int fail(int code) { errno = code; return -1; }
static bool bounds(struct dirty_tracker *t, size_t o, size_t n)
{ return o <= t->size && n <= t->size - o; }
static bool overlaps(const void *p, size_t n, const void *base, size_t size)
{
    uintptr_t a = (uintptr_t)p, b = (uintptr_t)base;
    return n && a < b + size && b < a + n;
}
int dirty_tracker_create(size_t size, struct dirty_tracker **out)
{
    if (!out || !size) return fail(EINVAL);
    *out = NULL;
    struct dirty_tracker *t = calloc(1, sizeof(*t));
    if (!t) return -1;
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0 || size > SIZE_MAX - (size_t)page) { free(t); return fail(EOVERFLOW); }
    t->size = size; t->page_size = (size_t)page;
    int error = posix_memalign((void **)&t->app, t->page_size, size);
    t->baseline = calloc(1, size);
    if (error || !t->baseline) {
        free(t->app); free(t->baseline); free(t); return fail(error ? error : ENOMEM);
    }
    memset(t->app, 0, size);
    error = pthread_mutex_init(&t->mutex, NULL);
    if (error) { free(t->app); free(t->baseline); free(t); return fail(error); }
    *out = t; return 0;
}
void *dirty_tracker_mapping(struct dirty_tracker *t) { return t->app; }
size_t dirty_tracker_page_size(struct dirty_tracker *t) { return t->page_size; }
int dirty_tracker_error(struct dirty_tracker *t) { (void)t; return 0; }

/* Peak requested heap bytes for a range: scratch plus snapshot descriptors
 * with one exact maximum allocation on first change. Allocator metadata
 * is outside this explicit-payload budget, as in the existing driver budget.
 * Caller must reserve this amount before capture; zero means overflow/error. */
size_t dirty_tracker_snapshot_reservation(struct dirty_tracker *t, size_t o, size_t n)
{
    if (!bounds(t, o, n)) { errno = EINVAL; return 0; }
    size_t pages = n ? ((o + n - 1) / t->page_size - o / t->page_size + 1) : 0;
    size_t stride = t->page_size + t->page_size / 8;
    if (stride > SIZE_MAX - sizeof(struct dirty_page)) {
        errno = EOVERFLOW; return 0;
    }
    size_t per_page = stride + sizeof(struct dirty_page);
    if (pages > (SIZE_MAX - t->page_size) / per_page) {
        errno = EOVERFLOW; return 0;
    }
    return t->page_size + pages * per_page;
}

static int capture(struct dirty_tracker *t, size_t o, size_t n, bool force,
                   struct dirty_snapshot *out)
{
    if (!out || !bounds(t, o, n)) return fail(EINVAL);
    pthread_mutex_lock(&t->mutex);
    if (t->pending) { pthread_mutex_unlock(&t->mutex); return fail(EBUSY); }
    memset(out, 0, sizeof(*out)); out->page_size = t->page_size;
    size_t capacity = 0, stride = t->page_size + t->page_size / 8;
    int error = 0;
    unsigned char *scratch = malloc(t->page_size);
    if (!scratch) { pthread_mutex_unlock(&t->mutex); return fail(ENOMEM); }
    /* Allocate the maximum payload only after the first changed page. Clean
     * ranges need scratch only; sparse payload pages are not eagerly touched. */
    size_t maximum = n ? ((o + n - 1) / t->page_size - o / t->page_size + 1) : 0;
    for (size_t pos = o; pos < o + n;) {
        size_t base = pos / t->page_size * t->page_size;
        size_t end = base + t->page_size;
        if (end > o + n) end = o + n;
        size_t len = end - pos;
        if (!force) t->stats.compared_bytes += len;
        machine_capture(scratch, t->app + pos, len);
        if (force || memcmp(scratch, t->baseline + pos, len)) {
            if (out->page_count == capacity) {
                size_t next = maximum;
                if (next < capacity || next > SIZE_MAX / stride ||
                    next > SIZE_MAX / sizeof(*out->pages)) { error = EOVERFLOW; break; }
                void *pages = malloc(next * sizeof(*out->pages));
                if (!pages) { error = ENOMEM; break; }
                out->pages = pages;
                void *storage = malloc(next * stride);
                if (!storage) { error = ENOMEM; break; }
                out->storage = storage; capacity = next;
            }
            size_t index = out->page_count++;
            out->pages[index].offset = base;
            unsigned char *bytes = out->storage + index * stride;
            unsigned char *changed = bytes + t->page_size;
            memset(changed, 0, t->page_size / 8);
            memset(bytes, 0, pos - base);
            memset(bytes + end - base, 0, t->page_size - (end - base));
            /* Never read outside requested range, including partial pages. */
            memcpy(bytes + pos - base, scratch, len);
            size_t i = pos - base, limit = end - base;
            for (; i < limit && (i & 7); ++i) {
                if (force || bytes[i] != t->baseline[base + i]) {
                    changed[i / 8] |= 1u << (i % 8); out->changed_bytes++;
                }
            }
            for (; limit - i >= 8; i += 8) {
                uint64_t a, b;
                memcpy(&a, bytes + i, 8); memcpy(&b, t->baseline + base + i, 8);
                uint64_t x = a ^ b;
                if (!force && !x) continue;
                /* Classic has-zero-byte test. False-positive zero candidates
                 * from borrow propagation only take the exact slow path. */
                if (force || !((x - UINT64_C(0x0101010101010101)) &
                              ~x & UINT64_C(0x8080808080808080))) {
                    changed[i / 8] = 255; out->changed_bytes += 8;
                } else {
                    unsigned mask = 0;
                    for (unsigned k = 0; k < 8; ++k)
                        mask |= (unsigned)(bytes[i + k] != t->baseline[base + i + k]) << k;
                    changed[i / 8] = (unsigned char)mask;
                    out->changed_bytes += (size_t)__builtin_popcount(mask);
                }
            }
            for (; i < limit; ++i) {
                if (force || bytes[i] != t->baseline[base + i]) {
                    changed[i / 8] |= 1u << (i % 8); out->changed_bytes++;
                }
            }
            if (force) t->stats.forced_bytes += len;
        }
        pos = end;
    }
    free(scratch);
    if (!error) {
        for (size_t i = 0; i < out->page_count; ++i) {
            out->pages[i].bytes = out->storage + i * stride;
            out->pages[i].changed = out->pages[i].bytes + t->page_size;
        }
        t->pending = out; out->pending_owner = t;
        t->stats.snapshots++; t->stats.captured_pages += out->page_count;
    }
    pthread_mutex_unlock(&t->mutex);
    if (error) { dirty_snapshot_free(out); return fail(error); }
    return 0;
}
int dirty_tracker_snapshot_range(struct dirty_tracker *t, size_t o, size_t n, struct dirty_snapshot *s)
{ return capture(t, o, n, false, s); }
int dirty_tracker_snapshot_range_force(struct dirty_tracker *t, size_t o, size_t n, struct dirty_snapshot *s)
{ return capture(t, o, n, true, s); }
int dirty_tracker_snapshot(struct dirty_tracker *t, struct dirty_snapshot *s)
{ return capture(t, 0, t->size, false, s); }
/* Unmap-only bookkeeping, not GPU upload admission: forget outstanding CPU
 * deltas by making baseline equal the current application bytes. Caller must
 * have stopped ALL users of the mapping and must retire its map generation
 * before any old readback can publish. No payload allocation or GPU writes.
 * Copy, rather than zero, so remap's merge can restore even GPU zero values. */
int dirty_tracker_discard_writes(struct dirty_tracker *t)
{
    pthread_mutex_lock(&t->mutex);
    if (t->pending) { pthread_mutex_unlock(&t->mutex); return fail(EBUSY); }
    machine_capture(t->baseline, t->app, t->size);
    pthread_mutex_unlock(&t->mutex);
    return 0;
}
int dirty_tracker_accept(struct dirty_tracker *t, struct dirty_snapshot *s)
{
    pthread_mutex_lock(&t->mutex);
    if (!s || t->pending != s) { pthread_mutex_unlock(&t->mutex); return fail(EINVAL); }
    for (size_t i = 0; i < s->page_count; ++i)
        for (size_t k = 0; k < t->page_size / 8; ++k) {
            unsigned bits = s->pages[i].changed[k];
            if (!bits) continue;
            size_t j = k * 8;
            if (bits == 255) {
                size_t end = k + 1;
                while (end < t->page_size / 8 && s->pages[i].changed[end] == 255) ++end;
                memcpy(t->baseline + s->pages[i].offset + j, s->pages[i].bytes + j, (end - k) * 8);
                k = end - 1;
                continue;
            }
            for (size_t b = 0; b < 8; ++b) if (bits & (1u << b))
                t->baseline[s->pages[i].offset + j + b] = s->pages[i].bytes[j + b];
        }
    t->pending = NULL; s->pending_owner = NULL;
    pthread_mutex_unlock(&t->mutex); return 0;
}
int dirty_tracker_abort(struct dirty_tracker *t, struct dirty_snapshot *s)
{
    pthread_mutex_lock(&t->mutex);
    if (!s || t->pending != s) { pthread_mutex_unlock(&t->mutex); return fail(EINVAL); }
    t->pending = NULL; s->pending_owner = NULL;
    pthread_mutex_unlock(&t->mutex); return 0;
}
void dirty_snapshot_free(struct dirty_snapshot *s)
{
    if (s->pending_owner) dirty_tracker_abort(s->pending_owner, s);
    free(s->pages); free(s->storage); memset(s, 0, sizeof(*s));
}
static bool source_ok(struct dirty_tracker *t, const void *p, size_t n)
{
    return (!n || p) && n <= UINTPTR_MAX - (uintptr_t)p &&
        !overlaps(p, n, t->app, t->size) && !overlaps(p, n, t->baseline, t->size);
}
static void publish(struct dirty_tracker *t, size_t o, const unsigned char *gpu,
                    size_t n, const unsigned char *skip)
{
    for (size_t i = 0; i < n; ++i) {
        if (skip && (skip[i / 8] & (1u << (i % 8)))) continue;
        if (gpu[i] == t->baseline[o + i]) continue;
        /* No widened stores to neighboring application-owned bytes. Preserve
         * already-observed CPU changes; overlapping concurrent stores are not
         * supported. This is not an arbitrary concurrent-memory CAS protocol. */
        if (machine_load_byte(t->app + o + i) == t->baseline[o + i])
            machine_store_byte(t->app + o + i, gpu[i]);
        t->baseline[o + i] = gpu[i];
    }
}
int dirty_tracker_merge(struct dirty_tracker *t, size_t o, const void *p,
                        size_t n, const unsigned char *skip)
{
    if (!bounds(t, o, n) || !source_ok(t, p, n) ||
        (skip && !source_ok(t, skip, n / 8 + !!(n % 8)))) return fail(EINVAL);
    pthread_mutex_lock(&t->mutex);
    if (t->pending) { pthread_mutex_unlock(&t->mutex); return fail(EBUSY); }
    publish(t, o, p, n, skip);
    pthread_mutex_unlock(&t->mutex); return 0;
}
int dirty_tracker_readbackv(struct dirty_tracker *t, const struct dirty_readback *s, size_t count)
{
    if ((!s && count) || count > SIZE_MAX / sizeof(*s)) return fail(EINVAL);
    size_t end = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!bounds(t, s[i].offset, s[i].length) || !source_ok(t, s[i].bytes, s[i].length)) return fail(EINVAL);
        if (!s[i].length) continue;
        if (s[i].offset < end) return fail(EINVAL);
        end = s[i].offset + s[i].length;
    }
    pthread_mutex_lock(&t->mutex);
    if (t->pending) { pthread_mutex_unlock(&t->mutex); return fail(EBUSY); }
    for (size_t i = 0; i < count; ++i) publish(t, s[i].offset, s[i].bytes, s[i].length, NULL);
    pthread_mutex_unlock(&t->mutex); return 0;
}
int dirty_tracker_readback(struct dirty_tracker *t, size_t o, const void *p, size_t n)
{ const struct dirty_readback s = {o, p, n}; return dirty_tracker_readbackv(t, &s, 1); }
int dirty_tracker_stats(struct dirty_tracker *t, struct dirty_stats *out)
{ pthread_mutex_lock(&t->mutex); *out = t->stats; pthread_mutex_unlock(&t->mutex); return 0; }
void dirty_tracker_destroy(struct dirty_tracker *t)
{
    if (!t) return;
    if (t->pending) t->pending->pending_owner = NULL;
    pthread_mutex_destroy(&t->mutex); free(t->app); free(t->baseline); free(t);
}
