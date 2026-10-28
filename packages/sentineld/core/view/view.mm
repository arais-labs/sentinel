// sentineld view: a workspace's desktop in a native macOS window, for using sentineld on its own
// (DESIGN.md §6). It does what the app's viewer does:
// H.264 frames decoded and shown by AVSampleBufferDisplayLayer, the guest cursor as the Mac pointer,
// mouse, wheel and keys as the same evdev input packets, Command-C/X/V through the workspace's
// clipboard, and the guest's sound (Opus).
//
//   sentineld view (--state DIR | --socket PATH) WORKSPACE
#import <AVFoundation/AVFoundation.h>
#import <AppKit/AppKit.h>
#import <CoreMedia/CoreMedia.h>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "../util/json.hpp"
#include "transport.hpp"
#include "view.hpp"

namespace {

using sentineld::json::Object;
using sentineld::json::Value;
namespace json = sentineld::json;

std::string gSocket, gWorkspace;

// One request to sentineld (status first, DESIGN.md §4); the reply, or one with "error".
Value request(const std::string& op, Object fields) {
  std::string path = gSocket;
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  if (path.size() >= sizeof addr.sun_path) return Value(Object{{"error", Object{{"message", "socket path too long"}}}});
  std::memcpy(addr.sun_path, path.c_str(), path.size() + 1);
  const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0 || ::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
    if (fd >= 0) ::close(fd);
    return Value(Object{{"error", Object{{"message", "no sentineld at " + path}}}});
  }
  fields["op"] = op;
  fields["id"] = 2;
  const std::string out = json::dump(Object{{"id", 1}, {"op", "status"}, {"api", 1}}) + "\n" + json::dump(fields) + "\n";
  (void)!::write(fd, out.data(), out.size());
  std::string buffer;
  char chunk[65536];
  for (;;) {
    for (size_t eol; (eol = buffer.find('\n')) != std::string::npos;) {
      Value reply = json::parse(buffer.substr(0, eol));
      buffer.erase(0, eol + 1);
      if (!reply["id"].isNull() && reply["id"].num() == 2) {
        ::close(fd);
        return reply;
      }
      if (!reply["error"].isNull()) {
        ::close(fd);
        return reply;
      }
    }
    const ssize_t n = ::read(fd, chunk, sizeof chunk);
    if (n <= 0) break;
    buffer.append(chunk, size_t(n));
  }
  ::close(fd);
  return Value(Object{{"error", Object{{"message", "sentineld closed the connection"}}}});
}

std::string errorOf(const Value& reply) { return reply["error"].isNull() ? "" : reply["error"]["message"].str(); }

// Mac virtual key codes (Carbon kVK_*) to Linux evdev codes, as the app maps DOM physical keys.
const std::map<unsigned short, uint16_t> kKeys = {
    {0x00, 30}, {0x01, 31}, {0x02, 32}, {0x03, 33}, {0x04, 35}, {0x05, 34}, {0x06, 44}, {0x07, 45}, {0x08, 46}, {0x09, 47},
    {0x0A, 86}, {0x0B, 48}, {0x0C, 16}, {0x0D, 17}, {0x0E, 18}, {0x0F, 19}, {0x10, 21}, {0x11, 20}, {0x12, 2},  {0x13, 3},
    {0x14, 4},  {0x15, 5},  {0x16, 7},  {0x17, 6},  {0x18, 13}, {0x19, 10}, {0x1A, 8},  {0x1B, 12}, {0x1C, 9},  {0x1D, 11},
    {0x1E, 27}, {0x1F, 24}, {0x20, 22}, {0x21, 26}, {0x22, 23}, {0x23, 25}, {0x24, 28}, {0x25, 38}, {0x26, 36}, {0x27, 40},
    {0x28, 37}, {0x29, 39}, {0x2A, 43}, {0x2B, 51}, {0x2C, 53}, {0x2D, 49}, {0x2E, 50}, {0x2F, 52}, {0x30, 15}, {0x31, 57},
    {0x32, 41}, {0x33, 14}, {0x35, 1},  {0x36, 126}, {0x37, 125}, {0x38, 42}, {0x39, 58}, {0x3A, 56}, {0x3B, 29}, {0x3C, 54},
    {0x3D, 100}, {0x3E, 97}, {0x41, 83}, {0x43, 55}, {0x45, 78}, {0x47, 69}, {0x4B, 98}, {0x4C, 96}, {0x4E, 74}, {0x52, 82},
    {0x53, 79}, {0x54, 80}, {0x55, 81}, {0x56, 75}, {0x57, 76}, {0x58, 77}, {0x59, 71}, {0x5B, 72}, {0x5C, 73}, {0x60, 63},
    {0x61, 64}, {0x62, 65}, {0x63, 61}, {0x64, 66}, {0x65, 67}, {0x67, 87}, {0x6D, 68}, {0x6F, 88}, {0x72, 110}, {0x73, 102},
    {0x74, 104}, {0x75, 111}, {0x76, 62}, {0x77, 107}, {0x78, 60}, {0x79, 109}, {0x7A, 59}, {0x7B, 105}, {0x7C, 106},
    {0x7D, 108}, {0x7E, 103}};

