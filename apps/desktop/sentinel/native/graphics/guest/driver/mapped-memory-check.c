#include "dirty_tracker.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
    struct dirty_tracker *tracker = NULL;
    struct dirty_snapshot snapshot = {0};
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0 || dirty_tracker_create((size_t)page, &tracker)) {
        fprintf(stderr, "Sentinel graphics: mapped-memory tracker initialization failed: %s\n", strerror(errno));
        return 1;
    }
    volatile unsigned char *mapping = dirty_tracker_mapping(tracker);
    mapping[0] = 1;
    int result = dirty_tracker_snapshot(tracker, &snapshot);
    if (!result && snapshot.changed_bytes != 1) {
        errno = EIO;
        result = -1;
    }
    if (!result)
        result = dirty_tracker_accept(tracker, &snapshot);
    if (result)
        fprintf(stderr, "Sentinel graphics: mapped-memory capture failed: %s\n", strerror(errno));
    dirty_snapshot_free(&snapshot);
    dirty_tracker_destroy(tracker);
    return result ? 1 : 0;
}
