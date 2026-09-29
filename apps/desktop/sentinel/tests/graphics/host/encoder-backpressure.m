#import <VideoToolbox/VideoToolbox.h>
#include <assert.h>
#include <stdatomic.h>
#include <unistd.h>

static dispatch_semaphore_t submitted;
static unsigned tags[4];
static atomic_uint submissions;

// Control the asynchronous hardware completion boundary deterministically.
// Frame-ring separately verifies the real VideoToolbox encoder and H264 bytes.
static OSStatus captureEncode(VTCompressionSessionRef session, CVImageBufferRef image,
    CMTime timestamp, CMTime duration, CFDictionaryRef properties, void *context,
    VTEncodeInfoFlags *flags) {
    (void)session;(void)timestamp;(void)duration;(void)properties;(void)context;(void)flags;
    unsigned index=atomic_fetch_add(&submissions,1);assert(index<4);
    CVPixelBufferLockBaseAddress(image,kCVPixelBufferLock_ReadOnly);
    tags[index]=*(const unsigned char *)CVPixelBufferGetBaseAddress(image);
    CVPixelBufferUnlockBaseAddress(image,kCVPixelBufferLock_ReadOnly);
    dispatch_semaphore_signal(submitted);
    return noErr;
}
#define VTCompressionSessionEncodeFrame captureEncode
#include "DesktopVideoEncoder.m"
#undef VTCompressionSessionEncodeFrame

static void submitTag(DesktopVideoEncoder *encoder,unsigned char tag) {
    CVPixelBufferRef buffer=NULL;
    assert(CVPixelBufferCreate(NULL,320,240,kCVPixelFormatType_32BGRA,NULL,&buffer)==kCVReturnSuccess);
    CVPixelBufferLockBaseAddress(buffer,0);
    *(unsigned char *)CVPixelBufferGetBaseAddress(buffer)=tag;
    CVPixelBufferUnlockBaseAddress(buffer,0);
    [encoder encode:buffer];CVPixelBufferRelease(buffer);
}
static void expectSubmission(void) {
    assert(dispatch_semaphore_wait(submitted,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0);
}
int main(void) {
    @autoreleasepool {
        alarm(15);
        submitted=dispatch_semaphore_create(0);
        DesktopVideoEncoder *encoder=[[DesktopVideoEncoder alloc]
            initWithWidth:320 height:240 fps:120 bitrate:2000000 output:^(NSData *packet){(void)packet;}];
        for(unsigned char tag=1;tag<=3;tag++){submitTag(encoder,tag);expectSubmission();}
        assert(!encoder.canAcceptFrame&&atomic_load(&submissions)==3);
        submitTag(encoder,4);submitTag(encoder,5);
        assert(atomic_load(&submissions)==3);
        // A completion, not a new encode request, must pump the newest retained
        // buffer. Simulated dropped completions also exercise error recovery.
        compressed((__bridge void *)encoder,NULL,noErr,kVTEncodeInfo_FrameDropped,NULL);
        expectSubmission();
        assert(atomic_load(&submissions)==4&&tags[0]==1&&tags[1]==2&&tags[2]==3&&tags[3]==5);
        for(unsigned i=0;i<3;i++)compressed((__bridge void *)encoder,NULL,noErr,kVTEncodeInfo_FrameDropped,NULL);
        [encoder close];
        puts("PASS encoder bounded pending-latest frame resumes on completion without new input");
    }
    return 0;
}
