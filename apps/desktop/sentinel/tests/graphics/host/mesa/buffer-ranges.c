#include "zink_buffer_ranges.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static unsigned state = 17;
static unsigned random_word(void)
{
   state ^= state << 13;
   state ^= state >> 17;
   state ^= state << 5;
   return state;
}
static double now(void)
{
   struct timespec t;
   clock_gettime(CLOCK_MONOTONIC, &t);
   return t.tv_sec + t.tv_nsec / 1e9;
}
int main(void)
{
   struct pipe_box *boxes = calloc(100001, sizeof(*boxes));
   assert(boxes);
   unsigned long checks = 0;
   for (unsigned cycle = 0; cycle < 250; cycle++) {
      unsigned char reference[4096] = {0};
      unsigned count = 0;
      for (unsigned step = 0; step < 1000; step++) {
         unsigned start = random_word() % 4096;
         unsigned width = random_word() % 65;
         if (width > 4096 - start)
            width = 4096 - start;
         struct pipe_box box = {.x = start, .width = width};
         bool expected = false;
         for (unsigned j = start; j < start + width; j++)
            expected |= reference[j] != 0;
         assert(zink_buffer_range_intersects(boxes, count, &box) == expected);
         checks++;
         count = zink_buffer_range_add(boxes, count, &box);
         for (unsigned j = start; j < start + width; j++)
            reference[j] = 1;
         for (unsigned j = 0; j < count; j++) {
            assert(boxes[j].width > 0);
            if (j)
               assert(zink_buffer_range_end(&boxes[j - 1]) < (unsigned)boxes[j].x);
         }
         if (step % 31 == 0)
            for (unsigned j = 0; j < 4096; j++) {
               struct pipe_box q = {.x = j, .width = 1};
               assert(zink_buffer_range_intersects(boxes, count, &q) == (reference[j] != 0));
               checks++;
            }
      }
   }
   unsigned count = 0;
   double begin = now();
   for (unsigned i = 0; i < 100000; i++) {
      struct pipe_box box = {.x = (int)(i * 8), .width = 3};
      assert(!zink_buffer_range_intersects(boxes, count, &box));
      count = zink_buffer_range_add(boxes, count, &box);
   }
   assert(count == 100000);
   printf("PASS %lu exact randomized reference checks; 100000 disjoint inserts %.3f ms\n", checks,
          (now() - begin) * 1000);
   struct pipe_box bridge = {.x = 1, .width = 799998};
   count = zink_buffer_range_add(boxes, count, &bridge);
   assert(count == 1 && boxes[0].x == 0 && boxes[0].width == 799999);
   count = 0;
   for (unsigned i = 4096; i > 0; i--) {
      struct pipe_box box = {.x = (int)((i - 1) * 8), .width = 3};
      count = zink_buffer_range_add(boxes, count, &box);
   }
   assert(count == 4096);
   for (unsigned i = 0; i < count; i++) {
      assert(boxes[i].x == (int)(i * 8) && boxes[i].width == 3);
      struct pipe_box gap = {.x = (int)(i * 8 + 3), .width = 5};
      assert(!zink_buffer_range_intersects(boxes, count, &gap));
   }
   count = 0;
   struct pipe_box large = {.x = (int)0x80000000u, .width = 0x100};
   count = zink_buffer_range_add(boxes, count, &large);
   struct pipe_box hit = {.x = (int)0x800000ffu, .width = 1};
   struct pipe_box miss = {.x = (int)0x80000100u, .width = 1};
   assert(zink_buffer_range_intersects(boxes, count, &hit));
   assert(!zink_buffer_range_intersects(boxes, count, &miss));
   count = 0;
   struct pipe_box lower = {.x = 0, .width = 0x7fffffff};
   struct pipe_box upper = {.x = 0x7fffffff, .width = (int)0x80000000u};
   count = zink_buffer_range_add(boxes, count, &upper);
   count = zink_buffer_range_add(boxes, count, &lower);
   assert(count == 1 && boxes[0].x == 0 && (unsigned)boxes[0].width == UINT32_MAX);
   struct pipe_box final_byte = {.x = (int)(UINT32_MAX - 1), .width = 1};
   struct pipe_box one_past = {.x = (int)UINT32_MAX, .width = 1};
   assert(zink_buffer_range_intersects(boxes, count, &final_byte));
   assert(!zink_buffer_range_intersects(boxes, count, &one_past));
   free(boxes);
   puts("PASS bridge coalescing, empty ranges, 32-bit unsigned offset boundary");
}
