#define _GNU_SOURCE
#include "dirty_tracker.h"
#include <dirent.h>
#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

#define REQUIRE(condition) do { if (!(condition)) { \
    fprintf(stderr, "FAIL line %d: %s (%s)\n", __LINE__, #condition, strerror(errno)); \
    exit(1); } } while (0)

static uint64_t nanoseconds(void)
{
    struct timespec time;
    REQUIRE(clock_gettime(CLOCK_MONOTONIC, &time) == 0);
    return (uint64_t)time.tv_sec * 1000000000 + time.tv_nsec;
}

static int directory_count(const char *path)
{
    DIR *directory = opendir(path);
    REQUIRE(directory);
    int count = 0;
    struct dirent *entry;
    while ((entry = readdir(directory)))
        if (entry->d_name[0] != '.') count++;
    closedir(directory);
    return count;
}

struct watchdog { int deadline_fd, done_fd; pthread_t thread; };

static void *watch_deadline(void *argument)
{
    struct watchdog *watchdog = argument;
    struct pollfd fds[] = {{watchdog->deadline_fd, POLLIN, 0}, {watchdog->done_fd, POLLIN, 0}};
    int result;
    do result = poll(fds, 2, -1); while (result < 0 && errno == EINTR);
    if (result > 0 && (fds[1].revents & POLLIN)) return NULL;
    static const char error[] = "FAIL: bounded dirty-tracker test deadline\n";
    ssize_t written = write(STDERR_FILENO, error, sizeof(error) - 1);
    (void)written;
    _Exit(124);
}

static void watchdog_start(struct watchdog *watchdog)
{
    watchdog->deadline_fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    watchdog->done_fd = eventfd(0, EFD_CLOEXEC);
    REQUIRE(watchdog->deadline_fd >= 0 && watchdog->done_fd >= 0);
    struct itimerspec deadline = {.it_value = {.tv_sec = 60}};
    REQUIRE(timerfd_settime(watchdog->deadline_fd, 0, &deadline, NULL) == 0);
    REQUIRE(pthread_create(&watchdog->thread, NULL, watch_deadline, watchdog) == 0);
}

static void watchdog_stop(struct watchdog *watchdog)
{
    uint64_t one = 1;
    REQUIRE(write(watchdog->done_fd, &one, sizeof(one)) == sizeof(one));
    REQUIRE(pthread_join(watchdog->thread, NULL) == 0);
    close(watchdog->deadline_fd); close(watchdog->done_fd);
}

static void apply(const struct dirty_snapshot *snapshot, unsigned char *replica)
{
    for (size_t i = 0; i < snapshot->page_count; i++) {
        const struct dirty_page *page = &snapshot->pages[i];
        for (size_t j = 0; j < snapshot->page_size; j++)
            if (page->changed[j / 8] & (1u << (j % 8)))
                replica[page->offset + j] = page->bytes[j];
    }
}

static void accept_snapshot(struct dirty_snapshot *snapshot)
{
    REQUIRE(dirty_tracker_accept(snapshot->pending_owner, snapshot) == 0);
    dirty_snapshot_free(snapshot);
}

struct writer {
    volatile uint32_t *pointer;
    pthread_barrier_t *barrier;
    uint32_t tag;
};

static void wait_barrier(pthread_barrier_t *barrier)
{
    int result = pthread_barrier_wait(barrier);
    REQUIRE(result == 0 || result == PTHREAD_BARRIER_SERIAL_THREAD);
}

static void *write_concurrently(void *argument)
{
    struct writer *writer = argument;
    for (unsigned epoch = 1; epoch <= 64; epoch++) {
        wait_barrier(writer->barrier);
        for (unsigned n = 1; n <= 128; n++)
            *writer->pointer = writer->tag | (epoch << 8) | n;
        wait_barrier(writer->barrier);
    }
    return NULL;
}

