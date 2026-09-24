#include "mc_merge.h"
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct fixture {
    struct mc_merge *merge;
    struct mc_merge_key key;
    unsigned char baseline[64], current[64];
    uint64_t sequence;
    unsigned releases, callbacks;
    int fail;
};
struct snapshot {
    struct fixture *fixture;
    uint64_t ticket;
    size_t length;
    unsigned char bytes[];
};
struct accept {
    struct fixture *fixture;
    const struct mc_merge_range *ranges;
    size_t count;
    int fail;
};
static unsigned checks;

static void check(bool value)
{
    ++checks;
    if (!value)
        abort();
}

static void release(void *owner)
{
    struct snapshot *s = owner;
    ++s->fixture->releases;
    free(s);
}

static void init(struct fixture *f)
{
    memset(f, 0, sizeof(*f));
    f->key = (struct mc_merge_key){1, 1, 1};
    check(!mc_merge_create(f->key, 64, 8, 4096, &f->merge));
}

static struct snapshot *capture(struct fixture *f, size_t offset,
                                size_t length, unsigned char value)
{
    struct snapshot *s = calloc(1, sizeof(*s) + length);
    check(s != NULL);
    s->fixture = f;
    s->length = length;
    memset(s->bytes, value, length);
    check(!mc_merge_capture(f->merge, f->key, ++f->sequence, offset, length,
                           s, release, &s->ticket));
    return s;
}

static int publish_callback(void *user, size_t offset, const void *staging,
                            size_t length, const unsigned char *superseded)
{
    struct fixture *f = user;
    const unsigned char *gpu = staging;
    ++f->callbacks;
    if (f->fail)
        return f->fail;
    for (size_t i = 0; i < length; ++i) {
        if (superseded[i / 8] & (1u << (i & 7)))
            continue;
        if (gpu[i] != f->baseline[offset + i] &&
            f->current[offset + i] != f->baseline[offset + i])
            return EBUSY;
    }
    for (size_t i = 0; i < length; ++i) {
        if (superseded[i / 8] & (1u << (i & 7)))
            continue;
        if (gpu[i] != f->baseline[offset + i])
            f->current[offset + i] = f->baseline[offset + i] = gpu[i];
    }
    return 0;
}

static int publish(struct snapshot *s)
{
    return mc_merge_publish(s->fixture->merge, s->ticket, s->bytes,
                            s->length, publish_callback, s->fixture);
}

static int accept_callback(void *user)
{
    struct accept *a = user;
    if (a->fail)
        return a->fail;
    for (size_t i = 0; i < a->count; ++i)
        memcpy(a->fixture->baseline + a->ranges[i].offset,
               a->fixture->current + a->ranges[i].offset, a->ranges[i].length);
    return 0;
}

static int accept_cpu(struct fixture *f, const struct mc_merge_range *ranges,
                       size_t count, int fail)
{
    struct accept a = {f, ranges, count, fail};
    return mc_merge_accept_cpu(f->merge, f->key, ++f->sequence, ranges, count,
                               accept_callback, &a);
}

static void check_byte(struct fixture *f, size_t offset, unsigned char value)
{
    check(f->current[offset] == value && f->baseline[offset] == value);
}

static void test_revert(bool reverse)
{
    struct fixture f;
    init(&f);
    struct snapshot *first = capture(&f, 0, 64, 1);
    struct snapshot *second = capture(&f, 0, 64, 0);
    if (reverse) {
        check(!publish(second));
        check(!publish(first));
    } else {
        check(!publish(first));
        check_byte(&f, 0, 1);
        check(!publish(second));
    }
    for (size_t i = 0; i < 64; ++i)
        check_byte(&f, i, 0);
    check(f.releases == 2);
    mc_merge_destroy(f.merge);
}

static void test_disjoint(void)
{
    struct fixture f;
    init(&f);
    struct snapshot *s = capture(&f, 0, 64, 0);
    s->bytes[35] = 7;
    f.current[2] = 9;
    check(!publish(s));
    check(f.current[2] == 9 && f.baseline[2] == 0);
    check_byte(&f, 35, 7);
    s = capture(&f, 0, 64, 0);
    s->bytes[35] = 7;
    s->bytes[2] = 3;
    struct mc_merge_range range = {2, 1};
    check(!accept_cpu(&f, &range, 1, 0));
    check(!publish(s));
    check_byte(&f, 2, 9);
    mc_merge_destroy(f.merge);
}

static void test_repeated_observation(void)
{
    struct fixture f;
    init(&f);
    struct snapshot *first = capture(&f, 0, 64, 0);
    first->bytes[3] = 1;
    struct snapshot *second = capture(&f, 0, 64, 0);
    second->bytes[3] = 1;
    second->bytes[37] = 2;
    check(!publish(first));
    f.current[3] = 5;
    check(!publish(second));
    check(f.current[3] == 5 && f.baseline[3] == 1);
    check_byte(&f, 37, 2);
    mc_merge_destroy(f.merge);
}

