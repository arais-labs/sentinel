#ifndef SENTINEL_MAPPED_MASK_H
#define SENTINEL_MAPPED_MASK_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Count 0->1 transitions, including across word boundaries. This stays linear
 * in mask words even for alternating bits (one tiny run every other byte). */
static inline size_t mapped_mask_runs(const unsigned char *mask, size_t limit)
{
    size_t count = 0, offset = 0;
    uint64_t previous = 0;
    while (limit - offset >= 64) {
        uint64_t word;
        memcpy(&word, mask + offset / 8, sizeof(word));
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
        word = __builtin_bswap64(word);
#endif
        count += (unsigned)__builtin_popcountll(word & ~((word << 1) | previous));
        previous = word >> 63;
        offset += 64;
    }
    while (offset < limit) {
        size_t length = limit - offset < 8 ? limit - offset : 8;
        unsigned byte = mask[offset / 8] & ((1u << length) - 1);
        count += (unsigned)__builtin_popcount(byte & ~((byte << 1) | (unsigned)previous));
        previous = (byte >> (length - 1)) & 1;
        offset += length;
    }
    return count;
}

/* Find a set/clear bit in a private immutable LSB-first byte mask. Skip whole
 * homogeneous words without relying on host byte order or aligned pointers.
 * Never read beyond ceil(limit/8) bytes; limit itself denotes "not found". */
static inline size_t mapped_mask_next(const unsigned char *mask, size_t from,
                                      size_t limit, bool set)
{
    while (from < limit) {
        if (!(from % 64) && limit - from >= 64) {
            uint64_t word;
            memcpy(&word, mask + from / 8, sizeof(word));
            if (word == (set ? 0 : UINT64_MAX)) {
                from += 64;
                continue;
            }
        }
        unsigned byte = set ? mask[from / 8] : (unsigned char)~mask[from / 8];
        byte &= 0xffu << (from % 8);
        if (byte) {
            size_t found = from - from % 8 + (unsigned)__builtin_ctz(byte);
            return found < limit ? found : limit;
        }
        size_t step = 8 - from % 8;
        if (step >= limit - from)
            return limit;
        from += step;
    }
    return limit;
}
#endif