static void test_readbackv(struct dirty_tracker *tracker, unsigned char *mapping,
                           size_t page_size, size_t size)
{
    const size_t base = 61 * page_size, later = base + 2 * page_size;
    const uint32_t values[] = {0x11223344, 0x55667788, 0xaabbccdd};
    struct dirty_readback spans[] = {
        {base + 8, &values[0], 4},
        {base + 12, &values[1], 4},
        {later + 8, &values[2], 4},
    };
    struct dirty_snapshot snapshot;
    REQUIRE(dirty_tracker_readbackv(tracker, NULL, 0) == 0);
    REQUIRE(dirty_tracker_readbackv(tracker, NULL, 1) == -1 && errno == EINVAL);
    REQUIRE(dirty_tracker_readbackv(tracker, spans, SIZE_MAX) == -1 && errno == EINVAL);
    struct dirty_readback empty = {size, NULL, 0};
    REQUIRE(dirty_tracker_readbackv(tracker, &empty, 1) == 0);

    /* Every invalid late descriptor must reject the entire transaction. */
    const struct dirty_readback invalid[] = {
        {base + 10, &values[1], 4}, /* overlap */
        {base + 4, &values[1], 4},  /* unsorted */
        {size - 1, &values[1], 4},
        {SIZE_MAX, &values[1], 4},
        {later, &values[1], SIZE_MAX},
        {later, NULL, 4},
        {later, mapping + base, 4}, /* source is tracked application alias */
        {later, (const void *)(UINTPTR_MAX - 1), 4},
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
        struct dirty_readback bad[] = {spans[0], invalid[i]};
        REQUIRE(dirty_tracker_readbackv(tracker, bad, 2) == -1 && errno == EINVAL);
        REQUIRE(*(uint32_t *)(mapping + base + 8) == 0);
        REQUIRE(*(uint32_t *)(mapping + later + 8) == 0);
    }

    /* Observed CPU differences survive byte merge; disjoint GPU spans publish.
     * This is not a promise about concurrent same-byte CPU/GPU races. */
    *(volatile uint32_t *)(mapping + later + 8) = 0x19283746;
    REQUIRE(dirty_tracker_readbackv(tracker, spans, 3) == 0);
    REQUIRE(*(uint32_t *)(mapping + base + 8) == values[0]);
    REQUIRE(*(uint32_t *)(mapping + base + 12) == values[1]);
    REQUIRE(*(uint32_t *)(mapping + later + 8) == 0x19283746);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 4);
    REQUIRE(snapshot.pages[0].offset == later);
    REQUIRE(dirty_tracker_readbackv(tracker, spans, 3) == -1 && errno == EBUSY);
    REQUIRE(*(uint32_t *)(mapping + base + 8) == values[0]);
    accept_snapshot(&snapshot);

    /* Successful batch preserves dirty same-page neighbors, adjacent spans and
     * untouched holes. Its GPU bytes must NOT appear as subsequent CPU changes. */
    *(volatile uint32_t *)(mapping + base + 24) = 0x98765432;
    REQUIRE(dirty_tracker_readbackv(tracker, spans, 3) == 0);
    for (size_t i = 0; i < 3; i++)
        REQUIRE(*(uint32_t *)(mapping + spans[i].offset) == values[i]);
    REQUIRE(*(uint32_t *)(mapping + base + 24) == 0x98765432);
    REQUIRE(mapping[base + 16] == 0 && mapping[base + page_size] == 0);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 4);
    REQUIRE(snapshot.pages[0].offset == base);
    REQUIRE(snapshot.pages[0].changed[24 / 8] == 0x0f);
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);
}

