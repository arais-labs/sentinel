#include "dirty_tracker.h"
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct shared { unsigned char *p; atomic_bool stop, ready; };
static void *writer(void *arg)
{
    struct shared *s = arg;
    atomic_store_explicit(&s->ready, true, memory_order_release);
    unsigned char round = 0;
    while (!atomic_load_explicit(&s->stop, memory_order_relaxed)) {
        ++round;
        /* Ordinary CPU stores, no atomics required of application payload. */
        for (size_t i = 256; i < 65536; ++i) s->p[i] = round;
    }
    memset(s->p + 256, 173, 65536 - 256);
    return NULL;
}
int main(void)
{
    struct dirty_tracker *t; assert(!dirty_tracker_create(65536, &t));
    unsigned char *p = dirty_tracker_mapping(t);
    memset(p, 29, 256);
    struct shared shared = {.p = p};
    atomic_init(&shared.stop, false); atomic_init(&shared.ready, false);
    pthread_t worker; assert(!pthread_create(&worker, NULL, writer, &shared));
    while (!atomic_load_explicit(&shared.ready, memory_order_acquire)) {}
    size_t page = dirty_tracker_page_size(t), stride = page + page / 8;
    for (unsigned repeat = 0; repeat < 2000; ++repeat) {
        struct dirty_snapshot s;
        assert(!dirty_tracker_snapshot_range_force(t, 0, 65536, &s));
        assert(s.changed_bytes == 65536);
        for (size_t i = 0; i < 256; ++i) assert(s.pages[0].bytes[i] == 29);
        size_t length = s.page_count * stride;
        unsigned char *copy = malloc(length); assert(copy); memcpy(copy, s.storage, length);
        for (unsigned spin = 0; spin < 1000; ++spin) __asm__ volatile("" ::: "memory");
        assert(!memcmp(copy, s.storage, length)); free(copy);
        assert(!dirty_tracker_accept(t, &s)); dirty_snapshot_free(&s);
        /* GPU updates A, which application writer never accesses. */
        unsigned char gpu[1] = {(unsigned char)repeat};
        assert(!dirty_tracker_merge(t, 128, gpu, 1, NULL)); p[128] = 29;
    }
    atomic_store_explicit(&shared.stop, true, memory_order_relaxed);
    assert(!pthread_join(worker, NULL));
    for (size_t i = 256; i < 65536; ++i) assert(p[i] == 173);
    struct dirty_snapshot last;
    assert(!dirty_tracker_snapshot_range_force(t, 0, 65536, &last));
    for (size_t i = 256; i < 65536; ++i)
        assert(last.pages[i / page].bytes[i % page] == 173);
    assert(!dirty_tracker_accept(t, &last)); dirty_snapshot_free(&last);
    dirty_tracker_destroy(t);
    puts("ARM64 broad snapshot vs disjoint CPU writer: PASS, 2000 captures; immutable payload and final writes retained");
}
