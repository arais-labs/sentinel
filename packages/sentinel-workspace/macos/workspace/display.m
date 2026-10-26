// The desktop (see display.h). libkrun calls the display backend on its GPU worker thread: each
// guest flush copies the scanout into a pixel buffer handed out by alloc_frame, which present_frame
// sends to the encoder. A frame the encoder cannot take yet is kept and sent when it has room, so
// an idle desktop always ends on its newest frame. The guest's cursor plane goes to viewers as its
// image, which they show as their own pointer: moving it costs no frame.
#import "display.h"

#import <CoreVideo/CoreVideo.h>
#import <ImageIO/ImageIO.h>
#include <libkrun_display.h>
#include <stdatomic.h>
#include <time.h>
#include <unistd.h>

#import "DesktopVideoEncoder.h"
#include "input.h"

enum { FRAMES = 4 };  // pixel buffers handed to libkrun and not yet presented

static struct {
  NSRecursiveLock *lock;  // the encoder may call capacityAvailable from inside encode:
  uint32_t displayWidth, displayHeight, hz;
  uint32_t width, height;  // the scanout
  CVPixelBufferPoolRef pool;
  CVPixelBufferRef frames[FRAMES];  // by frame id, between alloc and present
  uint32_t next;
  CVPixelBufferRef latest;   // the newest presented frame (snapshots)
  CVPixelBufferRef pending;  // presented while the encoder was full
  DesktopVideoEncoder *encoder;
} display = {.lock = nil};

// The displays' refresh rates, which follow the viewers' screens (§6 refresh packets).
enum { REFRESHES = 2 };  // the boot framebuffer's display and the GPU's
static KrunDisplayRefresh refreshes[REFRESHES];
static unsigned refreshCount;

static int mediaFD = -1;
static dispatch_queue_t mediaQueue;
static atomic_ulong mediaBacklog;
static atomic_bool mediaWaitKey, mediaClosed;
// Whether the workspace has viewers (§6 watching, or a key frame request): frames are encoded only
// for them. The newest frame is kept either way, for computer use's screenshots.
static atomic_bool watched;

static uint64_t wallUs(void) { return clock_gettime_nsec_np(CLOCK_REALTIME) / 1000; }

// Writes never block the encoder: past 8 MiB unsent, frames are dropped until the next key frame.
static void publish(NSData *packet) {
  if (mediaFD < 0 || atomic_load(&mediaClosed)) return;
  const uint8_t *bytes = packet.bytes;
  const BOOL frame = bytes[0] == 2, key = frame && (bytes[1] & 1);
  if (frame && !key && atomic_load(&mediaWaitKey)) return;
  if (frame && atomic_load(&mediaBacklog) + packet.length > (8u << 20)) {
    atomic_store(&mediaWaitKey, true);
    [display.encoder requestKeyframe];
    return;
  }
  if (key) atomic_store(&mediaWaitKey, false);
  atomic_fetch_add(&mediaBacklog, packet.length);
  dispatch_async(mediaQueue, ^{
    const uint8_t *p = packet.bytes;
    size_t left = packet.length;
    while (left && !atomic_load(&mediaClosed)) {
      ssize_t n = write(mediaFD, p, left);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) {
        atomic_store(&mediaClosed, true);
        break;
      }
      p += n, left -= (size_t)n;
    }
    atomic_fetch_sub(&mediaBacklog, packet.length);
  });
}

// Sends the pending frame if the encoder has room; display.lock held.
static void flushPending(void) {
  if (!display.pending || !display.encoder.canAcceptFrame) return;
  [display.encoder encode:display.pending];
  CVPixelBufferRelease(display.pending);
  display.pending = NULL;
}