static void test_masks(void)
{
    for (size_t offset = 0; offset < 32; ++offset) {
        for (size_t length = 1; length <= 32; ++length) {
            struct fixture f;
            init(&f);
            struct snapshot *old = capture(&f, 0, 64, 1);
            struct snapshot *fresh = capture(&f, offset, length, 0);
            check(!publish(fresh));
            check(!publish(old));
            for (size_t byte = 0; byte < 64; ++byte)
                check_byte(&f, byte, byte >= offset && byte < offset + length ? 0 : 1);
            mc_merge_destroy(f.merge);
        }
    }
}

static void test_conflict_retry(void)
{
    struct fixture f;
    init(&f);
    struct snapshot *s = capture(&f, 3, 40, 0);
    s->bytes[0] = 9;
    s->bytes[39] = 1;
    f.current[42] = 8;
    check(publish(s) == EBUSY);
    check_byte(&f, 3, 0);
    check(f.current[42] == 8 && f.baseline[42] == 0);
    check(f.releases == 0);
    struct mc_merge_range range = {42, 1};
    check(accept_cpu(&f, &range, 1, EIO) == EIO);
    check(publish(s) == EBUSY);
    check(!accept_cpu(&f, &range, 1, 0));
    check(!publish(s));
    check_byte(&f, 3, 9);
    check_byte(&f, 42, 8);
    mc_merge_destroy(f.merge);

    init(&f);
    struct snapshot *old = capture(&f, 0, 64, 1);
    struct snapshot *fresh = capture(&f, 0, 64, 2);
    f.fail = EIO;
    check(publish(fresh) == EIO);
    f.fail = 0;
    check(!publish(old));
    check_byte(&f, 0, 1);
    check(!publish(fresh));
    check_byte(&f, 0, 2);
    mc_merge_destroy(f.merge);
}

static void test_generation_bounds(void)
{
    struct fixture f;
    init(&f);
    struct snapshot *old = capture(&f, 0, 64, 1);
    uint64_t old_ticket = old->ticket;
    ++f.key.map_generation;
    check(!mc_merge_set_active(f.merge, f.key, 64));
    check(publish(old) == ESTALE && f.callbacks == 0 && f.releases == 0);
    check(!mc_merge_discard(f.merge, old_ticket));
    check(mc_merge_discard(f.merge, old_ticket) == ESTALE);
    uint64_t unused;
    check(mc_merge_capture(f.merge, (struct mc_merge_key){1,1,1}, 4,
                           0, 1, NULL, NULL, &unused) == ESTALE);
    check(mc_merge_capture(f.merge, f.key, 4, SIZE_MAX, 2,
                           NULL, NULL, &unused) == EINVAL);
    check(mc_merge_capture(f.merge, f.key, 1, 0, 1,
                           NULL, NULL, &unused) == EINVAL);
    check(mc_merge_set_active(f.merge, (struct mc_merge_key){1,1,1}, 64) == ESTALE);
    struct mc_merge_range invalid[] = {{7, 2}, {8, 3}};
    check(accept_cpu(&f, invalid, 2, 0) == EINVAL);
    struct snapshot *s = capture(&f, 0, 64, 3);
    check(mc_merge_publish(f.merge, s->ticket, s->bytes, 63,
                           publish_callback, &f) == EINVAL);
    check(!publish(s));
    check_byte(&f, 0, 3);
    mc_merge_destroy(f.merge);
}

static void test_bounded(void)
{
    struct fixture f;
    init(&f);
    struct mc_merge_stats before, after;
    mc_merge_get_stats(f.merge, &before);
    for (size_t i = 0; i < 8; ++i)
        capture(&f, i, 9, 1);
    uint64_t ticket;
    check(mc_merge_capture(f.merge, f.key, f.sequence + 1, 0, 1,
                           NULL, NULL, &ticket) == ENOSPC);
    mc_merge_get_stats(f.merge, &after);
    check(after.last_sequence == f.sequence && after.captures == 8);
    check(after.charged_bytes == before.charged_bytes + 8 * (9 + 2));
    mc_merge_destroy(f.merge);
    check(f.releases == 8);

    check(!mc_merge_create(f.key, SIZE_MAX, 1, 4096, &f.merge));
    check(mc_merge_capture(f.merge, f.key, 1, 0, SIZE_MAX,
                           NULL, NULL, &ticket) == EOVERFLOW);
    check(mc_merge_capture(f.merge, f.key, 1, 0, 4096,
                           NULL, NULL, &ticket) == ENOSPC);
    mc_merge_destroy(f.merge);
}

int main(void)
{
    test_revert(false);
    test_revert(true);
    test_disjoint();
    test_repeated_observation();
    test_masks();
    test_conflict_retry();
    test_generation_bounds();
    test_bounded();
    printf("PASS %u checks: GPU revert, unchanged reverse publication, exact overlapping masks, CPU dirty/accepted disjoint writes, transactional failure/retry, generations and bounded retention\n", checks);
    return 0;
}