static void test_forced_range(struct dirty_tracker *tracker, unsigned char *mapping,
                              size_t page_size)
{
    const size_t base = 79 * page_size, offset = base + 13;
    struct dirty_snapshot snapshot;
    /* Clean pages still supply authoritative bytes for an explicit flush. */
    REQUIRE(dirty_tracker_snapshot_range_force(tracker, offset, 5, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 5);
    for (size_t byte = 0; byte < page_size; byte++) {
        bool marked = snapshot.pages[0].changed[byte / 8] & (1u << (byte % 8));
        REQUIRE(marked == (byte >= 13 && byte < 18));
    }
    accept_snapshot(&snapshot);

    /* Device bytes may have changed to7 without a READ shadow update. A CPU
     * store of the old0 baseline is nevertheless authoritative when flushed. */
    unsigned char device_byte = 7;
    mapping[offset] = 0;
    mapping[offset - 1] = 0x12;
    mapping[offset + 5] = 0x34;
    REQUIRE(dirty_tracker_snapshot_range_force(tracker, offset, 1, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 1);
    REQUIRE(snapshot.pages[0].changed[13 / 8] == (1u << (13 % 8)));
    device_byte = snapshot.pages[0].bytes[13];
    REQUIRE(device_byte == 0);
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 2); /* unflushed neighbors survive */
    accept_snapshot(&snapshot);

    mapping[offset] = 0x66;
    REQUIRE(dirty_tracker_snapshot_range_force(tracker, offset, 5, &snapshot) == 0);
    mapping[offset] = 0;
    REQUIRE(dirty_tracker_abort(tracker, &snapshot) == 0);
    dirty_snapshot_free(&snapshot);
    REQUIRE(dirty_tracker_snapshot_range_force(tracker, offset, 5, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 5 && snapshot.pages[0].bytes[13] == 0);
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);

    REQUIRE(dirty_tracker_snapshot_range_force(tracker, base + page_size - 2, 5, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 2 && snapshot.changed_bytes == 5);
    REQUIRE(snapshot.pages[0].changed[page_size / 8 - 1] == 0xc0);
    REQUIRE(snapshot.pages[1].changed[0] == 0x07);
    accept_snapshot(&snapshot);
}

static void test_baseline_merge(struct dirty_tracker *tracker, unsigned char *mapping,
                                size_t page_size, size_t size)
{
    const size_t offset = 91 * page_size + 13;
    unsigned char gpu[17] = {0}, skip[3] = {0};
    struct dirty_snapshot snapshot;
    mapping[offset + 1] = 0xa1;
    gpu[5] = 7;
    REQUIRE(dirty_tracker_merge(tracker, offset, gpu, sizeof(gpu), NULL) == 0);
    REQUIRE(mapping[offset + 1] == 0xa1 && mapping[offset + 5] == 7);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 1); /* GPU change is not re-uploaded */
    accept_snapshot(&snapshot);
    gpu[1] = 0xa1; /* accepted CPU upload is now reflected in device data */

    /* A previous GPU merge advanced baseline7. The old CPU value0 is now an
     * actual delta even though it equals the allocation's original contents. */
    mapping[offset + 5] = 0;
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 1);
    accept_snapshot(&snapshot);
    gpu[5] = 0;

    gpu[2] = 2;
    gpu[16] = 0x16;
    mapping[offset + 16] = 0x99;
    REQUIRE(dirty_tracker_merge(tracker, offset, gpu, sizeof(gpu), NULL) == 0);
    REQUIRE(mapping[offset + 2] == 2 && mapping[offset + 16] == 0x99);
    /* A relative bit beyond the first two mask bytes excludes the conflicting
     * superseded byte completely; all other GPU differences may now merge. */
    skip[2] = 1;
    REQUIRE(dirty_tracker_merge(tracker, offset, gpu, sizeof(gpu), skip) == 0);
    REQUIRE(mapping[offset + 2] == 2 && mapping[offset + 16] == 0x99);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 1); /* skipped CPU byte remains pending */
    REQUIRE(dirty_tracker_merge(tracker, offset, gpu, sizeof(gpu), skip) == -1 && errno == EBUSY);
    accept_snapshot(&snapshot);
    gpu[16] = 0x99;

    unsigned char newer = 9;
    REQUIRE(dirty_tracker_readback(tracker, offset + 3, &newer, 1) == 0);
    skip[0] = 1u << 3;
    REQUIRE(gpu[3] == 0);
    REQUIRE(dirty_tracker_merge(tracker, offset, gpu, sizeof(gpu), skip) == 0);
    REQUIRE(mapping[offset + 3] == 9); /* skipped older data cannot regress baseline */
    mapping[offset + 3] = 0;
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 1);
    accept_snapshot(&snapshot);

    REQUIRE(dirty_tracker_merge(tracker, size, NULL, 0, NULL) == 0);
    REQUIRE(dirty_tracker_merge(tracker, size, gpu, 1, NULL) == -1 && errno == EINVAL);
    REQUIRE(dirty_tracker_merge(tracker, offset, NULL, 1, NULL) == -1 && errno == EINVAL);
    REQUIRE(dirty_tracker_merge(tracker, offset, mapping, 1, NULL) == -1 && errno == EINVAL);
    REQUIRE(dirty_tracker_merge(tracker, offset, gpu, 1, mapping) == -1 && errno == EINVAL);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);
}

