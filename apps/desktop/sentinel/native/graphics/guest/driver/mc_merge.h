#ifndef MC_MERGE_H
#define MC_MERGE_H
#include <stddef.h>
#include <stdint.h>

struct mc_merge;
struct mc_merge_key {
    uint64_t allocation, allocation_generation, map_generation;
};
struct mc_merge_range {
    size_t offset, length;
};
struct mc_merge_stats {
    size_t captures, charged_bytes, byte_limit;
    uint64_t last_sequence;
};
typedef int (*mc_merge_cpu_commit)(void *user);
typedef int (*mc_merge_publish_commit)(void *user, size_t offset,
    const void *staging, size_t length, const unsigned char *superseded);
typedef void (*mc_merge_release)(void *owner);

int mc_merge_create(struct mc_merge_key key, size_t allocation_size,
                    size_t max_captures, size_t byte_limit, struct mc_merge **out);
void mc_merge_destroy(struct mc_merge *merge);
int mc_merge_set_active(struct mc_merge *merge, struct mc_merge_key key,
                       size_t allocation_size);
int mc_merge_capture(struct mc_merge *merge, struct mc_merge_key key,
                    uint64_t sequence, size_t offset, size_t length,
                    void *owner, mc_merge_release release, uint64_t *ticket);
int mc_merge_accept_cpu(struct mc_merge *merge, struct mc_merge_key key,
                       uint64_t sequence, const struct mc_merge_range *ranges,
                       size_t count, mc_merge_cpu_commit commit, void *user);
int mc_merge_publish(struct mc_merge *merge, uint64_t ticket,
                     const void *staging, size_t length,
                     mc_merge_publish_commit commit, void *user);
int mc_merge_discard(struct mc_merge *merge, uint64_t ticket);
void mc_merge_get_stats(const struct mc_merge *merge, struct mc_merge_stats *out);
#endif
