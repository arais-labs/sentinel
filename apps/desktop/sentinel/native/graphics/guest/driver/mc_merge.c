#include "mc_merge.h"
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

struct capture {
    struct mc_merge_key key;
    uint64_t sequence;
    size_t offset, length, charge;
    unsigned char *superseded;
    void *owner;
    mc_merge_release release;
};
struct mc_merge {
    struct mc_merge_key key;
    size_t allocation_size, capacity;
    struct capture *records;
    struct mc_merge_stats stats;
    bool busy;
};

static bool same_key(struct mc_merge_key a, struct mc_merge_key b)
{
    return a.allocation == b.allocation &&
           a.allocation_generation == b.allocation_generation &&
           a.map_generation == b.map_generation;
}

static bool valid_key(struct mc_merge_key key)
{
    return key.allocation && key.allocation_generation && key.map_generation;
}

static int active(struct mc_merge *m, struct mc_merge_key key)
{
    if (!m)
        return EINVAL;
    if (m->busy)
        return EBUSY;
    return same_key(m->key, key) ? 0 : ESTALE;
}

static bool valid_range(size_t size, size_t offset, size_t length)
{
    return offset <= size && length <= size - offset;
}

static struct capture *find(struct mc_merge *m, uint64_t ticket)
{
    if (!ticket)
        return NULL;
    for (size_t i = 0; i < m->capacity; ++i)
        if (m->records[i].sequence == ticket)
            return &m->records[i];
    return NULL;
}

static void retire(struct mc_merge *m, struct capture *record)
{
    void *owner = record->owner;
    mc_merge_release release = record->release;
    free(record->superseded);
    m->stats.charged_bytes -= record->charge;
    --m->stats.captures;
    memset(record, 0, sizeof(*record));
    if (release)
        release(owner);
}

static void mark(struct capture *record, size_t offset, size_t length)
{
    size_t begin = offset > record->offset ? offset : record->offset;
    size_t end = offset + length;
    size_t record_end = record->offset + record->length;
    if (end > record_end)
        end = record_end;
    if (begin >= end)
        return;
    begin -= record->offset;
    end -= record->offset;
    while (begin < end && (begin & 7)) {
        record->superseded[begin / 8] |= 1u << (begin & 7);
        ++begin;
    }
    size_t full = (end - begin) / 8;
    memset(record->superseded + begin / 8, 0xff, full);
    begin += full * 8;
    while (begin < end) {
        record->superseded[begin / 8] |= 1u << (begin & 7);
        ++begin;
    }
}

int mc_merge_create(struct mc_merge_key key, size_t allocation_size,
                    size_t max_captures, size_t byte_limit, struct mc_merge **out)
{
    if (!out || !valid_key(key) || !allocation_size || !max_captures ||
        max_captures > (SIZE_MAX - sizeof(struct mc_merge)) / sizeof(struct capture))
        return EINVAL;
    size_t base_charge = sizeof(struct mc_merge) + max_captures * sizeof(struct capture);
    if (base_charge > byte_limit)
        return ENOSPC;
    struct mc_merge *m = calloc(1, sizeof(*m));
    if (!m)
        return ENOMEM;
    m->records = calloc(max_captures, sizeof(*m->records));
    if (!m->records) {
        free(m);
        return ENOMEM;
    }
    m->key = key;
    m->allocation_size = allocation_size;
    m->capacity = max_captures;
    m->stats.charged_bytes = base_charge;
    m->stats.byte_limit = byte_limit;
    *out = m;
    return 0;
}

void mc_merge_destroy(struct mc_merge *m)
{
    if (!m)
        return;
    if (m->busy)
        abort();
    m->busy = true;
    for (size_t i = 0; i < m->capacity; ++i)
        if (m->records[i].sequence)
            retire(m, &m->records[i]);
    free(m->records);
    free(m);
}

int mc_merge_set_active(struct mc_merge *m, struct mc_merge_key key,
                       size_t allocation_size)
{
    if (!m || !valid_key(key) || !allocation_size)
        return EINVAL;
    if (m->busy)
        return EBUSY;
    if (same_key(m->key, key))
        return m->allocation_size == allocation_size ? 0 : EINVAL;
    if (key.allocation != m->key.allocation ||
        key.allocation_generation < m->key.allocation_generation ||
        (key.allocation_generation == m->key.allocation_generation &&
         key.map_generation <= m->key.map_generation))
        return ESTALE;
    m->key = key;
    m->allocation_size = allocation_size;
    return 0;
}