// A new scanout size: a new pool and encoder. display.lock held.
static BOOL configure(uint32_t width, uint32_t height) {
  if (width < 64 || height < 64 || width > 8192 || height > 8192) return NO;
  if (display.pool && width == display.width && height == display.height) return YES;
  [display.encoder close];
  display.encoder = nil;
  for (unsigned i = 0; i < FRAMES; i++) {
    if (display.frames[i]) CVPixelBufferRelease(display.frames[i]);
    display.frames[i] = NULL;
  }
  if (display.pending) CVPixelBufferRelease(display.pending);
  display.pending = NULL;
  if (display.pool) CVPixelBufferPoolRelease(display.pool);
  display.pool = NULL;
  NSDictionary *attributes = @{
    (__bridge NSString *)kCVPixelBufferPixelFormatTypeKey : @(kCVPixelFormatType_32BGRA),
    (__bridge NSString *)kCVPixelBufferWidthKey : @(width),
    (__bridge NSString *)kCVPixelBufferHeightKey : @(height),
    (__bridge NSString *)kCVPixelBufferBytesPerRowAlignmentKey : @(4),
    (__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey : @{},
  };
  if (CVPixelBufferPoolCreate(NULL, NULL, (__bridge CFDictionaryRef)attributes, &display.pool)) return NO;
  display.width = width, display.height = height;
  // Even dimensions for H.264; an odd scanout loses its last column or row.
  display.encoder = [[DesktopVideoEncoder alloc] initWithWidth:(int)(width & ~1u)
                                                        height:(int)(height & ~1u)
                                                           fps:(int)display.hz
                                                       bitrate:24000000
                                                        output:^(NSData *packet) {
                                                          if (((const uint8_t *)packet.bytes)[0] == 2) {
                                                            // Frames carry their send time (wall clock, µs).
                                                            NSMutableData *stamped = [packet mutableCopy];
                                                            const uint64_t sent = CFSwapInt64HostToBig(wallUs());
                                                            memcpy((uint8_t *)stamped.mutableBytes + 8, &sent, 8);
                                                            packet = stamped;
                                                          }
                                                          publish(packet);
                                                        }];
  display.encoder.capacityAvailable = ^{
    [display.lock lock];
    flushPending();
    [display.lock unlock];
  };
  fprintf(stderr, "sentinel-workspace: display %ux%u\n", width, height);
  return YES;
}

static int32_t disableScanout(void *self, uint32_t scanout) {
  (void)self, (void)scanout;
  return 0;
}

static int32_t configureScanout(void *self, uint32_t scanout, uint32_t displayWidth, uint32_t displayHeight,
                                uint32_t width, uint32_t height, uint32_t format) {
  (void)self, (void)displayWidth, (void)displayHeight;
  if (scanout != 0) return KRUN_DISPLAY_ERR_INVALID_SCANOUT_ID;
  if (format != KRUN_DISPLAY_FORMAT_B8G8R8A8_UNORM && format != KRUN_DISPLAY_FORMAT_B8G8R8X8_UNORM) {
    fprintf(stderr, "sentinel-workspace: unsupported scanout format %u\n", format);
    return KRUN_DISPLAY_ERR_INVALID_PARAM;
  }
  [display.lock lock];
  const BOOL ok = configure(width, height);
  [display.lock unlock];
  return ok ? 0 : KRUN_DISPLAY_ERR_INVALID_PARAM;
}

static int32_t allocFrame(void *self, uint32_t scanout, uint8_t **buffer, size_t *size) {
  (void)self;
  if (scanout != 0) return KRUN_DISPLAY_ERR_INVALID_SCANOUT_ID;
  [display.lock lock];
  int32_t id = KRUN_DISPLAY_ERR_OUT_OF_BUFFERS;
  CVPixelBufferRef frame = NULL;
  const uint32_t slot = display.next;
  if (display.pool && !display.frames[slot] &&
      !CVPixelBufferPoolCreatePixelBuffer(NULL, display.pool, &frame) &&
      CVPixelBufferGetBytesPerRow(frame) == display.width * 4u &&
      !CVPixelBufferLockBaseAddress(frame, 0)) {
    display.frames[slot] = frame;
    display.next = (slot + 1) % FRAMES;
    *buffer = CVPixelBufferGetBaseAddress(frame);
    *size = (size_t)display.width * display.height * 4;
    id = (int32_t)slot;
  } else if (frame) {
    CVPixelBufferRelease(frame);
  }
  [display.lock unlock];
  return id;
}

static int32_t presentFrame(void *self, uint32_t scanout, uint32_t id, const struct krun_rect *damage) {
  (void)self, (void)scanout, (void)damage;
  if (id >= FRAMES) return KRUN_DISPLAY_ERR_INVALID_PARAM;
  [display.lock lock];
  CVPixelBufferRef frame = display.frames[id];
  display.frames[id] = NULL;
  if (frame) {
    CVPixelBufferUnlockBaseAddress(frame, 0);
    if (display.latest) CVPixelBufferRelease(display.latest);
    display.latest = CVPixelBufferRetain(frame);
    if (display.pending) CVPixelBufferRelease(display.pending);
    display.pending = NULL;
    if (atomic_load(&watched)) {
      display.pending = frame;  // ownership moves to pending
      flushPending();
    } else {
      CVPixelBufferRelease(frame);
    }
  }
  [display.lock unlock];
  return frame ? 0 : KRUN_DISPLAY_ERR_INVALID_PARAM;
}

void display_packet(uint8_t type, const void *payload, uint32_t size) {
  NSMutableData *packet = [NSMutableData dataWithLength:16 + size];
  uint8_t *p = packet.mutableBytes;
  p[0] = type;
  const uint32_t length = htonl(size);
  const uint64_t now = CFSwapInt64HostToBig(wallUs());
  memcpy(p + 4, &length, 4);
  memcpy(p + 8, &now, 8);
  memcpy(p + 16, payload, size);
  publish(packet);
}

// Cursor packet (§6 type 6): hotspot and size (u16be each), then straight-alpha RGBA rows; an empty
// payload hides it. Viewers take up to 128x128.
static NSData *lastCursor;

static int32_t updateCursor(void *self, uint32_t scanout, uint32_t hotX, uint32_t hotY, uint32_t width,
                            uint32_t height, const uint8_t *pixels) {
  (void)self;
  if (scanout != 0) return KRUN_DISPLAY_ERR_INVALID_SCANOUT_ID;
  if (pixels && (!width || !height || width > 128 || height > 128 || hotX >= width || hotY >= height))
    return KRUN_DISPLAY_ERR_INVALID_PARAM;
  NSMutableData *payload = [NSMutableData dataWithLength:pixels ? 8 + (size_t)width * height * 4 : 0];
  if (pixels) {
    uint8_t *p = payload.mutableBytes;
    const uint16_t header[4] = {htons((uint16_t)hotX), htons((uint16_t)hotY), htons((uint16_t)width),
                                htons((uint16_t)height)};
    memcpy(p, header, 8);
    uint8_t *out = p + 8;
    // Premultiplied BGRA to straight RGBA.
    for (size_t i = 0; i < (size_t)width * height; i++, pixels += 4, out += 4) {
      const uint8_t a = pixels[3];
      if (!a) continue;
      out[0] = (uint8_t)MIN(255u, pixels[2] * 255u / a);
      out[1] = (uint8_t)MIN(255u, pixels[1] * 255u / a);
      out[2] = (uint8_t)MIN(255u, pixels[0] * 255u / a);
      out[3] = a;
    }
  }
  // The same image again (the guest re-sends it on every shape check) changes nothing.
  if ([lastCursor isEqualToData:payload]) return 0;
  lastCursor = payload;
  display_packet(6, payload.bytes, (uint32_t)payload.length);
  return 0;
}

KrunDisplayBackend display_backend(uint32_t width, uint32_t height, uint32_t hz) {
  // One display, whichever device draws on it (the boot framebuffer, then the GPU): each gets its
  // own handle onto it.
  if (!display.lock) {
    display.lock = [NSRecursiveLock new];
    display.displayWidth = width, display.displayHeight = height, display.hz = hz;
  }
  struct krun_display_backend backend = {
      .features = KRUN_DISPLAY_FEATURE_BASIC_FRAMEBUFFER,
      .vtable.basic_framebuffer = {.disable_scanout = disableScanout,
                                   .configure_scanout = configureScanout,
                                   .alloc_frame = allocFrame,
                                   .present_frame = presentFrame,
                                   .update_cursor = updateCursor}};
  KrunError err = NULL;
  KrunDisplayBackend displays = krun_display_backend_new(&backend, sizeof backend, &err);
  if (err) {
    fprintf(stderr, "sentinel-workspace: display backend failed\n");
    exit(1);
  }
  KrunDisplayInfoBuilder info = krun_display_info_builder_new(width, height);
  krun_display_info_builder_refresh_rate(&info, hz);
  krun_display_backend_add_display(displays, info);
  KrunDisplayRefresh refresh = krun_display_backend_refresh(displays, 0, &err);
  if (!err && refreshCount < REFRESHES) refreshes[refreshCount++] = refresh;
  return displays;
}

static BOOL readFull(int fd, void *bytes, size_t count) {
  for (uint8_t *p = bytes; count;) {
    ssize_t n = read(fd, p, count);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return NO;
    p += n, count -= (size_t)n;
  }
  return YES;
}

void display_media(int fd) {
  mediaFD = fd;
  mediaQueue = dispatch_queue_create("sentinel-workspace.media", DISPATCH_QUEUE_SERIAL);
  // Viewer requests: input (3), key frames (4), the viewers' refresh rate (7, Hz u16be) and whether
  // there are viewers (8, u8).
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
    uint8_t header[16], payload[1024];
    while (readFull(mediaFD, header, sizeof header)) {
      uint32_t size;
      memcpy(&size, header + 4, 4);
      size = ntohl(size);
      if (size > sizeof payload || (size && !readFull(mediaFD, payload, size))) break;
      if (header[0] == 3) input_packet(payload, size, NULL);
      if (header[0] == 7 && size == 2) {
        const uint32_t hz = (uint32_t)payload[0] << 8 | payload[1];
        for (unsigned i = 0; i < refreshCount; i++) krun_display_refresh_set(refreshes[i], hz, NULL);
        fprintf(stderr, "sentinel-workspace: display refresh %u Hz\n", hz);
      }
      if (header[0] == 8 && size == 1 && !payload[0]) atomic_store(&watched, false);
      if (header[0] == 4 || (header[0] == 8 && size == 1 && payload[0])) {
        // Watched from now on: the current picture goes out first, as a key frame, even on a
        // desktop that does not change.
        [display.lock lock];
        atomic_store(&watched, true);
        if (!display.pending && display.latest) display.pending = CVPixelBufferRetain(display.latest);
        [display.encoder requestKeyframe];
        flushPending();
        [display.lock unlock];
      }
    }
    atomic_store(&mediaClosed, true);
  });
}

