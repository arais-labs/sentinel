#include "mapped_mask.h"
#include <assert.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

static size_t reference(const unsigned char *mask, size_t from, size_t limit, bool set)
{
    for (; from < limit; from++)
        if (!!(mask[from / 8] & (1u << (from % 8))) == set)
            return from;
    return limit;
}

static void check(const unsigned char *mask, size_t limit)
{
    size_t runs = 0;
    bool previous = false;
    for (size_t bit = 0; bit < limit; bit++) {
        bool set = mask[bit / 8] & (1u << (bit % 8));
        runs += set && !previous;
        previous = set;
    }
    assert(mapped_mask_runs(mask, limit) == runs);
    for (size_t from = 0; from <= limit + 1; from++)
        for (unsigned set = 0; set < 2; set++)
            assert(mapped_mask_next(mask, from, limit, set) ==
                   reference(mask, from, limit, set));
}

int main(void)
{
    for (unsigned pattern = 0; pattern < 256; pattern++) {
        unsigned char byte = pattern;
        for (size_t limit = 0; limit <= 8; limit++)
            check(&byte, limit);
    }
    long page = sysconf(_SC_PAGESIZE);
    assert(page > 0);
    unsigned char *memory = mmap(NULL, (size_t)page * 2, PROT_READ | PROT_WRITE,
                                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(memory != MAP_FAILED);
    assert(!mprotect(memory + page, page, PROT_NONE));
    uint32_t random = 17;
    for (size_t limit = 0; limit <= 513; limit++) {
        size_t bytes = (limit + 7) / 8;
        unsigned char *mask = memory + page - bytes;
        for (unsigned pattern = 0; pattern < 3; pattern++) {
            for (size_t i = 0; i < bytes; i++) {
                random = random * 1664525u + 1013904223u;
                mask[i] = pattern == 0 ? 0 : pattern == 1 ? 255 : random >> 24;
            }
            check(mask, limit);
        }
    }
    assert(!munmap(memory, (size_t)page * 2));
    puts("mapped mask: exact runs, partial tails and guard-page bounds PASS");
    return 0;
}
