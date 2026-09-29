#import "MetalFrameCopy.h"
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define REQUIRE(x) do {if(!(x)){fprintf(stderr,"FAIL %s at %d\n",#x,__LINE__);abort();}}while(0)

static CVPixelBufferRef make_buffer(size_t width,size_t height,OSType format)
{
   NSDictionary *attributes=@{(__bridge NSString*)kCVPixelBufferMetalCompatibilityKey:@YES,
                              (__bridge NSString*)kCVPixelBufferIOSurfacePropertiesKey:@{}};
   CVPixelBufferRef buffer=NULL;
   REQUIRE(CVPixelBufferCreate(NULL,width,height,format,(__bridge CFDictionaryRef)attributes,&buffer)==kCVReturnSuccess);
   return buffer;
}

static unsigned component(unsigned x,unsigned y,unsigned c)
{
   return c==0?x*31:c==1?y*43:c==2?(x+y)*17:255;
}

static id<MTLTexture> make_source_type(id<MTLDevice> device,BOOL bgra,BOOL array)
{
   MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:bgra?MTLPixelFormatBGRA8Unorm:MTLPixelFormatRGBA8Unorm width:8 height:6 mipmapped:NO];
   d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
   if(array){d.textureType=MTLTextureType2DArray;d.arrayLength=1;}
   id<MTLTexture> texture=[device newTextureWithDescriptor:d];REQUIRE(texture);
   uint8_t bytes[8*6*4];
   for(unsigned y=0;y<6;++y)for(unsigned x=0;x<8;++x)for(unsigned c=0;c<4;++c)
      bytes[(y*8+x)*4+(bgra&&c!=1&&c!=3?2-c:c)]=component(x,y,c);
   [texture replaceRegion:MTLRegionMake2D(0,0,8,6) mipmapLevel:0 slice:0 withBytes:bytes bytesPerRow:32 bytesPerImage:sizeof(bytes)];
   return texture;
}

static id<MTLTexture> make_source(id<MTLDevice> device,BOOL bgra)
{
   return make_source_type(device,bgra,NO);
}

static unsigned sample(double x,double y,unsigned c)
{
   int x0=(int)floor(x),y0=(int)floor(y),x1=x0+1,y1=y0+1;
   double tx=x-x0,ty=y-y0;
   x0=MAX(0,MIN(7,x0));x1=MAX(0,MIN(7,x1));y0=MAX(0,MIN(5,y0));y1=MAX(0,MIN(5,y1));
   double a=component(x0,y0,c)*(1-tx)+component(x1,y0,c)*tx;
   double b=component(x0,y1,c)*(1-tx)+component(x1,y1,c)*tx;
   return (unsigned)lround(a*(1-ty)+b*ty);
}

static BOOL check_buffer(CVPixelBufferRef buffer,BOOL flip)
{
   if(CVPixelBufferLockBaseAddress(buffer,kCVPixelBufferLock_ReadOnly)!=kCVReturnSuccess)return NO;
   size_t width=CVPixelBufferGetWidth(buffer),height=CVPixelBufferGetHeight(buffer),row=CVPixelBufferGetBytesPerRow(buffer);
   const uint8_t *data=CVPixelBufferGetBaseAddress(buffer);
   BOOL good=YES;
   for(size_t y=0;y<height&&good;++y)for(size_t x=0;x<width&&good;++x) {
      double sx=(x+.5)*8/width-.5,sy=(y+.5)*6/height-.5;if(flip)sy=5-sy;
      for(unsigned c=0;c<4;++c) {
         unsigned expected=sample(sx,sy,c),actual=data[y*row+x*4+(c==0?2:c==2?0:c)];
         if(abs((int)actual-(int)expected)>1) {
            fprintf(stderr,"pixel mismatch size=%zux%zu flip=%d xy=%zu,%zu component=%u actual=%u expected=%u\n",width,height,flip,x,y,c,actual,expected);
            good=NO;break;
         }
      }
   }
   CVPixelBufferUnlockBaseAddress(buffer,kCVPixelBufferLock_ReadOnly);
   return good;
}

int main(void)
{
   @autoreleasepool {
      alarm(30);
      id<MTLDevice> device=MTLCreateSystemDefaultDevice();
      if(!device){fputs("Metal device unavailable\n",stderr);return 77;}
      NSError *error=nil;MetalFrameCopy *copier=[[MetalFrameCopy alloc]initWithDevice:device error:&error];REQUIRE(copier);
      uint32_t opaque=0xffffffff;
      REQUIRE(![[MetalCursorImage alloc]initWithDevice:device width:129 height:1 pixels:&opaque bgra:NO]);
      REQUIRE(![[MetalCursorImage alloc]initWithDevice:device width:1 height:0 pixels:&opaque bgra:NO]);
      REQUIRE(![[MetalCursorImage alloc]initWithDevice:device width:1 height:1 pixels:NULL bgra:NO]);
      const unsigned sizes[4][2]={{8,6},{17,11},{4,3},{1920,1200}};
      unsigned cases=0;
      for(unsigned array=0;array<2;++array)for(unsigned bgra=0;bgra<2;++bgra)for(unsigned flip=0;flip<2;++flip)for(unsigned s=0;s<4;++s) {
         @autoreleasepool {
            CVPixelBufferRef output=make_buffer(sizes[s][0],sizes[s][1],kCVPixelFormatType_32BGRA);
            __block BOOL passed=NO;
            REQUIRE([copier copyTexture:make_source_type(device,bgra,array) readyEvent:nil readyValue:0 toBuffer:output flipY:flip error:NULL completion:^(BOOL success){passed=success&&check_buffer(output,flip);}]);
            CVPixelBufferRelease(output);
            [copier drain];REQUIRE(passed);cases++;
         }
      }
      printf("PASS %u pixel cases: native 2D/one-slice array, RGBA/BGRA, both orientations, same/up/down scaling, full 1920x1200 output, every pixel checked\n",cases);
      // Cursor composition uses output-pixel coordinates, independently of
      // the primary's format, scaling or flip. Check every pixel including
      // transparent/half-alpha texels and negative/edge clipping.
      for(unsigned bgra=0;bgra<2;bgra++)for(int offset=-1;offset<8;offset+=4) {
         uint8_t rgba[]={255,0,0,255, 0,128,0,128, 0,0,255,255, 0,0,0,0},data[16];
         for(unsigned p=0;p<4;p++)for(unsigned c=0;c<4;c++)data[p*4+c]=rgba[p*4+(bgra&&c!=1&&c!=3?2-c:c)];
         MetalCursorImage *cursor=[[MetalCursorImage alloc] initWithDevice:device width:2 height:2 pixels:data bgra:bgra];REQUIRE(cursor);
         memset(data,0,sizeof(data)); // The upload owns a snapshot of guest bytes.
         CVPixelBufferRef output=make_buffer(8,6,kCVPixelFormatType_32BGRA);
         __block BOOL passed=NO;
         REQUIRE([copier copyTexture:make_source_type(device,NO,YES) readyEvent:nil readyValue:0 toBuffer:output
            flipY:NO cursor:cursor x:offset y:offset error:NULL completion:^(BOOL success){passed=success;}]);
         cursor=nil; // The accepted command must retain its upload until completion.
         [copier drain];REQUIRE(passed);
         REQUIRE(CVPixelBufferLockBaseAddress(output,kCVPixelBufferLock_ReadOnly)==kCVReturnSuccess);
         const uint8_t *bytes=CVPixelBufferGetBaseAddress(output);size_t row=CVPixelBufferGetBytesPerRow(output);
         for(int y=0;y<6;y++)for(int x=0;x<8;x++)for(unsigned c=0;c<4;c++) {
            unsigned expected=component(x,y,c);
            if(x>=offset&&x<offset+2&&y>=offset&&y<offset+2) {
               const uint8_t *p=rgba+((y-offset)*2+x-offset)*4;
               expected=p[c]+(unsigned)lround(expected*(255-p[3])/255.0);
            }
            unsigned actual=bytes[y*row+x*4+(c==0?2:c==2?0:c)];
            REQUIRE(abs((int)actual-(int)expected)<=1);
         }
         CVPixelBufferUnlockBaseAddress(output,kCVPixelBufferLock_ReadOnly);CVPixelBufferRelease(output);
      }
      {
         uint8_t data[]={0,0,255,255, 0,128,0,128, 0,0,0,0, 255,0,0,255, 64,0,64,128, 0,255,255,255};
         MetalCursorImage *cursor=[[MetalCursorImage alloc] initWithDevice:device width:3 height:2 pixels:data bgra:YES];REQUIRE(cursor);
         CVPixelBufferRef output=make_buffer(640,360,kCVPixelFormatType_32BGRA);
         REQUIRE([copier copyTexture:make_source_type(device,NO,YES) readyEvent:nil readyValue:0 toBuffer:output
            flipY:NO cursor:cursor x:49 y:59 error:NULL completion:^(BOOL success){REQUIRE(success);}]);
         [copier drain];CVPixelBufferLockBaseAddress(output,kCVPixelBufferLock_ReadOnly);
         const uint8_t *pixel=(const uint8_t*)CVPixelBufferGetBaseAddress(output)+59*CVPixelBufferGetBytesPerRow(output)+49*4;
         REQUIRE(pixel[0]==0&&pixel[1]==0&&pixel[2]==255&&pixel[3]==255);
         CVPixelBufferUnlockBaseAddress(output,kCVPixelBufferLock_ReadOnly);CVPixelBufferRelease(output);
      }
      puts("PASS cursor pixel composition, premultiplied alpha, RGBA/BGRA and clipped edges");
      id<MTLSharedEvent> gate=[device newSharedEvent];REQUIRE(gate);
      printf("Shared timeline device: %s\n",gate.device?"validation wrapper":"device-independent (nil)");
      __block atomic_uint callbacks,failures;atomic_init(&callbacks,0);atomic_init(&failures,0);
      for(unsigned i=0;i<3;++i) {
         @autoreleasepool {
            CVPixelBufferRef output=make_buffer(17,11,kCVPixelFormatType_32BGRA);
            REQUIRE([copier copyTexture:make_source(device,i%2) readyEvent:gate readyValue:1 toBuffer:output flipY:i%2 error:NULL completion:^(BOOL success){if(!success||!check_buffer(output,i%2))atomic_fetch_add(&failures,1);atomic_fetch_add(&callbacks,1);}]);
            CVPixelBufferRelease(output);
         }
      }
      CVPixelBufferRef fourth=make_buffer(8,6,kCVPixelFormatType_32BGRA);
      NSError *rejection=[NSError errorWithDomain:@"test" code:1 userInfo:nil];
      REQUIRE(![copier copyTexture:make_source(device,NO) readyEvent:gate readyValue:1 toBuffer:fourth flipY:NO error:&rejection completion:^(BOOL success){(void)success;atomic_fetch_add(&failures,1);}]);
      REQUIRE(!rejection);
      REQUIRE(atomic_load(&callbacks)==0);
      gate.signaledValue=1;[copier drain];REQUIRE(atomic_load(&callbacks)==3&&atomic_load(&failures)==0);
      __block BOOL reused=NO;
      REQUIRE([copier copyTexture:make_source(device,NO) readyEvent:gate readyValue:1 toBuffer:fourth flipY:NO error:NULL completion:^(BOOL success){reused=success&&check_buffer(fourth,NO);}]);
      [copier drain];REQUIRE(reused);CVPixelBufferRelease(fourth);
      printf("PASS exact three-inflight bound with unsignaled event, rejected fourth call, released capacity, retained source/buffer lifetime; no sleeps\n");
      id<MTLTexture> source=make_source(device,NO);CVPixelBufferRef output=make_buffer(8,6,kCVPixelFormatType_32BGRA);
      MetalFrameCopyCompletion completion=^(BOOL success){(void)success;atomic_fetch_add(&failures,1);};
      REQUIRE(![copier copyTexture:nil readyEvent:nil readyValue:0 toBuffer:output flipY:NO error:&rejection completion:completion]);
      REQUIRE(rejection&&[rejection.domain isEqualToString:@"SentinelMetalFrameCopy"]);
      REQUIRE(![copier copyTexture:source readyEvent:nil readyValue:0 toBuffer:NULL flipY:NO error:NULL completion:completion]);
      REQUIRE(![copier copyTexture:source readyEvent:nil readyValue:0 toBuffer:output flipY:NO error:NULL completion:nil]);
      REQUIRE(![copier copyTexture:source readyEvent:nil readyValue:1 toBuffer:output flipY:NO error:NULL completion:completion]);
      CVPixelBufferRef wrong=make_buffer(8,6,kCVPixelFormatType_OneComponent8);
      REQUIRE(![copier copyTexture:source readyEvent:nil readyValue:0 toBuffer:wrong flipY:NO error:NULL completion:completion]);CVPixelBufferRelease(wrong);
      MTLPixelFormat formats[]={MTLPixelFormatRGBA8Uint,MTLPixelFormatRGBA8Unorm_sRGB,MTLPixelFormatDepth32Float};
      for(unsigned i=0;i<3;++i) {
         MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[i] width:8 height:6 mipmapped:NO];d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageShaderRead;
         id<MTLTexture> unsupported=[device newTextureWithDescriptor:d];REQUIRE(unsupported);
         REQUIRE(![copier copyTexture:unsupported readyEvent:nil readyValue:0 toBuffer:output flipY:NO error:NULL completion:completion]);
      }
      MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:6 mipmapped:NO];d.textureType=MTLTextureType2DArray;d.arrayLength=2;d.usage=MTLTextureUsageShaderRead;
      id<MTLTexture> array=[device newTextureWithDescriptor:d];REQUIRE(array);
      REQUIRE(![copier copyTexture:array readyEvent:nil readyValue:0 toBuffer:output flipY:NO error:NULL completion:completion]);
      d.textureType=MTLTextureType2D;d.arrayLength=1;d.usage=MTLTextureUsageRenderTarget;
      id<MTLTexture> render_only=[device newTextureWithDescriptor:d];REQUIRE(render_only);
      REQUIRE(![copier copyTexture:render_only readyEvent:nil readyValue:0 toBuffer:output flipY:NO error:NULL completion:completion]);
      if([device supportsTextureSampleCount:4]) {
         d.textureType=MTLTextureType2DMultisample;d.arrayLength=1;d.sampleCount=4;d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageRenderTarget;
         id<MTLTexture> multisample=[device newTextureWithDescriptor:d];REQUIRE(multisample);
         REQUIRE(![copier copyTexture:multisample readyEvent:nil readyValue:0 toBuffer:output flipY:NO error:NULL completion:completion]);
      }
      [copier drain];REQUIRE(atomic_load(&failures)==0);CVPixelBufferRelease(output);
      printf("PASS input rejection: nil inputs, missing event, wrong CV format, uint/sRGB/depth/multi-slice-array/multisample/render-only textures; no completion on rejected submissions\n");
      dispatch_semaphore_t completed=dispatch_semaphore_create(0);id<MTLSharedEvent> life_gate=[device newSharedEvent];
      __block BOOL lifetime_ok=NO;
      @autoreleasepool {
         MetalFrameCopy *transient=[[MetalFrameCopy alloc]initWithDevice:device error:&error];REQUIRE(transient);
         CVPixelBufferRef temporary=make_buffer(8,6,kCVPixelFormatType_32BGRA);
         REQUIRE([transient copyTexture:make_source(device,YES) readyEvent:life_gate readyValue:1 toBuffer:temporary flipY:YES error:NULL completion:^(BOOL success){lifetime_ok=success&&check_buffer(temporary,YES);dispatch_semaphore_signal(completed);}]);
         CVPixelBufferRelease(temporary);transient=nil;
      }
      life_gate.signaledValue=1;
      REQUIRE(dispatch_semaphore_wait(completed,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC))==0&&lifetime_ok);
      printf("PASS asynchronous copier/source/CVPixelBuffer lifetime after caller references are dropped\n");
   }
   return 0;
}