// Which device-dependent modifier bit (IOKit's NX_DEVICE*KEYMASK) a modifier key's state is in.
const std::map<unsigned short, NSUInteger> kModifierBits = {
    {0x38, 0x02}, {0x3C, 0x04}, {0x3B, 0x01}, {0x3E, 0x2000}, {0x3A, 0x20}, {0x3D, 0x40}, {0x37, 0x08}, {0x36, 0x10}};

std::vector<uint8_t> inputPacket(uint16_t device, const std::vector<std::array<int32_t, 3>>& events) {
  std::vector<uint8_t> out(4 + (events.size() + 1) * 8, 0);
  auto put16 = [&](size_t at, uint16_t v) { out[at] = uint8_t(v >> 8), out[at + 1] = uint8_t(v); };
  put16(0, device);
  put16(2, uint16_t(events.size() + 1));  // and the SYN_REPORT that ends the report
  for (size_t i = 0; i < events.size(); ++i) {
    const size_t at = 4 + i * 8;
    put16(at, uint16_t(events[i][0]));
    put16(at + 2, uint16_t(events[i][1]));
    const uint32_t v = uint32_t(events[i][2]);
    out[at + 4] = uint8_t(v >> 24), out[at + 5] = uint8_t(v >> 16), out[at + 6] = uint8_t(v >> 8), out[at + 7] = uint8_t(v);
  }
  return out;
}

}  // namespace

// The desktop: video, input, cursor and clipboard.
@interface DesktopView : NSView
@property(nonatomic) viewer::Transport* transport;
- (void)configure:(const uint8_t*)data size:(size_t)size;
- (void)frame:(const uint8_t*)data size:(size_t)size key:(BOOL)key timestamp:(uint64_t)timestamp;
- (void)cursor:(const uint8_t*)data size:(size_t)size;
- (void)audio:(const uint8_t*)data size:(size_t)size;
- (void)releaseAll;
@end

@implementation DesktopView {
  AVSampleBufferDisplayLayer* _layer;
  CMVideoFormatDescriptionRef _format;
  CGSize _video;
  NSCursor* _cursor;       // the guest's, scaled to the window
  std::vector<uint8_t> _cursorData;
  BOOL _cursorKnown;       // a cursor packet came (else the guest draws it in the frames)
  std::set<uint16_t> _keys, _buttons;
  NSTrackingArea* _tracking;
  BOOL _copying;           // Command-C/X sent; the guest's clipboard is read when Command goes up
  NSUInteger _modifiers;
  AVAudioEngine* _engine;
  AVAudioPlayerNode* _player;
  AVAudioConverter* _opus;
  AVAudioFormat* _pcm;
}

- (instancetype)initWithFrame:(NSRect)frame {
  if (!(self = [super initWithFrame:frame])) return nil;
  self.wantsLayer = YES;
  _layer = [AVSampleBufferDisplayLayer layer];
  _layer.videoGravity = AVLayerVideoGravityResizeAspect;
  _layer.backgroundColor = NSColor.blackColor.CGColor;
  self.layer = _layer;
  _video = CGSizeMake(1920, 1200);
  return self;
}

- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)acceptsFirstMouse:(NSEvent*)event { return YES; }

- (void)updateTrackingAreas {
  [super updateTrackingAreas];
  if (_tracking) [self removeTrackingArea:_tracking];
  _tracking = [[NSTrackingArea alloc] initWithRect:self.bounds
                                           options:NSTrackingMouseMoved | NSTrackingCursorUpdate | NSTrackingActiveInKeyWindow |
                                                   NSTrackingInVisibleRect | NSTrackingMouseEnteredAndExited
                                             owner:self
                                          userInfo:nil];
  [self addTrackingArea:_tracking];
}

// Where the desktop is drawn in the view (aspect fit), and how many points a guest pixel takes.
- (NSRect)videoRect {
  const NSRect b = self.bounds;
  const CGFloat scale = MIN(b.size.width / _video.width, b.size.height / _video.height);
  const CGFloat w = _video.width * scale, h = _video.height * scale;
  return NSMakeRect((b.size.width - w) / 2, (b.size.height - h) / 2, w, h);
}

// --- video ------------------------------------------------------------------------------------

- (void)configure:(const uint8_t*)data size:(size_t)size {
  if (size < 8) return;
  const uint32_t width = uint32_t(data[0]) << 24 | uint32_t(data[1]) << 16 | uint32_t(data[2]) << 8 | data[3];
  const uint32_t height = uint32_t(data[4]) << 24 | uint32_t(data[5]) << 16 | uint32_t(data[6]) << 8 | data[7];
  NSData* avcC = [NSData dataWithBytes:data + 8 length:size - 8];
  NSDictionary* extensions = @{(__bridge NSString*)kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms : @{@"avcC" : avcC}};
  CMVideoFormatDescriptionRef format = nullptr;
  if (CMVideoFormatDescriptionCreate(nullptr, kCMVideoCodecType_H264, int32_t(width), int32_t(height),
                                     (__bridge CFDictionaryRef)extensions, &format) != noErr)
    return;
  if (_format) CFRelease(_format);
  _format = format;
  if (_video.width != width || _video.height != height) {
    _video = CGSizeMake(width, height);
    [self.window setContentAspectRatio:_video];
    [self renderCursor];
  }
}

- (void)frame:(const uint8_t*)data size:(size_t)size key:(BOOL)key timestamp:(uint64_t)timestamp {
  if (!_format) return;
  AVSampleBufferVideoRenderer* renderer = _layer.sampleBufferRenderer;
  if (renderer.status == AVQueuedSampleBufferRenderingStatusFailed) {
    [renderer flush];
    if (!key) {
      _transport->send(4, {});  // decode from the next key frame
      return;
    }
  }
  CMBlockBufferRef block = nullptr;
  if (CMBlockBufferCreateWithMemoryBlock(nullptr, nullptr, size, nullptr, nullptr, 0, size, 0, &block) != noErr) return;
  CMBlockBufferReplaceDataBytes(data, block, 0, size);
  CMSampleTimingInfo timing{kCMTimeInvalid, CMTimeMake(int64_t(timestamp), 1000000), kCMTimeInvalid};
  CMSampleBufferRef sample = nullptr;
  const size_t sizes[] = {size};
  CMSampleBufferCreateReady(nullptr, block, _format, 1, 1, &timing, 1, sizes, &sample);
  CFRelease(block);
  if (!sample) return;
  CFMutableDictionaryRef attachments =
      (CFMutableDictionaryRef)CFArrayGetValueAtIndex(CMSampleBufferGetSampleAttachmentsArray(sample, YES), 0);
  CFDictionarySetValue(attachments, kCMSampleAttachmentKey_DisplayImmediately, kCFBooleanTrue);
  if (!key) CFDictionarySetValue(attachments, kCMSampleAttachmentKey_NotSync, kCFBooleanTrue);
  [renderer enqueueSampleBuffer:sample];
  CFRelease(sample);
}

