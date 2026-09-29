#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <CoreVideo/CoreVideo.h>

typedef void (^MetalFrameCopyCompletion)(BOOL success);

// Immutable, bounded guest cursor upload. No texture sampling or scaling is
// needed: virtio cursor pixels map one-to-one to output pixels.
@interface MetalCursorImage : NSObject
@property(nonatomic, readonly) id<MTLBuffer> buffer;
@property(nonatomic, readonly) NSUInteger width;
@property(nonatomic, readonly) NSUInteger height;
- (instancetype)initWithDevice:(id<MTLDevice>)device width:(NSUInteger)width height:(NSUInteger)height
                        pixels:(const void *)pixels bgra:(BOOL)bgra;
@end

@interface MetalFrameCopy : NSObject
// Configure before submitting copies. Called after a completion releases its
// slot/resources; may only schedule work, not use the producer's GL context.
@property(nonatomic, copy) void (^capacityAvailable)(void);
- (instancetype)initWithDevice:(id<MTLDevice>)device error:(NSError **)error;
// At most three copies may be in flight. NO means the request was rejected;
// its completion is never called. An accepted request retains its resources and
// calls completion exactly once after GPU completion, including GPU failures.
// A rejected request sets error for permanent failures; nil means backpressure.
// The source must remain unchanged until completion. A nonzero readyValue
// orders the copy after the supplied shared timeline event reaches that value.
- (BOOL)copyTexture:(id<MTLTexture>)source
         readyEvent:(id<MTLSharedEvent>)event
         readyValue:(uint64_t)value
           toBuffer:(CVPixelBufferRef)buffer
          flipY:(BOOL)flipY
          error:(NSError **)error
         completion:(MetalFrameCopyCompletion)completion;
// Guest cursor pixels are premultiplied RGBA/BGRA, top row first. Position is
// the image's top-left in output pixels (hotspot already subtracted). The image
// is retained through GPU completion.
- (BOOL)copyTexture:(id<MTLTexture>)source
         readyEvent:(id<MTLSharedEvent>)event readyValue:(uint64_t)value
           toBuffer:(CVPixelBufferRef)buffer flipY:(BOOL)flipY
             cursor:(MetalCursorImage *)cursor x:(int64_t)x y:(int64_t)y
              error:(NSError **)error completion:(MetalFrameCopyCompletion)completion;
// Call outside completion handlers and while producers are quiescent.
- (void)drain;
@end
