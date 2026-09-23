#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/mman.h>

union block { struct { size_t size; } h; max_align_t align; };
static size_t live, peak, ceiling = SIZE_MAX, calls, fail_call;
static void *guard_malloc(size_t n)
{
    if (++calls == fail_call) { errno = ENOMEM; return NULL; }
    assert(n <= SIZE_MAX - sizeof(union block) - 16);
    union block *b = malloc(sizeof(*b) + n + 16);
    assert(b); b->h.size = n;
    memset((unsigned char *)(b + 1) + n, 0xAC, 16);
    live += n; if (live > peak) peak = live;
    assert(live <= ceiling);
    return b + 1;
}
static void guard_free(void *p)
{
    if (!p) return;
    union block *b = (union block *)p - 1;
    for (unsigned i = 0; i < 16; ++i)
        assert(((unsigned char *)p)[b->h.size + i] == 0xAC);
    live -= b->h.size; free(b);
}
static void *guard_calloc(size_t n, size_t z)
{
    assert(!z || n <= SIZE_MAX / z);
    void *p = guard_malloc(n * z); if (p) memset(p, 0, n * z); return p;
}
static int guard_memalign(void **p, size_t alignment, size_t size)
{
    // No syscall uses alignment in this allocator-only proof. Real candidate
    // tests separately exercise genuine posix_memalign mappings.
    (void)alignment; *p = guard_malloc(size); return *p ? 0 : ENOMEM;
}
#define malloc guard_malloc
#define calloc guard_calloc
#define realloc EXACT_VARIANT_MUST_NOT_REALLOC
#define free guard_free
#define posix_memalign guard_memalign
#include "dirty_tracker.c"
#undef malloc
#undef calloc
#undef realloc
#undef free
#undef posix_memalign

static void test_capture_tail(void)
{
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    unsigned char *a = mmap(NULL, page * 2, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANON, -1, 0);
    unsigned char *b = mmap(NULL, page * 2, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(a != MAP_FAILED && b != MAP_FAILED);
    assert(!mprotect(a + page, page, PROT_NONE));
    assert(!mprotect(b + page, page, PROT_NONE));
    memset(a, 57, page);
    for (size_t n = 0; n < 128; ++n) {
        memset(b, 91, page);
        machine_capture(b + page - n, a + page - n, n);
        assert(!memcmp(b + page - n, a + page - n, n));
        assert(b[page - n - 1] == 91);
    }
    assert(!munmap(a, page * 2)); assert(!munmap(b, page * 2));
}

int main(void)
{
    test_capture_tail();
    struct dirty_tracker *t; assert(!dirty_tracker_create(32 * 1024 * 1024, &t));
    size_t baseline_live = live, page = t->page_size;
    // Include partial boundary pages and sparse/clean/dense payloads.
    for (size_t pages = 1; pages <= 65; ++pages) {
        size_t off = 13, len = pages * page - 26;
        for (unsigned pattern = 0; pattern < 4; ++pattern) {
            memset(t->app, 0, t->size); memset(t->baseline, 0, t->size);
            if (pattern == 1) t->app[off] = 7;
            if (pattern >= 2) memset(t->app + off, 7, len);
            size_t bound = dirty_tracker_snapshot_reservation(t, off, len);
            assert(bound); ceiling = baseline_live + bound; peak = live;
            struct dirty_snapshot s;
            assert(!dirty_tracker_snapshot_range(t, off, len, &s));
            assert(peak <= ceiling);
            if (pattern == 0) assert(peak == baseline_live + page);
            else assert(peak == ceiling);
            assert(!dirty_tracker_abort(t, &s)); dirty_snapshot_free(&s);
            assert(live == baseline_live);
        }
    }
    // Every allocation failure leaves no pending ownership or leaked payload.
    memset(t->app, 7, t->size);
    ceiling = baseline_live + dirty_tracker_snapshot_reservation(t, 0, 32 * page);
    for (unsigned failure = 1; failure <= 12; ++failure) {
        fail_call = calls + failure;
        struct dirty_snapshot s;
        int result = dirty_tracker_snapshot_range(t, 0, 32 * page, &s);
        if (!result) { assert(!dirty_tracker_abort(t, &s)); dirty_snapshot_free(&s); }
        else assert(errno == ENOMEM);
        assert(!t->pending && live == baseline_live);
        fail_call = 0;
    }
    // Large clean/one-byte ranges: exact maximum requested allocation, but
    // sparse publication still captures/touches only one payload page.
    memset(t->app, 0, t->size); memset(t->baseline, 0, t->size);
    for (unsigned changed = 0; changed < 2; ++changed) {
        t->app[0] = changed;
        size_t reservation = dirty_tracker_snapshot_reservation(t, 0, t->size);
        ceiling = baseline_live + reservation; peak = live;
        struct dirty_snapshot s;
        assert(!dirty_tracker_snapshot(t, &s));
        assert(s.page_count == changed);
        assert(peak == baseline_live + (changed ? reservation : page));
        assert(!dirty_tracker_abort(t, &s)); dirty_snapshot_free(&s);
        assert(live == baseline_live);
    }
    ceiling = SIZE_MAX;
    assert(!dirty_tracker_snapshot_reservation(t, SIZE_MAX, 1) && errno == EINVAL);
    // Discard performs no allocation and rejects a pending snapshot without
    // changing baseline. It must not publish unflushed bytes to a GPU replica.
    t->app[23] = 99;
    struct dirty_snapshot pending;
    assert(!dirty_tracker_snapshot_range(t, 23, 1, &pending));
    unsigned char old_baseline = t->baseline[23];
    assert(dirty_tracker_discard_writes(t) == -1 && errno == EBUSY);
    assert(t->baseline[23] == old_baseline && t->app[23] == 99);
    assert(!dirty_tracker_abort(t, &pending)); dirty_snapshot_free(&pending);
    size_t before_calls = calls;
    ceiling = live;
    assert(!dirty_tracker_discard_writes(t));
    assert(calls == before_calls && live == baseline_live);
    assert(!memcmp(t->app, t->baseline, t->size));
    // GPU zero equals the original baseline but must still replace discarded
    // CPU99 on remap. Equal GPU/current-baseline is harmless after discard.
    unsigned char gpu_zero = 0, gpu_equal = t->app[24];
    assert(!dirty_tracker_readback(t, 23, &gpu_zero, 1));
    assert(t->app[23] == 0 && t->baseline[23] == 0);
    assert(!dirty_tracker_readback(t, 24, &gpu_equal, 1));
    assert(t->app[24] == gpu_equal && t->baseline[24] == gpu_equal);
    dirty_tracker_destroy(t); assert(live == 0);
    puts("exact allocation bound: PASS 260 shapes + failures + guards");
}