// --- cursor -----------------------------------------------------------------------------------

- (void)cursor:(const uint8_t*)data size:(size_t)size {
  _cursorKnown = YES;
  _cursorData.assign(data, data + size);
  [self renderCursor];
}

// The guest's cursor at the size the desktop is drawn, as the Mac pointer: it moves with no round
// trip. Hidden, or before the guest sends one (a cursor drawn in the frames), the pointer is hidden.
- (void)renderCursor {
  static NSCursor* none = [[NSCursor alloc] initWithImage:[[NSImage alloc] initWithSize:NSMakeSize(1, 1)] hotSpot:NSZeroPoint];
  const uint8_t* p = _cursorData.data();
  const size_t n = _cursorData.size();
  if (!_cursorKnown || n < 8) {
    _cursor = none;
  } else {
    const int hx = p[0] << 8 | p[1], hy = p[2] << 8 | p[3], w = p[4] << 8 | p[5], h = p[6] << 8 | p[7];
    if (w <= 0 || h <= 0 || n < size_t(8 + w * h * 4)) {
      _cursor = none;
    } else {
      NSBitmapImageRep* rep = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:nullptr
                                                                      pixelsWide:w
                                                                      pixelsHigh:h
                                                                   bitsPerSample:8
                                                                 samplesPerPixel:4
                                                                        hasAlpha:YES
                                                                        isPlanar:NO
                                                                  colorSpaceName:NSDeviceRGBColorSpace
                                                                    bitmapFormat:NSBitmapFormatAlphaNonpremultiplied
                                                                     bytesPerRow:w * 4
                                                                    bitsPerPixel:32];
      std::memcpy(rep.bitmapData, p + 8, size_t(w * h * 4));
      const CGFloat scale = self.videoRect.size.width / _video.width;
      NSImage* image = [[NSImage alloc] initWithSize:NSMakeSize(w * scale, h * scale)];
      [image addRepresentation:rep];
      _cursor = [[NSCursor alloc] initWithImage:image hotSpot:NSMakePoint(hx * scale, hy * scale)];
    }
  }
  [self.window invalidateCursorRectsForView:self];
}

- (void)resetCursorRects {
  [self addCursorRect:self.videoRect cursor:_cursor ? _cursor : NSCursor.arrowCursor];
}

- (void)setFrameSize:(NSSize)size {
  [super setFrameSize:size];
  [self renderCursor];
}

// --- input ------------------------------------------------------------------------------------

- (void)input:(uint16_t)device events:(const std::vector<std::array<int32_t, 3>>&)events {
  _transport->send(3, inputPacket(device, events));
}

- (BOOL)position:(NSEvent*)event into:(std::vector<std::array<int32_t, 3>>&)events {
  const NSRect r = self.videoRect;
  const NSPoint p = [self convertPoint:event.locationInWindow fromView:nil];
  if (r.size.width <= 0 || r.size.height <= 0) return NO;
  const double x = (p.x - r.origin.x) / r.size.width, y = 1 - (p.y - r.origin.y) / r.size.height;
  events.push_back({3, 0, int32_t(lround(std::clamp(x, 0.0, 1.0) * 65535))});
  events.push_back({3, 1, int32_t(lround(std::clamp(y, 0.0, 1.0) * 65535))});
  return YES;
}

- (void)moved:(NSEvent*)event {
  std::vector<std::array<int32_t, 3>> events;
  if ([self position:event into:events]) [self input:1 events:events];
}
- (void)mouseMoved:(NSEvent*)event { [self moved:event]; }
- (void)mouseDragged:(NSEvent*)event { [self moved:event]; }
- (void)rightMouseDragged:(NSEvent*)event { [self moved:event]; }
- (void)otherMouseDragged:(NSEvent*)event { [self moved:event]; }

