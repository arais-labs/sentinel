#import "DesktopVideoEncoder.h"
#import <VideoToolbox/VideoToolbox.h>
#import <QuartzCore/QuartzCore.h>
#include <stdatomic.h>
#include <arpa/inet.h>

static void requireStatus(OSStatus status, NSString *operation) {
    if (status != noErr) @throw [NSException exceptionWithName:@"DesktopVideoEncoder"
        reason:[NSString stringWithFormat:@"%@: %d", operation, (int)status] userInfo:nil];
}
static NSData *packet(uint8_t kind, uint8_t flags, int64_t timestamp, NSData *payload) {
    if (payload.length > 4 * 1024 * 1024) @throw [NSException exceptionWithName:@"DesktopVideoEncoder"
        reason:@"Encoded frame exceeds packet limit" userInfo:nil];
    uint8_t header[16] = {kind, flags};
    uint32_t size = htonl((uint32_t)payload.length);
    uint64_t time = CFSwapInt64HostToBig((uint64_t)timestamp);
    memcpy(header + 4, &size, 4); memcpy(header + 8, &time, 8);
    NSMutableData *data = [NSMutableData dataWithBytes:header length:sizeof(header)];
    [data appendData:payload];
    return data;
}

@implementation DesktopVideoEncoder {
    VTCompressionSessionRef _session;
    dispatch_queue_t _submission;
    DesktopVideoOutput _output;
    atomic_int _inFlight;
    atomic_bool _closed, _forceKey;
    CVPixelBufferRef _lastFrame;
    CVPixelBufferRef _pendingFrame;
    BOOL _pumpScheduled;
    int64_t _lastTimestamp;
    uint32_t _width, _height;
}

static void compressed(void *context, void *frame, OSStatus status,
                       VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
    (void)frame;
    DesktopVideoEncoder *encoder = (__bridge DesktopVideoEncoder *)context;
    @autoreleasepool {
        @try {
            if (status != noErr || !sample || (flags & kVTEncodeInfo_FrameDropped)) {
                atomic_store(&encoder->_forceKey, true);
                fprintf(stderr, "Desktop hardware encoder dropped frame: %d\n", (int)status);
                return;
            }
            @synchronized (encoder) {
            CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sample, false);
            BOOL key = !attachments || !CFDictionaryContainsKey(CFArrayGetValueAtIndex(attachments, 0), kCMSampleAttachmentKey_NotSync);
            if (key) {
                CMFormatDescriptionRef format = CMSampleBufferGetFormatDescription(sample);
                NSDictionary *atoms = (__bridge NSDictionary *)CMFormatDescriptionGetExtension(format, kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms);
                NSData *avcc = atoms[@"avcC"];
                if (![avcc isKindOfClass:NSData.class] || avcc.length < 7) {
                    @throw [NSException exceptionWithName:@"DesktopVideoEncoder" reason:@"Missing AVC configuration" userInfo:nil];
                }
                uint32_t dimensions[2] = {htonl(encoder->_width), htonl(encoder->_height)};
                NSMutableData *config = [NSMutableData dataWithBytes:dimensions length:sizeof(dimensions)];
                [config appendData:avcc];
                encoder->_output(packet(1, 0, 0, config));
            }
            CMBlockBufferRef block = CMSampleBufferGetDataBuffer(sample);
            size_t size = CMBlockBufferGetDataLength(block);
            if (size > 4 * 1024 * 1024) @throw [NSException exceptionWithName:@"DesktopVideoEncoder" reason:@"Encoded frame too large" userInfo:nil];
            NSMutableData *bytes = [NSMutableData dataWithLength:size];
            requireStatus(CMBlockBufferCopyDataBytes(block, 0, size, bytes.mutableBytes), @"Read encoded frame");
            int64_t timestamp = CMTimeConvertScale(CMSampleBufferGetPresentationTimeStamp(sample), 1000000, kCMTimeRoundingMethod_Default).value;
            encoder->_output(packet(2, key ? 1 : 0, timestamp, bytes));
            }
        } @catch (NSException *error) {
            fprintf(stderr, "Desktop video output failed: %s\n", error.reason.UTF8String);
            atomic_store(&encoder->_forceKey, true);
        } @finally {
            atomic_fetch_sub(&encoder->_inFlight, 1);
            [encoder schedulePending];
            if(encoder.capacityAvailable)encoder.capacityAvailable();
        }
    }
}

