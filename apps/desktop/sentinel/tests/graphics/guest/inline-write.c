#include "virgl_inline_write_packet.h"
#include <stdlib.h>
#include <stdio.h>

static unsigned checks;

static void check(bool value)
{
   ++checks;
   if (!value)
      abort();
}

static void test_length(size_t length)
{
   size_t capacity = 12 + (length + 3) / 4;
   uint32_t *allocation = malloc((capacity + 2) * sizeof(*allocation));
   unsigned char *data = malloc(length ? length : 1);
   check(allocation && data);
   for (size_t i = 0; i < length; ++i)
      data[i] = (unsigned char)(i * 13 + 7);
   memset(allocation, 0xa5, (capacity + 2) * 4);
   uint32_t *packet = allocation + 1;
   size_t written = virgl_inline_packet_write(packet, capacity, 19, data, length);
   check(allocation[0] == 0xa5a5a5a5 && allocation[capacity + 1] == 0xa5a5a5a5);
   if (!length) {
      check(!written && packet[0] == 0xa5a5a5a5);
   } else {
      check(written == capacity);
      check((packet[0] >> 16) == written - 1);
      check((packet[0] & 0xffff) == VIRGL_CCMD_RESOURCE_INLINE_WRITE);
      check(packet[1] == 0 && packet[2] == 0 && packet[3] == 0);
      check(packet[4] == 0 && packet[5] == 0 && packet[6] == 19);
      check(packet[7] == 0 && packet[8] == 0 && packet[9] == length);
      check(packet[10] == 1 && packet[11] == 1);
      check(!memcmp(packet + 12, data, length));
      unsigned char *padded = (unsigned char *)(packet + 12);
      for (size_t i = length; i < (written - 12) * 4; ++i)
         check(padded[i] == 0);
      memset(allocation, 0xa5, (capacity + 2) * 4);
      check(!virgl_inline_packet_write(packet, capacity - 1, 19, data, length));
      for (size_t i = 0; i < capacity + 2; ++i)
         check(allocation[i] == 0xa5a5a5a5);
   }
   free(data);
   free(allocation);
}

int main(void)
{
   for (size_t size = 0; size <= 4097; ++size)
      test_length(size);
   size_t limit = ((size_t)UINT16_MAX - 11) * 4;
   for (size_t size = limit - 7; size <= limit; ++size)
      test_length(size);
   check(virgl_inline_packet_max_bytes(0) == 0);
   check(virgl_inline_packet_max_bytes(12) == 0);
   check(virgl_inline_packet_max_bytes(13) == 4);
   check(virgl_inline_packet_max_bytes(65536) == limit);
   check(virgl_inline_packet_max_bytes(SIZE_MAX) == limit);
   uint32_t packet[16];
   memset(packet, 0xa5, sizeof(packet));
   char data[8] = {0};
   check(!virgl_inline_packet_write(packet, SIZE_MAX, 0, data, limit + 1));
   check(!virgl_inline_packet_write(packet, 16, UINT32_MAX, data, 1));
   check(!virgl_inline_packet_write(packet, 16, INT_MAX, data, 1));
   check(!virgl_inline_packet_write(packet, 16, 0, NULL, 1));
   check(!virgl_inline_packet_write(NULL, 16, 0, data, 1));
   for (size_t i = 0; i < 16; ++i)
      check(packet[i] == 0xa5a5a5a5);
   check(virgl_inline_packet_write(packet, 16, INT_MAX - 1, data, 1) == 13);
   printf("PASS %u checks: exact odd-byte payloads, zero padding, guard words,16-bit packet limit, short capacity and overflow rejection\n", checks);
   return 0;
}
