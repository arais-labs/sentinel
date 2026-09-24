#include "mc_merge.h"
#include "dirty_tracker.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct fixture {
    struct mc_merge *merge;
    struct dirty_tracker *tracker;
    struct mc_merge_key key;
    unsigned char *map;
    size_t size;
    uint64_t sequence;
    unsigned releases;
};
struct capture {
    struct fixture *fixture;
    uint64_t ticket;
    unsigned char bytes[];
};
struct accept {
    struct dirty_tracker *tracker;
    struct dirty_snapshot *snapshot;
};
static unsigned checks;

static void check(int value)
{
    ++checks;
    if (!value) {
        fprintf(stderr, "check %u failed errno=%d\n", checks, errno);
        abort();
    }
}

static void init(struct fixture *f)
{
    memset(f, 0, sizeof(*f));
    f->size = sysconf(_SC_PAGESIZE);
    f->key = (struct mc_merge_key){1, 1, 1};
    check(!dirty_tracker_create(f->size, &f->tracker));
    f->map = dirty_tracker_mapping(f->tracker);
    check(!mc_merge_create(f->key, f->size, 8, 32 * f->size, &f->merge));
}

static void finish(struct fixture *f)
{
    check(!dirty_tracker_error(f->tracker));
    mc_merge_destroy(f->merge);
    dirty_tracker_destroy(f->tracker);
}

static void release(void *arg)
{
    struct capture *c = arg;
    ++c->fixture->releases;
    free(c);
}

static struct capture *capture(struct fixture *f, unsigned char value)
{
    struct capture *c = malloc(sizeof(*c) + f->size);
    check(c != NULL);
    c->fixture = f;
    memset(c->bytes, value, f->size);
    check(!mc_merge_capture(f->merge, f->key, ++f->sequence, 0, f->size,
                           c, release, &c->ticket));
    return c;
}

static int publish_callback(void *user, size_t offset, const void *staging,
                            size_t length, const unsigned char *superseded)
{
    struct fixture *f = user;
    return dirty_tracker_merge(f->tracker, offset, staging, length, superseded) ? errno : 0;
}

static int publish(struct capture *c)
{
    return mc_merge_publish(c->fixture->merge, c->ticket, c->bytes,
        c->fixture->size, publish_callback, c->fixture);
}

static int accept_callback(void *arg)
{
    struct accept *a = arg;
    return dirty_tracker_accept(a->tracker, a->snapshot) ? errno : 0;
}

static void accept_cpu(struct fixture *f, size_t offset, size_t length)
{
    struct dirty_snapshot snapshot;
    check(!dirty_tracker_snapshot_range(f->tracker, offset, length, &snapshot));
    struct mc_merge_range range = {offset, length};
    struct accept a = {f->tracker, &snapshot};
    check(!mc_merge_accept_cpu(f->merge, f->key, ++f->sequence, &range, 1,
                              accept_callback, &a));
    dirty_snapshot_free(&snapshot);
}

static void test_revert(int reverse)
{
    struct fixture f;
    init(&f);
    struct capture *a = capture(&f, 1);
    struct capture *b = capture(&f, 0);
    if (reverse) {
        check(!publish(b));
        check(!publish(a));
    } else {
        check(!publish(a));
        check(f.map[0] == 1);
        check(!publish(b));
    }
    for (size_t i = 0; i < f.size; ++i)
        check(f.map[i] == 0);
    check(f.releases == 2);
    finish(&f);
}

static void test_disjoint_repeat(void)
{
    struct fixture f;
    init(&f);
    struct capture *a = capture(&f, 0);
    a->bytes[3] = 1;
    struct capture *b = capture(&f, 0);
    b->bytes[3] = 1;
    b->bytes[41] = 2;
    check(!publish(a));
    f.map[3] = 5;
    check(!publish(b));
    check(f.map[3] == 5 && f.map[41] == 2);
    struct dirty_snapshot snapshot;
    check(!dirty_tracker_snapshot(f.tracker, &snapshot));
    check(snapshot.changed_bytes == 1);
    check(!dirty_tracker_abort(f.tracker, &snapshot));
    dirty_snapshot_free(&snapshot);
    a = capture(&f, 0);
    a->bytes[3] = 7;
    a->bytes[41] = 2;
    accept_cpu(&f, 3, 1);
    check(!publish(a));
    check(f.map[3] == 5 && f.map[41] == 2);
    finish(&f);
}

static void test_abort_retry(void)
{
    struct fixture f;
    init(&f);
    struct capture *a = capture(&f, 0);
    a->bytes[1] = 9;
    a->bytes[f.size - 1] = 1;
    f.map[f.size - 1] = 8;
    struct dirty_snapshot snapshot;
    check(!dirty_tracker_snapshot_range(f.tracker, f.size - 1, 1, &snapshot));
    check(publish(a) == EBUSY);
    check(f.map[1] == 0 && f.map[f.size - 1] == 8 && !f.releases);
    check(publish(a) == EBUSY);
    check(!dirty_tracker_abort(f.tracker, &snapshot));
    dirty_snapshot_free(&snapshot);
    accept_cpu(&f, f.size - 1, 1);
    check(!publish(a));
    check(f.map[1] == 9 && f.map[f.size - 1] == 8 && f.releases == 1);
    a = capture(&f, 4);
    uint64_t ticket = a->ticket;
    ++f.key.map_generation;
    check(!mc_merge_set_active(f.merge, f.key, f.size));
    check(publish(a) == ESTALE && f.map[1] == 9);
    check(!mc_merge_discard(f.merge, ticket));
    finish(&f);
}

int main(void)
{
    for (unsigned i = 0; i < 16; ++i) {
        test_revert(0);
        test_revert(1);
        test_disjoint_repeat();
        test_abort_retry();
    }
    printf("PASS %u tracker checks: ordered and reverse GPU reverts, repeated observations with disjoint CPU writes, accepted supersession, pending retry and stale maps\n", checks);
    return 0;
}
