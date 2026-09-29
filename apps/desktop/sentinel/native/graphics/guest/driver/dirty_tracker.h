#ifndef SENTINEL_DIRTY_TRACKER_H
#define SENTINEL_DIRTY_TRACKER_H
#include <stddef.h>
#include <stdint.h>

struct dirty_tracker;
struct dirty_page {
    size_t offset;
    unsigned char *bytes;
    unsigned char *changed; /* page_size/8 packed bytes: bit(i%8) in byte(i/8) */
};
struct dirty_snapshot {
    size_t page_size, page_count, changed_bytes;
    struct dirty_page *pages;
    unsigned char *storage;
    struct dirty_tracker *pending_owner;
};
struct dirty_stats {
    uint64_t snapshots, captured_pages, compared_bytes, forced_bytes;
};
struct dirty_readback {
    size_t offset;
    const void *bytes;
    size_t length;
};

/* Owns application allocation and private baseline. Caller must stop all users
 * before unmap/discard/destroy. ARM64 coherent Normal memory; no UFFD required. */
int dirty_tracker_create(size_t size, struct dirty_tracker **out);
void *dirty_tracker_mapping(struct dirty_tracker *tracker);
size_t dirty_tracker_page_size(struct dirty_tracker *tracker);
int dirty_tracker_snapshot(struct dirty_tracker *tracker, struct dirty_snapshot *out);
/* Peak requested heap payload including scratch and immutable snapshot storage.
 * Reserve before capture; zero reports invalid bounds/overflow through errno.
 * Does not include tracker storage, allocator metadata or encoded uploads. */
size_t dirty_tracker_snapshot_reservation(struct dirty_tracker *tracker,
                                          size_t offset, size_t length);
/* GL-ordered consumed bytes must be stable during capture. Unrelated bytes
 * scanned speculatively may tear; this is not an atomic whole-buffer snapshot.
 * Returned payload is immutable and later writes remain pending after accept. */
int dirty_tracker_snapshot_range(struct dirty_tracker *tracker, size_t offset,
                                 size_t length, struct dirty_snapshot *out);
/* Authoritative explicit flush: captures every requested byte, including
 * unchanged bytes and clean pages. Same prepare/accept/abort ownership. */
int dirty_tracker_snapshot_range_force(struct dirty_tracker *tracker, size_t offset,
                                       size_t length, struct dirty_snapshot *out);
/* One pending snapshot per tracker. Accept only after immutable queue admission.
 * Abort leaves baseline unchanged; a later scan rediscovers CPU differences. */
int dirty_tracker_accept(struct dirty_tracker *tracker, struct dirty_snapshot *snapshot);
int dirty_tracker_abort(struct dirty_tracker *tracker, struct dirty_snapshot *snapshot);
/* Frees an accepted/aborted snapshot; automatically aborts a pending one. */
void dirty_snapshot_free(struct dirty_snapshot *snapshot);
/* Unmap-only: make baseline equal application bytes without upload/allocation.
 * All application users must have stopped. Pending snapshot rejects with EBUSY.
 * Caller must retire the map generation before old GPU readbacks can publish. */
int dirty_tracker_discard_writes(struct dirty_tracker *tracker);
/* Transactional readback: nonempty spans must be sorted and disjoint. Sources
 * must remain immutable external staging storage until return (no tracker
 * aliases). Empty spans are ignored after bounds validation. EINVAL rejects
 * invalid/overlapping spans; EBUSY rejects pending snapshots. Neither failure
 * publishes bytes or baseline. Publication uses the byte-merge rules below;
 * no writer freeze or whole-range atomicity. Publish completion only afterward. */
int dirty_tracker_readbackv(struct dirty_tracker *tracker,
                            const struct dirty_readback *spans, size_t count);
/* Single-span wrapper with the same transaction contract. */
int dirty_tracker_readback(struct dirty_tracker *tracker, size_t offset,
                           const void *bytes, size_t length);
/* Merge one immutable GPU range against CURRENT baseline.
 * Optional skip_bits is ceil(length/8) bytes, range-relative packed bits:1 means
 * preserve that byte entirely. Only GPU!=baseline bytes participate; observed
 * CPU differences are preserved while baseline advances to GPU bytes. Byte
 * loads/stores preserve neighboring CPU writes, but this is NOT atomic CAS or
 * protection for unsynchronized same-byte CPU/GPU races. No writer freezing.
 * Sources/mask must be immutable external storage, and pending snapshots reject.
 * Returns0 or-1 with errno; caller publishes its fence only after success. */
int dirty_tracker_merge(struct dirty_tracker *tracker, size_t offset,
                         const void *bytes, size_t length,
                         const unsigned char *skip_bits);
int dirty_tracker_stats(struct dirty_tracker *tracker, struct dirty_stats *out);
int dirty_tracker_error(struct dirty_tracker *tracker);
void dirty_tracker_destroy(struct dirty_tracker *tracker);

#endif