- (void)button:(NSEvent*)event down:(BOOL)down {
  static const uint16_t codes[] = {272, 273, 274, 275, 276};  // left, right, middle, side, extra
  if (event.buttonNumber < 0 || event.buttonNumber > 4) return;
  const uint16_t code = codes[event.buttonNumber];
  if (down) _buttons.insert(code);
  else _buttons.erase(code);
  std::vector<std::array<int32_t, 3>> events;
  [self position:event into:events];  // position and button in one report
  events.push_back({1, code, down ? 1 : 0});
  [self input:1 events:events];
}
- (void)mouseDown:(NSEvent*)event { [self button:event down:YES]; }
- (void)mouseUp:(NSEvent*)event { [self button:event down:NO]; }
- (void)rightMouseDown:(NSEvent*)event { [self button:event down:YES]; }
- (void)rightMouseUp:(NSEvent*)event { [self button:event down:NO]; }
- (void)otherMouseDown:(NSEvent*)event { [self button:event down:YES]; }
- (void)otherMouseUp:(NSEvent*)event { [self button:event down:NO]; }

- (void)scrollWheel:(NSEvent*)event {
  std::vector<std::array<int32_t, 3>> events;
  if (event.scrollingDeltaY != 0) events.push_back({2, 8, event.scrollingDeltaY > 0 ? 1 : -1});
  if (event.scrollingDeltaX != 0) events.push_back({2, 6, event.scrollingDeltaX > 0 ? -1 : 1});
  if (!events.empty()) [self input:0 events:events];
}

- (void)key:(uint16_t)code value:(int32_t)value {
  if (value) _keys.insert(code);
  else _keys.erase(code);
  [self input:2 events:{{1, code, value}}];
}

// Control (and Shift) with a letter, for the guest's copy and paste.
- (void)shortcut:(uint16_t)letter shift:(BOOL)shift {
  [self releaseAll];
  [self key:29 value:1];
  if (shift) [self key:42 value:1];
  [self key:letter value:1];
  [self key:letter value:0];
  if (shift) [self key:42 value:0];
  [self key:29 value:0];
}

- (void)keyDown:(NSEvent*)event {
  const NSEventModifierFlags mods = event.modifierFlags;
  const BOOL command = mods & NSEventModifierFlagCommand;
  // Command-C/X/V: the workspace's clipboard, as in the app.
  if (command && !(mods & NSEventModifierFlagOption)) {
    const unsigned short k = event.keyCode;
    if (k == 0x08 || k == 0x07) {  // C, X
      [self shortcut:(k == 0x08 ? 46 : 45) shift:(mods & NSEventModifierFlagShift) != 0];
      _copying = YES;
      return;
    }
    if (k == 0x09) {  // V
      [self paste:(mods & NSEventModifierFlagShift) != 0];
      return;
    }
  }
  auto it = kKeys.find(event.keyCode);
  if (it != kKeys.end()) [self key:it->second value:event.isARepeat ? 2 : 1];
}

- (void)keyUp:(NSEvent*)event {
  auto it = kKeys.find(event.keyCode);
  if (it != kKeys.end() && _keys.count(it->second)) [self key:it->second value:0];
}

- (void)flagsChanged:(NSEvent*)event {
  const NSUInteger flags = event.modifierFlags;
  const unsigned short k = event.keyCode;
  if (k == 0x39) {  // Caps Lock: a press and a release per change
    [self key:58 value:1];
    [self key:58 value:0];
    return;
  }
  auto bit = kModifierBits.find(k);
  auto code = kKeys.find(k);
  if (bit == kModifierBits.end() || code == kKeys.end()) return;
  const BOOL down = (flags & bit->second) != 0;
  if (down != (_keys.count(code->second) != 0)) [self key:code->second value:down ? 1 : 0];
  // Command up after a copy: the guest's clipboard, now holding the selection, to the Mac's.
  if (!(flags & NSEventModifierFlagCommand) && _copying) {
    _copying = NO;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 150 * NSEC_PER_MSEC), dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
      Value reply = request("clipboard", Object{{"workspace", gWorkspace}, {"action", "read"}});
      if (!errorOf(reply).empty()) return NSLog(@"clipboard: %s", errorOf(reply).c_str());
      NSString* text = [NSString stringWithUTF8String:reply["text"].str().c_str()];
      dispatch_async(dispatch_get_main_queue(), ^{
        [NSPasteboard.generalPasteboard clearContents];
        [NSPasteboard.generalPasteboard setString:text forType:NSPasteboardTypeString];
      });
    });
  }
}

