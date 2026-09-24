#import <Foundation/Foundation.h>
#import <CoreVideo/CoreVideo.h>

// Framed H.264 bytes. The runtime supplies the transport; the encoder does not
// know whether the viewer is on this Mac or reached through SSH.
typedef void (^DesktopVideoOutput)(NSData *packet);
@interface DesktopVideoEncoder : NSObject
// Configure before encoding; invoked from completion/submission queues.
@property(nonatomic, copy) void (^capacityAvailable)(void);
- (instancetype)initWithWidth:(int)width height:(int)height fps:(int)fps
                      bitrate:(int)bitrate output:(DesktopVideoOutput)output;
- (BOOL)canAcceptFrame;
- (void)encode:(CVPixelBufferRef)buffer;
- (void)requestKeyframe;
- (void)close;
@end
