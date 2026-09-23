#import "MetalFrameCopy.h"
#include <stdatomic.h>

static BOOL rejectCopy(NSError **error, NSInteger code, NSString *reason) {
    if(error)*error=[NSError errorWithDomain:@"SentinelMetalFrameCopy" code:code
        userInfo:@{NSLocalizedDescriptionKey:reason}];
    return NO;
}

@implementation MetalCursorImage
- (instancetype)initWithDevice:(id<MTLDevice>)device width:(NSUInteger)width height:(NSUInteger)height
                        pixels:(const void *)pixels bgra:(BOOL)bgra {
    if(!(self=[super init]))return nil;
    if(!pixels||!width||!height||width>128||height>128)return nil;
    _width=width;_height=height;
    _buffer=[device newBufferWithLength:width*height*4 options:MTLResourceStorageModeShared];
    if(!_buffer)return nil;
    const uint8_t *source=pixels;uint8_t *target=_buffer.contents;
    for(NSUInteger i=0;i<width*height;i++) {
        target[4*i]=source[4*i+(bgra?2:0)];
        target[4*i+1]=source[4*i+1];
        target[4*i+2]=source[4*i+(bgra?0:2)];
        target[4*i+3]=source[4*i+3];
    }
    return self;
}
@end

@implementation MetalFrameCopy {
    id<MTLDevice> _device;
    id<MTLCommandQueue> _queue;
    id<MTLRenderPipelineState> _pipeline;
    id<MTLRenderPipelineState> _arrayPipeline;
    id<MTLBuffer> _transparentCursor;
    CVMetalTextureCacheRef _cache;
    dispatch_group_t _pending;
    atomic_uint _inFlight;
}

- (instancetype)initWithDevice:(id<MTLDevice>)device error:(NSError **)error {
    if (!(self=[super init])) return nil;
    _device=device;
    _queue=[device newCommandQueueWithMaxCommandBufferCount:3];
    _pending=dispatch_group_create();
    atomic_init(&_inFlight,0);
    if (!_queue) {
        if(error)*error=[NSError errorWithDomain:@"SentinelMetalFrameCopy" code:1
            userInfo:@{NSLocalizedDescriptionKey:@"Cannot create the Metal frame-copy command queue"}];
        return nil;
    }
    CVReturn cacheResult=CVMetalTextureCacheCreate(NULL,NULL,device,NULL,&_cache);
    if(cacheResult!=kCVReturnSuccess) {
        if(error)*error=[NSError errorWithDomain:@"SentinelMetalFrameCopy" code:cacheResult
            userInfo:@{NSLocalizedDescriptionKey:@"Cannot create the Metal video texture cache"}];
        return nil;
    }
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
       "struct V { float4 position [[position]]; float2 uv; };\n"
       "vertex V frame_vertex(uint i [[vertex_id]], constant uint &flip [[buffer(0)]]) {\n"
       " float2 p=float2((i<<1)&2,i&2); V v; v.position=float4(p*2-1,0,1);\n"
       " v.uv=float2(p.x,flip?p.y:1-p.y); return v; }\n"
       "float4 composite_cursor(float4 base,float2 position,device const uchar4 *cursor,constant int4 &rect) {\n"
       " int2 p=int2(position)-rect.xy; if(any(p<0)||any(p>=rect.zw)) return base;\n"
       " float4 c=float4(cursor[p.y*rect.z+p.x])/255.0; return c+base*(1-c.a); }\n"
       "fragment float4 frame_fragment(V v [[stage_in]],texture2d<float> image [[texture(0)]],\n"
       " device const uchar4 *cursor [[buffer(1)]],constant int4 &rect [[buffer(0)]]) {\n"
       " constexpr sampler s(coord::normalized,address::clamp_to_edge,filter::linear);\n"
       " return composite_cursor(image.sample(s,v.uv),v.position.xy,cursor,rect); }\n"
       "fragment float4 frame_array_fragment(V v [[stage_in]],texture2d_array<float> image [[texture(0)]],\n"
       " device const uchar4 *cursor [[buffer(1)]],constant int4 &rect [[buffer(0)]]) {\n"
       " constexpr sampler s(coord::normalized,address::clamp_to_edge,filter::linear);\n"
       " return composite_cursor(image.sample(s,v.uv,0),v.position.xy,cursor,rect); }\n";
    id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:error];
    if (!library) return nil;
    MTLRenderPipelineDescriptor *descriptor=[MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction=[library newFunctionWithName:@"frame_vertex"];
    descriptor.fragmentFunction=[library newFunctionWithName:@"frame_fragment"];
    descriptor.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
    _pipeline=[device newRenderPipelineStateWithDescriptor:descriptor error:error];
    if(!_pipeline)return nil;
    descriptor.fragmentFunction=[library newFunctionWithName:@"frame_array_fragment"];
    _arrayPipeline=[device newRenderPipelineStateWithDescriptor:descriptor error:error];
    if(!_arrayPipeline)return nil;
    uint32_t zero=0;
    _transparentCursor=[device newBufferWithBytes:&zero length:sizeof(zero) options:MTLResourceStorageModeShared];
    return _transparentCursor?self:nil;
}