- (void)paste:(BOOL)shift {
  NSString* text = [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
  const std::string utf8 = text ? text.UTF8String : "";
  if (utf8.size() > 262144) return NSLog(@"clipboard: too large to paste");
  [self releaseAll];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
    Value reply = request("clipboard", Object{{"workspace", gWorkspace}, {"action", "write"}, {"text", utf8}});
    if (!errorOf(reply).empty()) return NSLog(@"clipboard: %s", errorOf(reply).c_str());
    dispatch_async(dispatch_get_main_queue(), ^{ [self shortcut:47 shift:shift]; });
  });
}

- (void)releaseAll {
  for (uint16_t code : std::set<uint16_t>(_keys)) [self key:code value:0];
  for (uint16_t code : std::set<uint16_t>(_buttons)) [self input:1 events:{{1, code, 0}}];
  _buttons.clear();
}

// --- audio ------------------------------------------------------------------------------------

// One Opus packet (48 kHz stereo, 10 ms), decoded by macOS and played at once.
- (void)audio:(const uint8_t*)data size:(size_t)size {
  if (!_engine) {
    AudioStreamBasicDescription opus{.mSampleRate = 48000, .mFormatID = kAudioFormatOpus, .mFramesPerPacket = 480, .mChannelsPerFrame = 2};
    AVAudioFormat* input = [[AVAudioFormat alloc] initWithStreamDescription:&opus];
    _pcm = [[AVAudioFormat alloc] initWithCommonFormat:AVAudioPCMFormatFloat32 sampleRate:48000 channels:2 interleaved:NO];
    _opus = [[AVAudioConverter alloc] initFromFormat:input toFormat:_pcm];
    _engine = [[AVAudioEngine alloc] init];
    _player = [[AVAudioPlayerNode alloc] init];
    [_engine attachNode:_player];
    [_engine connect:_player to:_engine.mainMixerNode format:_pcm];
    NSError* error = nil;
    if (!_opus || ![_engine startAndReturnError:&error]) {
      NSLog(@"audio: %@", error ? error.localizedDescription : @"no Opus decoder");
      _opus = nil;
      return;
    }
    [_player play];
  }
  if (!_opus) return;
  AVAudioCompressedBuffer* packet = [[AVAudioCompressedBuffer alloc] initWithFormat:_opus.inputFormat packetCapacity:1 maximumPacketSize:size];
  std::memcpy(packet.data, data, size);
  packet.byteLength = uint32_t(size);
  packet.packetCount = 1;
  packet.packetDescriptions[0] = {0, 0, uint32_t(size)};
  AVAudioPCMBuffer* pcm = [[AVAudioPCMBuffer alloc] initWithPCMFormat:_pcm frameCapacity:960];
  __block BOOL given = NO;
  NSError* error = nil;
  [_opus convertToBuffer:pcm error:&error withInputFromBlock:^AVAudioBuffer*(AVAudioPacketCount, AVAudioConverterInputStatus* status) {
    if (given) {
      *status = AVAudioConverterInputStatus_NoDataNow;
      return nil;
    }
    given = YES;
    *status = AVAudioConverterInputStatus_HaveData;
    return packet;
  }];
  if (pcm.frameLength) [_player scheduleBuffer:pcm completionHandler:nil];
}

@end

@interface Delegate : NSObject <NSApplicationDelegate, NSWindowDelegate>
@end

@implementation Delegate {
  NSWindow* _window;
  DesktopView* _view;
  viewer::Transport _transport;
}