int mc_merge_capture(struct mc_merge *m, struct mc_merge_key key,
                    uint64_t sequence, size_t offset, size_t length,
                    void *owner, mc_merge_release release, uint64_t *ticket)
{
    int result = active(m, key);
    if (result)
        return result;
    if (!ticket || !sequence || sequence <= m->stats.last_sequence ||
        !length || !valid_range(m->allocation_size, offset, length))
        return EINVAL;
    size_t mask_bytes = length / 8 + (length % 8 != 0);
    if (length > SIZE_MAX - mask_bytes)
        return EOVERFLOW;
    size_t charge = length + mask_bytes;
    if (m->stats.captures == m->capacity ||
        charge > m->stats.byte_limit - m->stats.charged_bytes)
        return ENOSPC;
    unsigned char *mask = calloc(mask_bytes, 1);
    if (!mask)
        return ENOMEM;
    struct capture *record = NULL;
    for (size_t i = 0; i < m->capacity; ++i)
        if (!m->records[i].sequence) {
            record = &m->records[i];
            break;
        }
    *record = (struct capture) {
        .key = key, .sequence = sequence, .offset = offset,
        .length = length, .charge = charge, .superseded = mask,
        .owner = owner, .release = release,
    };
    m->stats.charged_bytes += charge;
    ++m->stats.captures;
    m->stats.last_sequence = sequence;
    *ticket = sequence;
    return 0;
}

int mc_merge_accept_cpu(struct mc_merge *m, struct mc_merge_key key,
                       uint64_t sequence, const struct mc_merge_range *ranges,
                       size_t count, mc_merge_cpu_commit commit, void *user)
{
    int result = active(m, key);
    if (result)
        return result;
    if (!sequence || sequence <= m->stats.last_sequence || !commit ||
        (count && !ranges) || count > m->allocation_size)
        return EINVAL;
    size_t end = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!ranges[i].length || ranges[i].offset < end ||
            !valid_range(m->allocation_size, ranges[i].offset, ranges[i].length))
            return EINVAL;
        end = ranges[i].offset + ranges[i].length;
    }
    m->busy = true;
    result = commit(user);
    if (!result) {
        for (size_t i = 0; i < m->capacity; ++i) {
            struct capture *record = &m->records[i];
            if (!record->sequence || !same_key(record->key, key))
                continue;
            for (size_t j = 0; j < count; ++j)
                mark(record, ranges[j].offset, ranges[j].length);
        }
        m->stats.last_sequence = sequence;
    }
    m->busy = false;
    return result;
}

int mc_merge_publish(struct mc_merge *m, uint64_t ticket,
                     const void *staging, size_t length,
                     mc_merge_publish_commit commit, void *user)
{
    if (!m || !staging || !commit)
        return EINVAL;
    if (m->busy)
        return EBUSY;
    struct capture *record = find(m, ticket);
    if (!record || !same_key(record->key, m->key))
        return ESTALE;
    if (length != record->length)
        return EINVAL;
    m->busy = true;
    int result = commit(user, record->offset, staging, length, record->superseded);
    if (!result) {
        for (size_t i = 0; i < m->capacity; ++i) {
            struct capture *older = &m->records[i];
            if (older->sequence && older->sequence < record->sequence &&
                same_key(older->key, record->key))
                mark(older, record->offset, record->length);
        }
        retire(m, record);
    }
    m->busy = false;
    return result;
}

int mc_merge_discard(struct mc_merge *m, uint64_t ticket)
{
    if (!m)
        return EINVAL;
    if (m->busy)
        return EBUSY;
    struct capture *record = find(m, ticket);
    if (!record)
        return ESTALE;
    m->busy = true;
    retire(m, record);
    m->busy = false;
    return 0;
}

void mc_merge_get_stats(const struct mc_merge *m, struct mc_merge_stats *out)
{
    if (m && out)
        *out = m->stats;
}