int main(void)
{
    const size_t size = 8u * 1024 * 1024;
    int initial_fds = directory_count("/proc/self/fd");
    int initial_threads = directory_count("/proc/self/task");
    struct watchdog watchdog;
    watchdog_start(&watchdog);
    struct dirty_tracker *tracker;
    uint64_t creation_start = nanoseconds();
    if (dirty_tracker_create(size, &tracker)) {
        fprintf(stderr, "Mapped-buffer tracker unavailable: %s\n", strerror(errno));
        return 77;
    }
    uint64_t creation_ns = nanoseconds() - creation_start;
    unsigned char *mapping = dirty_tracker_mapping(tracker);
    size_t page_size = dirty_tracker_page_size(tracker);
    unsigned char *replica = calloc(1, size);
    REQUIRE(replica);
    struct dirty_snapshot snapshot;

    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);

    size_t offsets[8];
    uint64_t sparse_write_start = nanoseconds();
    for (size_t i = 0; i < 8; i++) {
        offsets[i] = i * (size / 8) + 24;
        *(volatile uint32_t *)(mapping + offsets[i]) = 0x11223344;
    }
    uint64_t sparse_start = nanoseconds();
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    uint64_t sparse_ns = nanoseconds() - sparse_start;
    uint64_t sparse_write_and_snapshot_ns = nanoseconds() - sparse_write_start;
    REQUIRE(snapshot.page_count == 8 && snapshot.changed_bytes == 32);
    apply(&snapshot, replica);
    /* Immutable snapshots must remain valid across later CPU modifications. */
    for (size_t i = 0; i < 8; i++)
        *(volatile uint32_t *)(mapping + offsets[i]) = 0x55667788;
    for (size_t i = 0; i < 8; i++)
        REQUIRE(*(uint32_t *)(snapshot.pages[i].bytes + 24) == 0x11223344);
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 8 && snapshot.changed_bytes == 32);
    apply(&snapshot, replica); accept_snapshot(&snapshot);
    for (size_t i = 0; i < 8; i++) REQUIRE(*(uint32_t *)(replica + offsets[i]) == 0x55667788);

    /* Rewriting the baseline value needs neither a payload nor an upload. */
    *(volatile uint32_t *)(mapping + offsets[0]) = 0x55667788;
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);
    struct dirty_stats before_empty, after_empty;
    dirty_tracker_stats(tracker, &before_empty);
    uint64_t empty_start = nanoseconds();
    for (unsigned n = 0; n < 64; n++) {
        REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
        REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
        accept_snapshot(&snapshot);
    }
    uint64_t empty_ns = nanoseconds() - empty_start;
    dirty_tracker_stats(tracker, &after_empty);
    REQUIRE(after_empty.compared_bytes - before_empty.compared_bytes == 64 * size);

    uint32_t gpu_word = 0xa1b2c3d4;
    REQUIRE(dirty_tracker_readback(tracker, offsets[0] + 16, &gpu_word, 4) == 0);
    REQUIRE(*(uint32_t *)(mapping + offsets[0] + 16) == gpu_word);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);
    *(volatile uint32_t *)(mapping + offsets[0]) = 0x99aabbcc;
    gpu_word = 0xdeadbeef;
    REQUIRE(dirty_tracker_readback(tracker, offsets[0] + 16, &gpu_word, 4) == 0);
    errno = 0;
    REQUIRE(dirty_tracker_readback(tracker, offsets[0], &gpu_word, 4) == 0);
    REQUIRE(*(uint32_t *)(mapping + offsets[0]) == 0x99aabbcc);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 4);
    apply(&snapshot, replica); accept_snapshot(&snapshot);

    test_readbackv(tracker, mapping, page_size, size);
    test_forced_range(tracker, mapping, page_size);
    test_baseline_merge(tracker, mapping, page_size, size);

    /* Rejected queue admission must not consume dirty bytes, even on revert. */
    size_t transaction_offset = 29 * page_size + 24;
    volatile uint32_t *transaction = (volatile uint32_t *)(mapping + transaction_offset);
    *transaction = 0x10203040;
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 4);
    struct dirty_snapshot other;
    REQUIRE(dirty_tracker_snapshot(tracker, &other) == -1 && errno == EBUSY);
    *transaction = 0;
    REQUIRE(dirty_tracker_abort(tracker, &snapshot) == 0);
    dirty_snapshot_free(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);
    *transaction = 0x10203040;
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(dirty_tracker_abort(tracker, &snapshot) == 0);
    dirty_snapshot_free(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 4);
    *transaction = 0;
    /* Acceptance updates baseline to captured bytes, not later CPU contents. */
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 4);
    accept_snapshot(&snapshot);

    size_t explicit_page = 31 * page_size;
    mapping[explicit_page + 13] = 0x31;
    mapping[explicit_page + 15] = 0x73;
    REQUIRE(dirty_tracker_snapshot_range(tracker, explicit_page + 13, 1, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 1 && snapshot.page_count == 1);
    REQUIRE(snapshot.pages[0].changed[13 / 8] == (1u << (13 % 8)));
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot_range(tracker, explicit_page + 15, 1, &snapshot) == 0);
    REQUIRE(snapshot.changed_bytes == 1 && snapshot.page_count == 1);
    REQUIRE(snapshot.pages[0].changed[15 / 8] == (1u << (15 % 8)));
    accept_snapshot(&snapshot);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    /* Both changed bytes were accepted; a clean scan emits no payload page. */
    REQUIRE(snapshot.page_count == 0 && snapshot.changed_bytes == 0);
    accept_snapshot(&snapshot);

    /* Two application threads modify disjoint words on the same tracked page. */
    size_t concurrent_base = 13 * page_size;
    pthread_barrier_t barrier;
    REQUIRE(pthread_barrier_init(&barrier, NULL, 3) == 0);
    struct writer writers[2] = {
        {(volatile uint32_t *)(mapping + concurrent_base), &barrier, 0x11000000},
        {(volatile uint32_t *)(mapping + concurrent_base + 16), &barrier, 0x22000000},
    };
    pthread_t threads[2];
    REQUIRE(pthread_create(&threads[0], NULL, write_concurrently, &writers[0]) == 0);
    REQUIRE(pthread_create(&threads[1], NULL, write_concurrently, &writers[1]) == 0);
    for (unsigned epoch = 1; epoch <= 64; epoch++) {
        wait_barrier(&barrier);
        REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
        apply(&snapshot, replica); accept_snapshot(&snapshot);
        wait_barrier(&barrier);
    }
    REQUIRE(pthread_join(threads[0], NULL) == 0);
    REQUIRE(pthread_join(threads[1], NULL) == 0);
    REQUIRE(pthread_barrier_destroy(&barrier) == 0);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    apply(&snapshot, replica); accept_snapshot(&snapshot);
    REQUIRE(*(uint32_t *)(replica + concurrent_base) == (0x11000000 | (64 << 8) | 128));
    REQUIRE(*(uint32_t *)(replica + concurrent_base + 16) == (0x22000000 | (64 << 8) | 128));

    int pipe_fds[2];
    REQUIRE(pipe(pipe_fds) == 0);
    uint32_t kernel_word = 0x1234abcd;
    REQUIRE(write(pipe_fds[1], &kernel_word, 4) == 4);
    size_t kernel_offset = 19 * page_size + 24;
    REQUIRE(read(pipe_fds[0], mapping + kernel_offset, 4) == 4);
    close(pipe_fds[0]); close(pipe_fds[1]);
    REQUIRE(dirty_tracker_snapshot(tracker, &snapshot) == 0);
    REQUIRE(snapshot.page_count == 1 && snapshot.changed_bytes == 4);
    apply(&snapshot, replica); accept_snapshot(&snapshot);
    REQUIRE(*(uint32_t *)(replica + kernel_offset) == kernel_word);
    REQUIRE(dirty_tracker_error(tracker) == 0);
    struct dirty_stats stats;
    dirty_tracker_stats(tracker, &stats);
    dirty_tracker_destroy(tracker);
    free(replica);
    watchdog_stop(&watchdog);
    REQUIRE(directory_count("/proc/self/fd") == initial_fds);
    REQUIRE(directory_count("/proc/self/task") == initial_threads);
    printf("{\"pass\":true,\"mapping_bytes\":%zu,\"page_size\":%zu,"
           "\"sparse_changed_bytes\":32,\"sparse_captured_pages\":8,"
           "\"creation_ns\":%llu,\"sparse_snapshot_ns\":%llu,"
           "\"sparse_write_and_snapshot_ns\":%llu,"
           "\"empty_64_snapshots_ns\":%llu,"
           "\"captured_pages\":%llu,\"compared_bytes\":%llu,"
           "\"concurrent_epochs\":64,\"kernel_copy_write\":true,"
           "\"accept_abort_revert\":true,\"partial_flush_neighbors\":true,"
           "\"transactional_readbackv\":true,\"authoritative_flush\":true,"
           "\"baseline_merge\":true,\"teardown_clean\":true}\n",
           size, page_size, (unsigned long long)creation_ns, (unsigned long long)sparse_ns,
           (unsigned long long)sparse_write_and_snapshot_ns,
           (unsigned long long)empty_ns,
           (unsigned long long)stats.captured_pages, (unsigned long long)stats.compared_bytes);
    return 0;
}