- (void)applicationDidFinishLaunching:(NSNotification*)note {
  Value ticket = request("display", Object{{"workspace", gWorkspace}});
  if (!errorOf(ticket).empty()) {
    fprintf(stderr, "sentineld view: %s\n", errorOf(ticket).c_str());
    exit(1);
  }
  _window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1280, 800)
                                        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable |
                                                  NSWindowStyleMaskMiniaturizable
                                          backing:NSBackingStoreBuffered
                                            defer:NO];
  _window.title = [NSString stringWithFormat:@"%s — sentineld", gWorkspace.c_str()];
  _window.delegate = self;
  _window.contentAspectRatio = NSMakeSize(1920, 1200);
  _view = [[DesktopView alloc] initWithFrame:_window.contentView.bounds];
  _view.transport = &_transport;
  _window.contentView = _view;
  [_window center];
  [_window makeKeyAndOrderFront:nil];
  [_window makeFirstResponder:_view];
  [NSApp activateIgnoringOtherApps:YES];

  DesktopView* view = _view;
  viewer::Transport* transport = &_transport;
  Delegate* delegate = self;
  _transport.onReady = [transport, delegate] {
    transport->send(4, {});
    dispatch_async(dispatch_get_main_queue(), ^{ [delegate sendRefresh]; });
  };
  _transport.onPacket = [view](uint8_t type, uint8_t flags, uint64_t timestamp, const uint8_t* data, size_t size) {
    // Payloads live only during the call: copy what goes to the main thread.
    NSData* payload = [NSData dataWithBytes:data length:size];
    dispatch_async(dispatch_get_main_queue(), ^{
      const auto* p = static_cast<const uint8_t*>(payload.bytes);
      switch (type) {
        case 1: [view configure:p size:payload.length]; break;
        case 2: [view frame:p size:payload.length key:(flags & 1) != 0 timestamp:timestamp]; break;
        case 5: [view audio:p size:payload.length]; break;
        case 6: [view cursor:p size:payload.length]; break;
      }
    });
  };
  _transport.onClosed = [](const std::string& why) {
    fprintf(stderr, "sentineld view: %s\n", why.c_str());
    dispatch_async(dispatch_get_main_queue(), ^{ [NSApp terminate:nil]; });
  };
  _transport.start(int(ticket["port"].num()), ticket["cert_sha256"].str(), ticket["ticket"].str());
}

- (void)windowDidResignKey:(NSNotification*)note { [_view releaseAll]; }

// The screen the window is on: the workspace's display follows its refresh rate (§6 refresh).
- (void)sendRefresh {
  const NSInteger hz = (_window.screen ? _window.screen : NSScreen.mainScreen).maximumFramesPerSecond;
  if (hz < 24 || hz > 240) return;
  _transport.send(7, {uint8_t(hz >> 8), uint8_t(hz)});
}

- (void)windowDidChangeScreen:(NSNotification*)note { [self sendRefresh]; }
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)app { return YES; }
- (void)applicationWillTerminate:(NSNotification*)note { _transport.stop(); }

@end

int sentineld::view(int argc, char** argv) {
  for (int i = 2; i < argc; ++i) {
    const std::string a = argv[i];
    if (a == "--state" && i + 1 < argc) gSocket = std::string(argv[++i]) + "/sentineld.sock";
    else if (a == "--socket" && i + 1 < argc) gSocket = argv[++i];
    else if (gWorkspace.empty() && a[0] != '-') gWorkspace = a;
    else gSocket.clear(), i = argc;
  }
  if (gSocket.empty() || gWorkspace.empty()) {
    fprintf(stderr, "usage: %s view (--state DIR | --socket PATH) WORKSPACE\n", argv[0]);
    return 2;
  }
  @autoreleasepool {
    NSApplication* app = NSApplication.sharedApplication;
    app.activationPolicy = NSApplicationActivationPolicyRegular;
    NSMenu* bar = [[NSMenu alloc] init];
    NSMenuItem* item = [[NSMenuItem alloc] init];
    [bar addItem:item];
    NSMenu* menu = [[NSMenu alloc] init];
    [menu addItemWithTitle:@"Quit" action:@selector(terminate:) keyEquivalent:@"q"];
    item.submenu = menu;
    app.mainMenu = bar;
    Delegate* delegate = [[Delegate alloc] init];
    app.delegate = delegate;
    [app run];
  }
  return 0;
}
