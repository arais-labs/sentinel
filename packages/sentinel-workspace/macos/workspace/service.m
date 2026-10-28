// The computer-use display service (see service.h). A thread per connection; requests are short
// and at most a few batches run at once. One batch at a time may hold the lease ("acquire"): its
// input goes through and the viewer's is dropped.
#import "service.h"

#import <Foundation/Foundation.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#import "display.h"
#include "input.h"

static NSLock *leaseLock;
static int leaseHolder = -1;  // the connection holding the lease

struct client {
  int fd;
  bool held[INPUT_HELD];
};

static BOOL sendLine(int fd, NSData *data) {
  const uint8_t *p = data.bytes;
  size_t left = data.length;
  while (left) {
    ssize_t n = send(fd, p, left, 0);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return NO;
    p += n, left -= (size_t)n;
  }
  return YES;
}

static BOOL reply(int fd, NSDictionary *object) {
  NSMutableData *data = [[NSJSONSerialization dataWithJSONObject:object options:0 error:NULL] mutableCopy];
  [data appendBytes:"\n" length:1];
  return sendLine(fd, data);
}

static BOOL error(int fd, NSString *message) { return reply(fd, @{@"ok" : @NO, @"error" : message}); }

// {"ok": true, "viewport": ..., "cursor": ...} plus `extra`.
static BOOL view(int fd, NSDictionary *extra) {
  uint32_t w, h;
  uint16_t x, y;
  display_viewport(&w, &h);
  input_pointer_position(&x, &y);
  NSMutableDictionary *answer = [@{
    @"ok" : @YES,
    @"viewport" : @{@"width" : @(w), @"height" : @(h)},
    @"cursor" : @{@"x" : @((uint64_t)x * (w ? w - 1 : 0) / 65535), @"y" : @((uint64_t)y * (h ? h - 1 : 0) / 65535)},
  } mutableCopy];
  [answer addEntriesFromDictionary:extra];
  return reply(fd, answer);
}

static BOOL request(struct client *c, NSDictionary *line) {
  NSString *action = [line[@"action"] isKindOfClass:NSString.class] ? line[@"action"] : nil;
  if ([action isEqual:@"acquire"]) {
    [leaseLock lock];
    const BOOL free = leaseHolder < 0 || leaseHolder == c->fd;
    if (free) leaseHolder = c->fd, input_set_leased(true);
    [leaseLock unlock];
    return free ? reply(c->fd, @{@"ok" : @YES}) : error(c->fd, @"Virtual display is busy");
  }
  if ([action isEqual:@"status"]) return view(c->fd, @{});
  if ([action isEqual:@"input"]) {
    [leaseLock lock];
    const BOOL other = leaseHolder >= 0 && leaseHolder != c->fd;
    [leaseLock unlock];
    if (other) return reply(c->fd, @{@"ok" : @YES});  // another batch controls input
    NSString *data = [line[@"data"] isKindOfClass:NSString.class] ? line[@"data"] : nil;
    NSData *raw = data ? [[NSData alloc] initWithBase64EncodedString:data options:0] : nil;
    if (!raw || !input_packet(raw.bytes, (uint32_t)raw.length, c->held)) return error(c->fd, @"Invalid input");
    return reply(c->fd, @{@"ok" : @YES});
  }
  if ([action isEqual:@"release"]) {
    input_release(c->held);
    return reply(c->fd, @{@"ok" : @YES});
  }
  if ([action isEqual:@"snapshot"]) {
    NSData *image = display_snapshot();
    if (!image) return error(c->fd, @"No active desktop display");
    const BOOL png = image.length >= 4 && !memcmp(image.bytes, "\x89PNG", 4);
    NSString *url = [NSString stringWithFormat:@"data:image/%@;base64,%@", png ? @"png" : @"jpeg",
                                               [image base64EncodedStringWithOptions:0]];
    return view(c->fd, @{@"screenshot" : url});
  }
  return error(c->fd, @"Unknown display command");
}

static void *serve(void *arg) {
  @autoreleasepool {
    struct client *c = arg;
    NSMutableData *buffer = [NSMutableData data];
    uint8_t chunk[4096];
    for (;;) {
      ssize_t n = recv(c->fd, chunk, sizeof chunk, 0);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) break;
      [buffer appendBytes:chunk length:(size_t)n];
      if (buffer.length > 16384) break;  // a request longer than any valid one
      BOOL open = YES;
      for (;;) {
        const void *nl = memchr(buffer.bytes, '\n', buffer.length);
        if (!nl) break;
        const size_t length = (size_t)((const uint8_t *)nl - (const uint8_t *)buffer.bytes);
        @autoreleasepool {
          id line = [NSJSONSerialization JSONObjectWithData:[buffer subdataWithRange:NSMakeRange(0, length)]
                                                    options:0
                                                      error:NULL];
          open = [line isKindOfClass:NSDictionary.class] ? request(c, line) : error(c->fd, @"Invalid display command");
        }
        [buffer replaceBytesInRange:NSMakeRange(0, length + 1) withBytes:NULL length:0];
        if (!open) break;
      }
      if (!open) break;
    }
    input_release(c->held);
    [leaseLock lock];
    if (leaseHolder == c->fd) leaseHolder = -1, input_set_leased(false);
    [leaseLock unlock];
    close(c->fd);
    free(c);
  }
  return NULL;
}

static void *accept_loop(void *arg) {
  const int server = (int)(intptr_t)arg;
  for (;;) {
    const int fd = accept(server, NULL, NULL);
    if (fd < 0) {
      if (errno == EINTR) continue;
      perror("sentinel-workspace: display service accept");
      return NULL;
    }
    struct client *c = calloc(1, sizeof *c);
    c->fd = fd;
    pthread_t thread;
    if (pthread_create(&thread, NULL, serve, c)) {
      close(fd);
      free(c);
      continue;
    }
    pthread_detach(thread);
  }
}

void service_start(const char *path) {
  leaseLock = [NSLock new];
  const int server = socket(AF_UNIX, SOCK_STREAM, 0);
  struct sockaddr_un addr = {.sun_family = AF_UNIX};
  if (server < 0 || strlcpy(addr.sun_path, path, sizeof addr.sun_path) >= sizeof addr.sun_path) {
    fprintf(stderr, "sentinel-workspace: display service socket path too long\n");
    exit(1);
  }
  unlink(path);
  if (bind(server, (struct sockaddr *)&addr, sizeof addr) || listen(server, 4)) {
    perror("sentinel-workspace: display service");
    exit(1);
  }
  pthread_t thread;
  pthread_create(&thread, NULL, accept_loop, (void *)(intptr_t)server);
  pthread_detach(thread);
}