- (BOOL)copyTexture:(id<MTLTexture>)source readyEvent:(id<MTLSharedEvent>)event
         readyValue:(uint64_t)value toBuffer:(CVPixelBufferRef)buffer flipY:(BOOL)flipY
         error:(NSError **)error
         completion:(MetalFrameCopyCompletion)completion {
    return [self copyTexture:source readyEvent:event readyValue:value toBuffer:buffer
        flipY:flipY cursor:nil x:0 y:0 error:error completion:completion];
}

- (BOOL)copyTexture:(id<MTLTexture>)source readyEvent:(id<MTLSharedEvent>)event
         readyValue:(uint64_t)value toBuffer:(CVPixelBufferRef)buffer flipY:(BOOL)flipY
         cursor:(MetalCursorImage *)cursor x:(int64_t)x y:(int64_t)y
         error:(NSError **)error completion:(MetalFrameCopyCompletion)completion {
    if(error)*error=nil;
    if (!source || source.device!=_device || !buffer || !completion ||
        (source.textureType!=MTLTextureType2D &&
         !(source.textureType==MTLTextureType2DArray && source.arrayLength==1)) ||
        source.sampleCount!=1 ||
        source.framebufferOnly ||
        (source.usage!=MTLTextureUsageUnknown && !(source.usage&MTLTextureUsageShaderRead)) ||
        (source.pixelFormat!=MTLPixelFormatRGBA8Unorm &&
         source.pixelFormat!=MTLPixelFormatBGRA8Unorm) ||
        CVPixelBufferGetPixelFormatType(buffer)!=kCVPixelFormatType_32BGRA ||
        (value && !event))
        return rejectCopy(error,2,@"Invalid Metal frame-copy texture, buffer or timeline input");
    if(cursor && cursor.buffer.device!=_device)
        return rejectCopy(error,6,@"Guest cursor belongs to another Metal device");
    // MTLSharedEvent is device-independent: its device property is nil in
    // production, though Metal validation wrappers may supply a device.
    unsigned count=atomic_load(&_inFlight);
    do { if(count>=3) return NO; }
    while(!atomic_compare_exchange_weak(&_inFlight,&count,count+1));
    CVMetalTextureRef targetRef=NULL;
    CVReturn result=CVMetalTextureCacheCreateTextureFromImage(NULL,_cache,buffer,NULL,
        MTLPixelFormatBGRA8Unorm,CVPixelBufferGetWidth(buffer),CVPixelBufferGetHeight(buffer),0,&targetRef);
    if(result || !targetRef){atomic_fetch_sub(&_inFlight,1);
        return rejectCopy(error,result?:3,@"Cannot create the Metal frame-copy destination texture");}
    id<MTLTexture> target=CVMetalTextureGetTexture(targetRef);
    id<MTLCommandBuffer> command=[_queue commandBuffer];
    if(!target||!command){CFRelease(targetRef);atomic_fetch_sub(&_inFlight,1);
        return rejectCopy(error,4,@"Cannot create the Metal frame-copy command buffer");}
    if(value) [command encodeWaitForEvent:event value:value];
    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=target;
    pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
    if(!encoder){CFRelease(targetRef);atomic_fetch_sub(&_inFlight,1);
        return rejectCopy(error,5,@"Cannot create the Metal frame-copy render encoder");}
    uint32_t flip=flipY;
    [encoder setRenderPipelineState:source.textureType==MTLTextureType2DArray?_arrayPipeline:_pipeline];
    [encoder setVertexBytes:&flip length:sizeof(flip) atIndex:0];
    [encoder setFragmentTexture:source atIndex:0];
    int32_t rect[4]={0};
    if(cursor && x<(int64_t)target.width && y<(int64_t)target.height &&
       x+(int64_t)cursor.width>0 && y+(int64_t)cursor.height>0) {
        rect[0]=(int32_t)x;rect[1]=(int32_t)y;
        rect[2]=(int32_t)cursor.width;rect[3]=(int32_t)cursor.height;
    }
    [encoder setFragmentBuffer:cursor?cursor.buffer:_transparentCursor offset:0 atIndex:1];
    [encoder setFragmentBytes:rect length:sizeof(rect) atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [encoder endEncoding];
    CVPixelBufferRetain(buffer);
    dispatch_group_enter(_pending);
    [command addCompletedHandler:^(id<MTLCommandBuffer> completed){
        @autoreleasepool {
            @try {
                completion(completed.status==MTLCommandBufferStatusCompleted);
            } @finally {
                CFRelease(targetRef);CVPixelBufferRelease(buffer);
                atomic_fetch_sub(&self->_inFlight,1);
                if(self.capacityAvailable)self.capacityAvailable();
                dispatch_group_leave(self->_pending);
            }
        }
    }];
    [command commit];
    return YES;
}

- (void)drain {dispatch_group_wait(_pending,DISPATCH_TIME_FOREVER);}
- (void)dealloc {if(_cache)CFRelease(_cache);}
@end