void display_viewport(uint32_t *width, uint32_t *height) {
  [display.lock lock];
  *width = display.width ? display.width : display.displayWidth;
  *height = display.height ? display.height : display.displayHeight;
  [display.lock unlock];
}

NSData *display_snapshot(void) {
  [display.lock lock];
  CVPixelBufferRef frame = display.latest ? CVPixelBufferRetain(display.latest) : NULL;
  [display.lock unlock];
  if (!frame) return nil;
  NSData *result = nil;
  if (!CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly)) {
    CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef context =
        CGBitmapContextCreate(CVPixelBufferGetBaseAddress(frame), CVPixelBufferGetWidth(frame),
                              CVPixelBufferGetHeight(frame), 8, CVPixelBufferGetBytesPerRow(frame), color,
                              kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst);
    CGColorSpaceRelease(color);
    CGImageRef image = context ? CGBitmapContextCreateImage(context) : NULL;
    for (NSNumber *quality in @[ @1, @0.85, @0.65, @0.45 ]) {
      if (!image) break;
      const BOOL png = quality.doubleValue == 1;
      NSMutableData *data = [NSMutableData new];
      CGImageDestinationRef out = CGImageDestinationCreateWithData(
          (__bridge CFMutableDataRef)data, png ? CFSTR("public.png") : CFSTR("public.jpeg"), 1, NULL);
      if (!out) break;
      CGImageDestinationAddImage(
          out, image,
          (__bridge CFDictionaryRef) @{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality : quality});
      const BOOL ok = CGImageDestinationFinalize(out);
      CFRelease(out);
      if (!ok) break;
      if (data.length <= 1400000) {
        result = data;
        break;
      }
    }
    if (image) CGImageRelease(image);
    if (context) CGContextRelease(context);
    CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
  }
  CVPixelBufferRelease(frame);
  return result;
}
