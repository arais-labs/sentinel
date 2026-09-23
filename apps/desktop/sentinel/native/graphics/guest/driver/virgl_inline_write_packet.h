#ifndef VIRGL_INLINE_WRITE_PACKET_H
#define VIRGL_INLINE_WRITE_PACKET_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include "virtio-gpu/virgl_protocol.h"

static inline size_t
virgl_inline_packet_max_bytes(size_t capacity_dwords)
{
   if (capacity_dwords <= 12)
      return 0;
   size_t payload = capacity_dwords - 1;
   if (payload > UINT16_MAX)
      payload = UINT16_MAX;
   return (payload - 11) * 4;
}

static inline size_t
virgl_inline_packet_write(uint32_t *packet, size_t capacity_dwords,
                          uint32_t offset, const void *data, size_t length)
{
   if (!length)
      return 0;
   if (!packet || !data || length > virgl_inline_packet_max_bytes(capacity_dwords) ||
       offset > INT_MAX || length > (size_t)INT_MAX - offset)
      return 0;
   size_t data_dwords = (length + 3) / 4;
   size_t packet_dwords = 12 + data_dwords;
   packet[0] = VIRGL_CMD0(VIRGL_CCMD_RESOURCE_INLINE_WRITE, 0, 11 + data_dwords);
   packet[1] = 0;
   packet[2] = 0;
   packet[3] = 0;
   packet[4] = 0;
   packet[5] = 0;
   packet[6] = offset;
   packet[7] = 0;
   packet[8] = 0;
   packet[9] = length;
   packet[10] = 1;
   packet[11] = 1;
   packet[packet_dwords - 1] = 0;
   memcpy(packet + 12, data, length);
   return packet_dwords;
}
#endif
