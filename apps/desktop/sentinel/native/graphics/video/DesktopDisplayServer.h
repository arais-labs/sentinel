#import <Foundation/Foundation.h>
#import <CoreVideo/CoreVideo.h>

typedef struct { uint16_t type, code; int32_t value; } DesktopInputEvent;
typedef BOOL (^DesktopInput)(uint16_t device, const DesktopInputEvent *events, uint16_t count);

// The worker owns the display. Viewers only attach to private, bounded sockets.
@interface DesktopDisplayServer : NSObject
- (instancetype)initWithVideoPath:(NSString *)videoPath controlPath:(NSString *)controlPath
                           input:(DesktopInput)input keyframe:(void (^)(void))keyframe;
- (instancetype)initWithVideoPath:(NSString *)videoPath controlPath:(NSString *)controlPath
                           input:(DesktopInput)input keyframe:(void (^)(void))keyframe
                   ownedEndpoint:(void (^)(NSDictionary<NSString *, NSString *> *))ownedEndpoint;
- (void)publish:(NSData *)packet;
- (void)setFrame:(CVPixelBufferRef)frame;
- (void)close;
@end
