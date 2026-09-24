#pragma once
#include <stdint.h>
typedef uint8_t __u8;
typedef uint16_t __u16;
typedef uint32_t __u32;
typedef uint64_t __u64;
typedef int8_t __s8;
typedef int16_t __s16;
typedef int32_t __s32;
typedef int64_t __s64;
typedef __u16 __le16;
typedef __u32 __le32;
typedef __u64 __le64;
#define __bitwise
#define __aligned_u64 __u64 __attribute__((aligned(8)))
