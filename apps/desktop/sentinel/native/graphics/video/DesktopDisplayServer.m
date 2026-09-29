#import "DesktopDisplayServer.h"
#import <ImageIO/ImageIO.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <stdatomic.h>
#include <time.h>
#include <fcntl.h>

static const NSUInteger maxBacklog = 512 * 1024;
static BOOL transfer(int fd, void *bytes, size_t count, BOOL sending) {
    uint8_t *cursor = bytes;
    while (count) {
        ssize_t n = sending ? send(fd, cursor, count, 0) : recv(fd, cursor, count, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return NO;
        count -= n; cursor += n;
    }
    return YES;
}
static void fail(NSString *reason) {
    @throw [NSException exceptionWithName:@"DesktopDisplayServer" reason:reason userInfo:nil];
}
static void unlinkOwned(NSString *path, const struct stat *owned) {
    struct stat current;
    if (S_ISSOCK(owned->st_mode) && !lstat(path.fileSystemRepresentation, &current)
        && S_ISSOCK(current.st_mode) && current.st_uid == getuid()
        && current.st_uid == owned->st_uid && current.st_dev == owned->st_dev
        && current.st_ino == owned->st_ino)
        unlink(path.fileSystemRepresentation);
}
static int listenAt(NSString *path, struct stat *owned,
                    void (^reportOwned)(NSDictionary<NSString *, NSString *> *)) {
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if ([path lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >= sizeof(address.sun_path)) fail(@"Display socket path is too long");
    strcpy(address.sun_path, path.fileSystemRepresentation);
    struct stat existing;
    if (!lstat(address.sun_path, &existing) && (!S_ISSOCK(existing.st_mode) || existing.st_uid != getuid()))
        fail([NSString stringWithFormat:@"Cannot bind display socket %@: %s", path, strerror(EADDRINUSE)]);
    // Runtime endpoints survive an unclean exit. Reclaim only an owned socket
    // with no listener; never remove a live endpoint, symlink or regular file.
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) fail(@"Cannot create display socket");
    int bound = bind(fd, (void *)&address, sizeof(address));
    int bindError = errno;
    if (bound && errno == EADDRINUSE) {
        struct stat before, after;
        if (!lstat(address.sun_path, &before) && S_ISSOCK(before.st_mode) && before.st_uid == getuid()) {
            int probe = socket(AF_UNIX, SOCK_STREAM, 0);
            if (probe >= 0) {
                int result = -1, reason = 0;
                if (fcntl(probe, F_SETFL, O_NONBLOCK) == 0) {
                    result = connect(probe, (void *)&address, sizeof(address));
                    reason = errno;
                }
                close(probe);
                if (result && reason == ECONNREFUSED && !lstat(address.sun_path, &after)
                    && before.st_dev == after.st_dev && before.st_ino == after.st_ino
                    && !unlink(address.sun_path)) {
                    bound = bind(fd, (void *)&address, sizeof(address));
                    bindError = errno;
                }
            }
        }
    }
    if (bound) {
        close(fd);
        fail([NSString stringWithFormat:@"Cannot bind display socket %@: %s", path, strerror(bindError)]);
    }
    if (lstat(address.sun_path, owned) || !S_ISSOCK(owned->st_mode) || owned->st_uid != getuid()) {
        close(fd); fail(@"Cannot identify bound display socket");
    }
    if (reportOwned) reportOwned(@{@"event": @"endpoint", @"path": path,
        @"device": [NSString stringWithFormat:@"%llu", (unsigned long long)owned->st_dev],
        @"inode": [NSString stringWithFormat:@"%llu", (unsigned long long)owned->st_ino],
        @"uid": [NSString stringWithFormat:@"%llu", (unsigned long long)owned->st_uid]});
    if (chmod(path.fileSystemRepresentation, 0600) || listen(fd, 8)) {
        close(fd); unlinkOwned(path, owned); fail(@"Cannot listen on display socket");
    }
    return fd;
}

@interface DisplayPeer : NSObject
@property(readonly) int fd;
@property BOOL waitingKey;
@property NSMutableSet<NSNumber *> *held;
- (instancetype)initWithFD:(int)fd;
- (void)offer:(NSData *)data;
- (void)stop;
@end
@implementation DisplayPeer {
    atomic_bool _stopped;
    atomic_ulong _pending;
    dispatch_queue_t _writes;
}
- (instancetype)initWithFD:(int)fd {
    if (!(self = [super init])) return nil;
    _fd = fd; _waitingKey = YES; _held = [NSMutableSet new];
    atomic_init(&_stopped, false); atomic_init(&_pending, 0);
    _writes = dispatch_queue_create("us.arais.sentinel.desktop.viewer", DISPATCH_QUEUE_SERIAL);
    int yes = 1; setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
    struct timeval timeout = {.tv_sec = 0, .tv_usec = 250000};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    return self;
}
- (void)offer:(NSData *)data {
    if (atomic_load(&_stopped)) return;
    if (atomic_fetch_add(&_pending, data.length) + data.length > maxBacklog) {
        atomic_fetch_sub(&_pending, data.length); [self stop]; return;
    }
    dispatch_async(_writes, ^{
        if (!atomic_load(&self->_stopped) && !transfer(self->_fd, (void *)data.bytes, data.length, YES)) [self stop];
        atomic_fetch_sub(&self->_pending, data.length);
    });
}
- (void)stop { if (!atomic_exchange(&_stopped, true)) shutdown(_fd, SHUT_RDWR); }
- (void)dealloc { close(_fd); }
@end

@implementation DesktopDisplayServer {
    NSString *_videoPath, *_controlPath;
    struct stat _videoIdentity, _controlIdentity;
    int _videoListener, _controlListener;
    BOOL _accepting;
    atomic_bool _closed;
    NSMutableSet<DisplayPeer *> *_viewers, *_controls;
    NSLock *_inputLock;
    __weak DisplayPeer *_controller;
    __weak DisplayPeer *_audioPublisher;
    DesktopInput _input;
    void (^_keyframe)(void);
    CVPixelBufferRef _frame;
    int _x, _y;
}
- (instancetype)initWithVideoPath:(NSString *)videoPath controlPath:(NSString *)controlPath
                           input:(DesktopInput)input keyframe:(void (^)(void))keyframe {
    return [self initWithVideoPath:videoPath controlPath:controlPath input:input keyframe:keyframe ownedEndpoint:nil];
}
- (instancetype)initWithVideoPath:(NSString *)videoPath controlPath:(NSString *)controlPath
                           input:(DesktopInput)input keyframe:(void (^)(void))keyframe
                   ownedEndpoint:(void (^)(NSDictionary<NSString *, NSString *> *))ownedEndpoint {
    if (!(self = [super init])) return nil;
    _videoListener = -1; _controlListener = -1;
    atomic_init(&_closed, false);
    _videoPath = videoPath; _controlPath = controlPath; _input = [input copy]; _keyframe = [keyframe copy];
    _viewers = [NSMutableSet new]; _controls = [NSMutableSet new]; _inputLock = [NSLock new];
    @try {
        _videoListener = listenAt(videoPath, &_videoIdentity, ownedEndpoint);
        _controlListener = listenAt(controlPath, &_controlIdentity, ownedEndpoint);
        [self accept:_videoListener video:YES]; [self accept:_controlListener video:NO];
        _accepting = YES;
    } @catch (NSException *error) { [self close]; @throw error; }
    return self;
}
- (void)accept:(int)listener video:(BOOL)video {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        while (!atomic_load(&self->_closed)) {
            int fd = accept(listener, NULL, NULL);
            if (fd < 0) { if (errno == EINTR) continue; break; }
            DisplayPeer *peer = [[DisplayPeer alloc] initWithFD:fd];
            NSMutableSet *peers = video ? self->_viewers : self->_controls;
            @synchronized (self) {
                if (atomic_load(&self->_closed) || peers.count >= 8) { [peer stop]; continue; }
                [peers addObject:peer];
            }
            dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
                @autoreleasepool {
                    @try {
                        if (video) { self->_keyframe(); [self readVideo:peer]; }
                        else [self readControl:peer];
                    } @finally {
                        [peer stop];
                        [self releaseInput:peer disconnect:YES];
                        @synchronized (self) {
                            if (self->_audioPublisher == peer) self->_audioPublisher = nil;
                            [peers removeObject:peer];
                        }
                    }
                }
            });
        }
        close(listener);
    });
}
- (BOOL)input:(NSData *)data peer:(DisplayPeer *)peer {
    if (data.length < 4 || data.length > 132) return NO;
    const uint8_t *bytes = data.bytes;
    uint16_t device, count; memcpy(&device, bytes, 2); memcpy(&count, bytes + 2, 2);
    device = ntohs(device); count = ntohs(count);
    if (device > 2 || count == 0 || count > 16 || data.length != 4 + count * 8) return NO;
    DesktopInputEvent events[16];
    for (unsigned i = 0; i < count; i++) {
        uint16_t type, code; uint32_t value;
        memcpy(&type, bytes + 4 + i * 8, 2); memcpy(&code, bytes + 6 + i * 8, 2); memcpy(&value, bytes + 8 + i * 8, 4);
        events[i] = (DesktopInputEvent){ntohs(type), ntohs(code), (int32_t)ntohl(value)};
        DesktopInputEvent event = events[i];
        BOOL valid = event.type == 0 && event.code == 0 && event.value == 0;
        if (device == 0) valid |= event.type == 2 && (event.code == 0 || event.code == 1 || event.code == 6 || event.code == 8) && event.value >= -120 && event.value <= 120;
        if (device == 1) valid |= (event.type == 3 && event.code <= 1 && event.value >= 0 && event.value <= 65535)
            || (event.type == 1 && event.code >= 272 && event.code <= 276 && (event.value == 0 || event.value == 1));
        if (device == 2) valid |= event.type == 1 && event.code >= 1 && event.code <= 255 && event.value >= 0 && event.value <= 2;
        if (!valid) return NO;
    }
    [_inputLock lock];
    if (_controller && _controller != peer) { [_inputLock unlock]; return YES; }
    BOOL ok = _input(device, events, count);
    if (ok) for (unsigned i = 0; i < count; i++) {
        DesktopInputEvent event = events[i];
        if (device == 1 && event.type == 3) { if (event.code == 0) _x = event.value; else _y = event.value; }
        if (event.type == 1) {
            NSNumber *key = @(((uint32_t)device << 16) | event.code);
            if (event.value) [peer.held addObject:key]; else [peer.held removeObject:key];
        }
    }
    [_inputLock unlock];
    return ok;
}
- (void)releaseInput:(DisplayPeer *)peer {
    [self releaseInput:peer disconnect:NO];
}
- (void)releaseInput:(DisplayPeer *)peer disconnect:(BOOL)disconnect {
    [_inputLock lock];
    for (NSNumber *key in peer.held) {
        uint32_t value = key.unsignedIntValue;
        DesktopInputEvent release[] = {{1, value & 65535, 0}, {0, 0, 0}};
        _input(value >> 16, release, 2);
    }
    [peer.held removeAllObjects];
    if (disconnect && _controller == peer) _controller = nil;
    [_inputLock unlock];
}
- (BOOL)acquire:(DisplayPeer *)peer {
    @synchronized (self) {
        [_inputLock lock];
        if (_controller && _controller != peer) { [_inputLock unlock]; return NO; }
        _controller = peer;
        [_inputLock unlock];
        // Do not inherit a modifier/button held by a human before the batch.
        for (DisplayPeer *viewer in _viewers) [self releaseInput:viewer];
        return YES;
    }
}
- (void)readVideo:(DisplayPeer *)peer {
    for (;;) {
        uint8_t header[16];
        if (!transfer(peer.fd, header, sizeof(header), NO)) return;
        uint32_t size; memcpy(&size, header + 4, 4); size = ntohl(size);
        if (header[1] || header[2] || header[3] || size > 132) return;
        for (unsigned i = 8; i < 16; i++) if (header[i]) return;
        if (header[0] == 4 && !size) { _keyframe(); continue; }
        if (header[0] != 3) return;
        NSMutableData *data = [NSMutableData dataWithLength:size];
        if (!transfer(peer.fd, data.mutableBytes, size, NO) || ![self input:data peer:peer]) return;
    }
}
- (void)publish:(NSData *)packet {
    if (packet.length < 16) return;
    const uint8_t *bytes = packet.bytes;
    @synchronized (self) {
        for (DisplayPeer *peer in _viewers) {
            if (bytes[0] == 2 && peer.waitingKey && !(bytes[1] & 1)) continue;
            if (bytes[0] == 2 && bytes[1] & 1) peer.waitingKey = NO;
            [peer offer:packet];
        }
    }
}
- (void)setFrame:(CVPixelBufferRef)frame {
    @synchronized (self) {
        if (atomic_load(&_closed)) return;
        if (_frame) CVPixelBufferRelease(_frame);
        _frame = CVPixelBufferRetain(frame);
    }
}
- (BOOL)acquireAudio:(DisplayPeer *)peer {
    @synchronized (self) {
        if (_audioPublisher) return NO;
        _audioPublisher = peer;
        return YES;
    }
}
- (void)readAudio:(DisplayPeer *)peer {
    // A single guest output monitor supplies bounded 10 ms Opus packets.
    // Keep this independent of the exclusive computer-input lease.
    for (;;) {
        uint16_t length;
        if (!transfer(peer.fd, &length, sizeof(length), NO)) return;
        size_t size = ntohs(length);
        if (!size || size > 1275) return;
        uint8_t bytes[16 + 1275] = {5};
        if (!transfer(peer.fd, bytes + 16, size, NO)) return;
        unsigned config = bytes[16] >> 3, code = bytes[16] & 3;
        unsigned samples = config >= 16 ? (120u << (config & 3))
            : config >= 12 ? (480u << (config & 1))
            : (unsigned[]){480, 960, 1920, 2880}[config & 3];
        unsigned frames = code == 0 ? 1 : code != 3 ? 2 : size >= 2 ? bytes[17] & 63 : 0;
        if (samples * frames != 480) return;
        uint32_t count = htonl((uint32_t)size);
        uint64_t timestamp = CFSwapInt64HostToBig(clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / 1000);
        memcpy(bytes + 4, &count, sizeof(count));
        memcpy(bytes + 8, &timestamp, sizeof(timestamp));
        [self publish:[NSData dataWithBytes:bytes length:16 + size]];
    }
}
- (NSDictionary *)snapshot:(BOOL)image {
    CVPixelBufferRef frame;
    @synchronized (self) { frame = _frame ? CVPixelBufferRetain(_frame) : NULL; }
    if (!frame) return @{@"ok": @NO, @"error": @"No active desktop display"};
    @try {
        size_t width = CVPixelBufferGetWidth(frame), height = CVPixelBufferGetHeight(frame);
        [_inputLock lock]; int x = _x, y = _y; [_inputLock unlock];
        NSMutableDictionary *reply = [@{@"ok": @YES, @"viewport": @{@"width": @(width), @"height": @(height)},
            @"cursor": @{@"x": @((int64_t)x * (width - 1) / 65535), @"y": @((int64_t)y * (height - 1) / 65535)}} mutableCopy];
        if (!image) return reply;
        if (width * height > 16000000) fail(@"Desktop exceeds screenshot pixel limit");
        if (CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly)) fail(@"Cannot read display");
        CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CGContextRef context = CGBitmapContextCreate(CVPixelBufferGetBaseAddress(frame), width, height, 8,
            CVPixelBufferGetBytesPerRow(frame), color, kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst);
        CGColorSpaceRelease(color);
        if (!context) { CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly); fail(@"Cannot capture display"); }
        CGImageRef captured = CGBitmapContextCreateImage(context);
        NSMutableData *data; NSString *mime = @"image/png";
        for (NSNumber *quality in @[@1, @0.85, @0.65, @0.45]) {
            BOOL png = quality.doubleValue == 1;
            data = [NSMutableData new];
            CGImageDestinationRef destination = CGImageDestinationCreateWithData((__bridge CFMutableDataRef)data,
                png ? CFSTR("public.png") : CFSTR("public.jpeg"), 1, NULL);
            if (!destination || !captured) {
                if (destination) CFRelease(destination);
                data = nil; break;
            }
            CGImageDestinationAddImage(destination, captured, (__bridge CFDictionaryRef)@{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality: quality});
            BOOL encoded = CGImageDestinationFinalize(destination); CFRelease(destination);
            if (!encoded) { data = nil; break; }
            mime = png ? @"image/png" : @"image/jpeg";
            if (data.length <= 1400000) break;
        }
        if (captured) CGImageRelease(captured);
        CGContextRelease(context);
        CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
        if (!data || data.length > 1400000) fail(@"Screenshot exceeds attachment limit; reduce desktop resolution");
        reply[@"screenshot"] = [NSString stringWithFormat:@"data:%@;base64,%@", mime, [data base64EncodedStringWithOptions:0]];
        return reply;
    } @finally { CVPixelBufferRelease(frame); }
}
- (void)readControl:(DisplayPeer *)peer {
    // One connection may contain an entire computer-use batch; disconnect releases held input.
    struct timeval timeout = {.tv_sec = 40, .tv_usec = 0};
    setsockopt(peer.fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    for (;;) {
        NSMutableData *line = [NSMutableData new]; uint8_t byte = 0;
        while (transfer(peer.fd, &byte, 1, NO)) {
            if (byte == '\n') break;
            [line appendBytes:&byte length:1];
            if (line.length > 16384) return;
        }
        if (byte != '\n' || !line.length) return;
        NSDictionary *reply;
        BOOL audio = NO;
        @try {
            NSDictionary *command = [NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
            if (![command isKindOfClass:NSDictionary.class]) fail(@"Invalid display command");
            NSString *operation = command[@"action"];
            if ([operation isEqual:@"audio"]) {
                audio = [self acquireAudio:peer];
                reply = @{@"ok": @(audio)};
            } else if ([operation isEqual:@"acquire"]) reply = @{@"ok": @([self acquire:peer])};
            else if ([operation isEqual:@"snapshot"] || [operation isEqual:@"status"]) reply = [self snapshot:[operation isEqual:@"snapshot"]];
            else if ([operation isEqual:@"input"]) {
                if (![command[@"data"] isKindOfClass:NSString.class]) fail(@"Invalid input");
                NSData *data = [[NSData alloc] initWithBase64EncodedString:command[@"data"] options:0];
                reply = @{@"ok": @([self input:data peer:peer])};
            } else if ([operation isEqual:@"release"]) { [self releaseInput:peer]; reply = @{@"ok": @YES}; }
            else fail(@"Unknown display command");
        } @catch (NSException *error) { reply = @{@"ok": @NO, @"error": error.reason ?: @"Display command failed"}; }
        NSMutableData *data = [[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] mutableCopy];
        [data appendBytes:"\n" length:1];
        if (!transfer(peer.fd, data.mutableBytes, data.length, YES)) return;
        if (audio) { [self readAudio:peer]; return; }
    }
}
- (void)close {
    if (atomic_exchange(&_closed, true)) return;
    if (_videoListener >= 0) {
        shutdown(_videoListener, SHUT_RDWR);
        if (!_accepting) close(_videoListener);
        unlinkOwned(_videoPath, &_videoIdentity);
    }
    if (_controlListener >= 0) {
        shutdown(_controlListener, SHUT_RDWR);
        if (!_accepting) close(_controlListener);
        unlinkOwned(_controlPath, &_controlIdentity);
    }
    @synchronized (self) {
        for (DisplayPeer *peer in _viewers) [peer stop];
        for (DisplayPeer *peer in _controls) [peer stop];
        if (_frame) { CVPixelBufferRelease(_frame); _frame = NULL; }
    }
}
@end