- (instancetype)initWithWidth:(int)width height:(int)height fps:(int)fps
                      bitrate:(int)bitrate output:(DesktopVideoOutput)output {
    if (!(self = [super init])) return nil;
    _width = width; _height = height; _output = [output copy];
    atomic_init(&_inFlight, 0); atomic_init(&_closed, false); atomic_init(&_forceKey, true);
    _submission = dispatch_queue_create("us.arais.sentinel.desktop.encoder", DISPATCH_QUEUE_SERIAL);
    NSDictionary *spec = @{(__bridge NSString *)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: @YES,
        (__bridge NSString *)kVTVideoEncoderSpecification_EnableLowLatencyRateControl: @YES};
    requireStatus(VTCompressionSessionCreate(NULL, width, height, kCMVideoCodecType_H264,
        (__bridge CFDictionaryRef)spec, NULL, NULL, compressed, (__bridge void *)self, &_session), @"Create hardware H.264 encoder");
    requireStatus(VTSessionSetProperty(_session, kVTCompressionPropertyKey_RealTime, kCFBooleanTrue), @"Real time");
    requireStatus(VTSessionSetProperty(_session, kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse), @"Disable frame reordering");
    requireStatus(VTSessionSetProperty(_session, kVTCompressionPropertyKey_ProfileLevel, kVTProfileLevel_H264_High_AutoLevel), @"H.264 profile");
    requireStatus(VTSessionSetProperty(_session, kVTCompressionPropertyKey_ExpectedFrameRate, (__bridge CFNumberRef)@(fps)), @"Frame rate");
    requireStatus(VTSessionSetProperty(_session, kVTCompressionPropertyKey_AverageBitRate, (__bridge CFNumberRef)@(bitrate)), @"Bit rate");
    requireStatus(VTSessionSetProperty(_session, kVTCompressionPropertyKey_MaxKeyFrameInterval, (__bridge CFNumberRef)@(fps * 2)), @"Keyframe interval");
    requireStatus(VTCompressionSessionPrepareToEncodeFrames(_session), @"Prepare encoder");
    fprintf(stderr, "Hardware H.264 encoder ready: %dx%d, target %d fps, %d bit/s\n", width, height, fps, bitrate);
    return self;
}
- (BOOL)canAcceptFrame { return !atomic_load(&_closed) && atomic_load(&_inFlight) < 3; }
- (void)encode:(CVPixelBufferRef)buffer {
    @synchronized(_submission) {
        if (atomic_load(&_closed)) return;
        if(_pendingFrame)CVPixelBufferRelease(_pendingFrame);
        _pendingFrame=CVPixelBufferRetain(buffer);
    }
    [self schedulePending];
}
- (void)schedulePending {
    @synchronized(_submission) {
        if(atomic_load(&_closed)||_pumpScheduled||!_pendingFrame||atomic_load(&_inFlight)>=3)return;
        _pumpScheduled=YES;
    }
    dispatch_async(_submission, ^{
        CVPixelBufferRef buffer=NULL;
        @synchronized(self->_submission) {
            self->_pumpScheduled=NO;
            if(atomic_load(&self->_closed)||atomic_load(&self->_inFlight)>=3)return;
            buffer=self->_pendingFrame;self->_pendingFrame=NULL;
            if(!buffer)return;
            atomic_fetch_add(&self->_inFlight,1);
        }
        if (self->_lastFrame) CVPixelBufferRelease(self->_lastFrame);
        self->_lastFrame = CVPixelBufferRetain(buffer);
        int64_t timestamp = MAX(self->_lastTimestamp + 1, (int64_t)(CACurrentMediaTime() * 1000000));
        self->_lastTimestamp = timestamp;
        NSDictionary *options = atomic_exchange(&self->_forceKey, false)
            ? @{(__bridge NSString *)kVTEncodeFrameOptionKey_ForceKeyFrame: @YES} : nil;
        OSStatus status = VTCompressionSessionEncodeFrame(self->_session, buffer, CMTimeMake(timestamp, 1000000),
            kCMTimeInvalid, (__bridge CFDictionaryRef)options, NULL, NULL);
        CVPixelBufferRelease(buffer);
        if (status != noErr) {
            atomic_fetch_sub(&self->_inFlight, 1); atomic_store(&self->_forceKey, true);
            if(self.capacityAvailable)self.capacityAvailable();
        }
        [self schedulePending];
    });
}
- (void)requestKeyframe {
    if (atomic_exchange(&_forceKey, true)) return;
    // A reconnect must also work when the desktop is completely idle.
    dispatch_async(_submission, ^{ if (self->_lastFrame && !atomic_load(&self->_closed)) [self encode:self->_lastFrame]; });
}
- (void)close {
    if (atomic_exchange(&_closed, true)) return;
    dispatch_sync(_submission, ^{});
    if (_session) {
        VTCompressionSessionCompleteFrames(_session, kCMTimeInvalid);
        VTCompressionSessionInvalidate(_session); CFRelease(_session); _session = NULL;
    }
    if (_lastFrame) { CVPixelBufferRelease(_lastFrame); _lastFrame = NULL; }
    @synchronized(_submission) {
        if(_pendingFrame){CVPixelBufferRelease(_pendingFrame);_pendingFrame=NULL;}
    }
}
@end
