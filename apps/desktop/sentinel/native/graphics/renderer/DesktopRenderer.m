// The workspace GPU and display outlive attached viewers. Only encoded video
// and virtual input leave this owning Mac; GPU commands never cross SSH.
#import <Foundation/Foundation.h>
#import <CoreVideo/CoreVideo.h>
#import <IOSurface/IOSurface.h>
#import "DesktopVideoEncoder.h"
#import "DesktopDisplayServer.h"
#import "MetalFrameCopy.h"
#include "GPUConnection.h"
#include <GL/mesa_glinterop.h>
#include "zink_metal_interop.h"
#include "kk_metal_interop.h"
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdatomic.h>
#include <rvgpu-renderer/rvgpu-renderer.h>
#include <rvgpu-generic/rvgpu-capset.h>
#include <rvgpu-utils/rvgpu-utils.h>
#include <virglrenderer.h>
#include <virgl_hw.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <signal.h>
#include <unistd.h>
#include <err.h>
#include <fcntl.h>

static struct rvgpu_egl_state graphics;
static int inputFD = -1;
static DesktopDisplayServer *display;
static int width = 1280, height = 720, fps = 120;
static NSLock *inputLock;
static NSLock *encoderLock;
static NSLock *deliveryLock;
static uint64_t frameSerial,lastDelivered;
static DesktopVideoEncoder *encoder;
static CVPixelBufferPoolRef pool;
static PFNMESAGLINTEROPEGLEXPORTOBJECTPROC exportObject;
static PFN_sentinel_kk_copy_metal_timeline_event_v1 copyEvent;
static MetalFrameCopy *frameCopy;
static id<MTLSharedEvent> readyEvent;
static uint64_t readyDevice, readySemaphore;
static atomic_bool copyFailed;
static atomic_uint completedFrames;
static GLuint readFramebuffer;
static unsigned nextSlot;
static int frameWake[2]={-1,-1};
static volatile sig_atomic_t stopRequested, stopWake = -1;
static volatile sig_atomic_t transportStopWake = -1;
static GLuint pendingTexture,pendingFramebuffer;
static BOOL pendingFrame;
static MetalCursorImage *cursorImage;
static id<MTLDevice> cursorDevice;
static int64_t cursorX,cursorY;
static uint32_t cursorHotX,cursorHotY;
static void retryPendingFrame(struct rvgpu_egl_state *state);
static void setGuestCursor(struct rvgpu_egl_state *,const struct virtio_gpu_update_cursor *,uint32_t,uint32_t,uint32_t,const void *);
static void moveGuestCursor(struct rvgpu_egl_state *,const struct virtio_gpu_update_cursor *);

static void requestStop(int signalNumber) {
    (void)signalNumber;
    int savedErrno=errno;
    stopRequested=1;
    // Only async-signal-safe work here. The rendering thread owns teardown.
    if(stopWake>=0) {
        const char byte=1;
        (void)write((int)stopWake,&byte,1);
    }
    if(transportStopWake>=0) {
        const char byte=1;
        (void)write((int)transportStopWake,&byte,1);
    }
    errno=savedErrno;
}

static void wakeFrameCapacity(void) {
    if(frameWake[1]<0)return;
    const char byte=1;
    ssize_t written;
    do { written=write(frameWake[1],&byte,1); } while(written<0&&errno==EINTR);
    // A full nonblocking pipe already contains a wake; never block GPU callbacks.
    if(written<0&&errno!=EAGAIN)err(1,"Wake desktop frame delivery");
}

static void initFrameWake(void) {
    if(pipe(frameWake))err(1,"Create desktop frame wake pipe");
    for(unsigned i=0;i<2;i++) {
        if(fcntl(frameWake[i],F_SETFL,O_NONBLOCK)||fcntl(frameWake[i],F_SETFD,FD_CLOEXEC))
            err(1,"Configure desktop frame wake pipe");
    }
    graphics.wake_fd=frameWake[0];
    stopWake=frameWake[1];
    struct sigaction action={0};
    action.sa_handler=requestStop;
    sigemptyset(&action.sa_mask);
    action.sa_flags=SA_RESTART;
    if(sigaction(SIGTERM,&action,NULL)||sigaction(SIGINT,&action,NULL))
        err(1,"Install desktop shutdown handler");
}

@interface OutputSlot : NSObject {
@public
    GLuint texture, framebuffer;
    id<MTLTexture> nativeTexture;
    atomic_bool busy;
}
@end
@implementation OutputSlot
@end
static OutputSlot *slots[3];

void *rvgpu_egl_create_context(struct rvgpu_egl_state *e,int major,int minor,int shared) {
    const EGLint attrs[]={EGL_CONTEXT_MAJOR_VERSION_KHR,major,EGL_CONTEXT_MINOR_VERSION_KHR,minor,
      EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,EGL_NONE};
    (void)shared;
    return eglCreateContext(e->dpy,e->config,e->context,attrs);
}
void rvgpu_egl_destroy_context(struct rvgpu_egl_state *e,void *ctx) { eglDestroyContext(e->dpy,ctx); }
int rvgpu_egl_make_context_current(struct rvgpu_egl_state *e,void *ctx) {
    return eglMakeCurrent(e->dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,ctx)?0:-1;
}
void rvgpu_egl_set_scanout(struct rvgpu_egl_state *e,struct rvgpu_scanout *s,const struct rvgpu_virgl_params *p) {
    (void)e;
    s->virgl=*p;
}


static NSString *graphicsDirectory(void) {
    char executable[PATH_MAX], resolved[PATH_MAX];
    uint32_t size=sizeof(executable);
    if(_NSGetExecutablePath(executable,&size) || !realpath(executable,resolved))
        errx(1,"Cannot resolve bundled graphics directory");
    return [@(resolved) stringByDeletingLastPathComponent];
}

static void initEGL(void) {
    NSString *directory=graphicsDirectory();
    NSString *icd=[directory stringByAppendingPathComponent:@"icd.json"];
    if(setenv("VK_DRIVER_FILES",icd.fileSystemRepresentation,1) ||
       setenv("MESA_LOADER_DRIVER_OVERRIDE","zink",1))
        err(1,"Select bundled graphics backend");
    // Graphics capabilities come from the bundled driver, never version overrides.
    unsetenv("MESA_GL_VERSION_OVERRIDE");
    unsetenv("MESA_GLSL_VERSION_OVERRIDE");
    unsetenv("LIBGL_ALWAYS_SOFTWARE");
    deliveryLock=[NSLock new];
    graphics.dpy=eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA,NULL,NULL);
    if(!eglInitialize(graphics.dpy,NULL,NULL)) errx(1,"EGL init: %x",eglGetError());
    if(!eglBindAPI(EGL_OPENGL_API)) errx(1,"EGL desktop API");
    EGLint conf[]={EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_BIT,
      EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_NONE};
    EGLint count;
    if(!eglChooseConfig(graphics.dpy,conf,&graphics.config,1,&count)||count!=1) errx(1,"EGL config");
    const EGLint ctx[]={EGL_CONTEXT_MAJOR_VERSION_KHR,4,EGL_CONTEXT_MINOR_VERSION_KHR,3,
      EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,EGL_NONE};
    graphics.context=eglCreateContext(graphics.dpy,graphics.config,EGL_NO_CONTEXT,ctx);
    if(!graphics.context) errx(1,"EGL context: %x",eglGetError());
    if(!eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context)) errx(1,"EGL current");
    fprintf(stderr,"HOST GPU: %s | %s\n",glGetString(GL_RENDERER),glGetString(GL_VERSION));
    if(!epoxy_is_desktop_gl() || !strstr((const char *)glGetString(GL_RENDERER),"zink")) errx(1,"Unexpected renderer");
    NSString *eglPath=[directory stringByAppendingPathComponent:@"libEGL.dylib"];
    NSString *icdPath=[directory stringByAppendingPathComponent:@"libvulkan_kosmickrisp.dylib"];
    void *eglLibrary=dlopen(eglPath.fileSystemRepresentation,RTLD_NOW|RTLD_LOCAL);
    void *icdLibrary=dlopen(icdPath.fileSystemRepresentation,RTLD_NOW|RTLD_LOCAL);
    exportObject=eglLibrary?dlsym(eglLibrary,"MesaGLInteropEGLExportObject"):NULL;
    copyEvent=icdLibrary?dlsym(icdLibrary,"sentinel_kk_copy_metal_timeline_event_v1"):NULL;
    if(!exportObject||!copyEvent) errx(1,"Bundled graphics libraries do not provide the required Metal interop ABI");
    static const struct rvgpu_egl_callbacks callbacks={.process_wake=retryPendingFrame,
        .set_cursor=setGuestCursor,.move_cursor=moveGuestCursor};
    graphics.cb=&callbacks; graphics.scanouts[0].params.enabled=true;
    graphics.wake_fd=-1;
}

// Cursor commands run on the GPU dispatcher thread. Each shape is immutable:
// Metal command buffers retain older shapes until their frame copy completes.
static void cursorChanged(void) {
    if(pendingTexture) {pendingFrame=YES;wakeFrameCapacity();}
}

static void moveGuestCursor(struct rvgpu_egl_state *state,const struct virtio_gpu_update_cursor *update) {
    (void)state;
    if(update->pos.scanout_id!=0)return;
    cursorX=(int32_t)update->pos.x;cursorY=(int32_t)update->pos.y;
    cursorChanged();
}

static void setGuestCursor(struct rvgpu_egl_state *state,const struct virtio_gpu_update_cursor *update,
                           uint32_t w,uint32_t h,uint32_t format,const void *pixels) {
    @autoreleasepool {
        (void)state;
        if(update->pos.scanout_id!=0)return;
        if(!update->resource_id) {
            cursorImage=nil;
        } else {
            BOOL bgra=format==VIRTIO_GPU_FORMAT_B8G8R8A8_UNORM||format==VIRTIO_GPU_FORMAT_B8G8R8X8_UNORM;
            BOOL rgba=format==VIRTIO_GPU_FORMAT_R8G8B8A8_UNORM||format==VIRTIO_GPU_FORMAT_R8G8B8X8_UNORM;
            if(!pixels||!w||!h||w>128||h>128||update->hot_x>=w||update->hot_y>=h||
               (!bgra&&!rgba))
                errx(1,"Invalid guest cursor image");
            // Virtio dumb buffers are allocated as XRGB, but the cursor plane
            // interprets their bytes as ARGB. Preserve that alpha, including
            // a fully transparent cursor supplied by the compositor.
            if(!cursorDevice)cursorDevice=MTLCreateSystemDefaultDevice();
            MetalCursorImage *image=[[MetalCursorImage alloc] initWithDevice:cursorDevice width:w height:h
                pixels:pixels bgra:bgra];
            if(!image)errx(1,"Allocate guest cursor image");
            cursorImage=image;
            cursorHotX=update->hot_x;cursorHotY=update->hot_y;
        }
        cursorX=(int32_t)update->pos.x;cursorY=(int32_t)update->pos.y;
        cursorChanged();
    }
}

static struct zink_metal_interop_export_sync exportTexture(GLuint texture) {
    struct zink_metal_interop_export_sync payload={.base={
      .magic=ZINK_METAL_INTEROP_MAGIC,.version=ZINK_METAL_INTEROP_SYNC_VERSION}};
    struct mesa_glinterop_export_in in={.version=2,.target=GL_TEXTURE_2D,.obj=texture,
      .access=MESA_GLINTEROP_ACCESS_READ_ONLY,.out_driver_data_size=sizeof(payload),.out_driver_data=&payload};
    struct mesa_glinterop_export_out out={.version=2,.dmabuf_fd=-1};
    int result=exportObject(graphics.dpy,graphics.context,&in,&out);
    if(result||out.out_driver_data_written!=sizeof(payload)||payload.base.status||
       payload.base.flags!=ZINK_METAL_INTEROP_TEXTURE_BORROWED||!payload.base.texture||
       payload.base.width!=(unsigned)width||payload.base.height!=(unsigned)height)
        errx(1,"Metal export failed: Mesa=%d status=%u bytes=%u",result,payload.base.status,out.out_driver_data_written);
    if(!readyEvent) {
        void *native=NULL;
        if(copyEvent((VkDevice)(uintptr_t)payload.device,(VkSemaphore)(uintptr_t)payload.semaphore,&native)!=VK_SUCCESS||!native)
            errx(1,"Metal timeline export failed");
        readyEvent=CFBridgingRelease(native);
        readyDevice=payload.device;readySemaphore=payload.semaphore;
    } else if(readyDevice!=payload.device||readySemaphore!=payload.semaphore) errx(1,"Metal timeline changed");
    return payload;
}

static void releaseSlots(void) {
    [frameCopy drain];
    for(unsigned i=0;i<3;i++) {
        OutputSlot *slot=slots[i];
        if(!slot)continue;
        glDeleteFramebuffers(1,&slot->framebuffer);
        glDeleteTextures(1,&slot->texture);
        slots[i]=nil;
    }
    if(pendingFramebuffer)glDeleteFramebuffers(1,&pendingFramebuffer);
    if(pendingTexture)glDeleteTextures(1,&pendingTexture);
    pendingFramebuffer=pendingTexture=0;pendingFrame=NO;
}

static void createSlots(void) {
    for(unsigned i=0;i<3;i++) {
        OutputSlot *slot=[OutputSlot new];
        atomic_init(&slot->busy,false);
        glGenTextures(1,&slot->texture);
        glBindTexture(GL_TEXTURE_2D,slot->texture);
        glTexStorage2D(GL_TEXTURE_2D,1,GL_RGBA8,width,height);
        glFlush();
        struct zink_metal_interop_export_sync payload=exportTexture(slot->texture);
        slot->nativeTexture=(__bridge id<MTLTexture>)(void *)(uintptr_t)payload.base.texture;
        if(!frameCopy) {
            NSError *error=nil;
            frameCopy=[[MetalFrameCopy alloc] initWithDevice:slot->nativeTexture.device error:&error];
            if(!frameCopy)errx(1,"Metal copy setup: %s",error.description.UTF8String);
            frameCopy.capacityAvailable=^{wakeFrameCapacity();};
        }
        glGenFramebuffers(1,&slot->framebuffer);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER,slot->framebuffer);
        glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,slot->texture,0);
        if(glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)errx(1,"Output framebuffer incomplete");
        slots[i]=slot;
    }
    glBindFramebuffer(GL_FRAMEBUFFER,0);
    if(glGetError())errx(1,"Output ring setup failed");
    nextSlot=0;
}

static void configureVideo(int frameWidth, int frameHeight) {
    if (frameWidth < 320 || frameHeight < 240 || frameWidth > 4096 || frameHeight > 4096
        || frameWidth * frameHeight > 16000000 || frameWidth % 2 || frameHeight % 2) errx(1,"Invalid scanout dimensions");
    if (pool && width == frameWidth && height == frameHeight) return;
    releaseSlots();
    width = frameWidth; height = frameHeight;
    [encoderLock lock];
    [encoder close];
    if (pool) CVPixelBufferPoolRelease(pool);
    NSDictionary *attrsCV=@{
      (__bridge NSString*)kCVPixelBufferPixelFormatTypeKey:@(kCVPixelFormatType_32BGRA),
      (__bridge NSString*)kCVPixelBufferWidthKey:@(width),
      (__bridge NSString*)kCVPixelBufferHeightKey:@(height),
      (__bridge NSString*)kCVPixelBufferIOSurfacePropertiesKey:@{},
      (__bridge NSString*)kCVPixelBufferMetalCompatibilityKey:@YES
    };
    if(CVPixelBufferPoolCreate(NULL,NULL,(__bridge CFDictionaryRef)attrsCV,&pool)) errx(1,"Pixel pool");
    encoder=[[DesktopVideoEncoder alloc] initWithWidth:width height:height fps:fps bitrate:24000000
      output:^(NSData *packet) {
        [display publish:packet];
      }];
    encoder.capacityAvailable=^{wakeFrameCapacity();};
    [encoderLock unlock];
    createSlots();
}
static BOOL sendInput(uint16_t device, const DesktopInputEvent *events, uint16_t count) {
    if(count>16)return NO;
    struct rvgpu_input_header header = {.dev=device, .src=0, .evnum=count};
    struct rvgpu_input_event converted[16];
    for (unsigned i=0; i<count; i++)
        converted[i]=(struct rvgpu_input_event){events[i].type,events[i].code,events[i].value};
    [inputLock lock];
    BOOL ok = inputFD >= 0 && write_all(inputFD,&header,sizeof(header))==sizeof(header)
        && write_all(inputFD,converted,count*sizeof(*converted))==count*sizeof(*converted);
    [inputLock unlock];
    return ok;
}

static void markFrameFailure(void) {
    if(atomic_exchange(&copyFailed,true))return;
    // Wake a dispatch blocked on an idle guest so runtime supervision observes
    // failure immediately. inputFD remains owned until frameCopy has drained;
    // shutdown interrupts input writers too, without waiting for their lock.
    if(inputFD>=0)shutdown(inputFD,SHUT_RDWR);
}

static BOOL submitFrame(const struct rvgpu_virgl_params *scanout, GLsync *readComplete) {
    *readComplete=NULL;
    if(atomic_load(&copyFailed))errx(1,"Native GPU copy failed");
    if(!encoder.canAcceptFrame)return NO;
    OutputSlot *slot=nil;
    for(unsigned i=0;i<3;i++) {
        unsigned index=(nextSlot+i)%3;
        bool expected=false;
        if(atomic_compare_exchange_strong(&slots[index]->busy,&expected,true)) {
            slot=slots[index];nextSlot=(index+1)%3;break;
        }
    }
    if(!slot)return NO;
    // Three copies, three encodes, one pending encode, plus display/last-frame
    // retention. Usually these share buffers, but the hard bound covers all.
    NSDictionary *threshold=@{(__bridge NSString*)kCVPixelBufferPoolAllocationThresholdKey:@9};
    CVPixelBufferRef buffer=NULL;
    CVReturn result=CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(NULL,pool,(__bridge CFDictionaryRef)threshold,&buffer);
    if(result==kCVReturnWouldExceedAllocationThreshold){atomic_store(&slot->busy,false);return NO;}
    if(result)errx(1,"Pixel pool allocation %d",result);
    if(!readFramebuffer)glGenFramebuffers(1,&readFramebuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER,readFramebuffer);
    glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,scanout->tex_id,0);
    if(glCheckFramebufferStatus(GL_READ_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)errx(1,"Scanout framebuffer");
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER,slot->framebuffer);
    glDisable(GL_SCISSOR_TEST);
    int y1=scanout->box.y+(scanout->y0_top?scanout->box.h:0);
    int y2=scanout->box.y+(scanout->y0_top?0:scanout->box.h);
    glBlitFramebuffer(scanout->box.x,y1,scanout->box.x+scanout->box.w,y2,
      0,0,width,height,GL_COLOR_BUFFER_BIT,GL_NEAREST);
    *readComplete=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);
    glFlush();
    if(!*readComplete||glGetError())errx(1,"GPU output blit failed");
    struct zink_metal_interop_export_sync payload=exportTexture(slot->texture);
    if(payload.base.texture!=(uintptr_t)(__bridge void *)slot->nativeTexture)errx(1,"Exported texture identity changed");
    uint64_t serial=++frameSerial;
    NSError *copyError=nil;
    BOOL submitted=[frameCopy copyTexture:slot->nativeTexture readyEvent:readyEvent readyValue:payload.value
      toBuffer:buffer flipY:NO cursor:cursorImage x:cursorX-cursorHotX y:cursorY-cursorHotY
      error:&copyError completion:^(BOOL success) {
        [deliveryLock lock];
        @try {
            if(success&&serial>lastDelivered) {
                lastDelivered=serial;
                [display setFrame:buffer];
                [encoder encode:buffer];
                if(atomic_fetch_add(&completedFrames,1)==0)fprintf(stderr,"FIRST VIDEO FRAME\n");
            } else if(!success)markFrameFailure();
        } @catch(NSException *error) {
            fprintf(stderr,"Desktop frame completion failed: %s\n",error.reason.UTF8String);
            markFrameFailure();
        } @finally {
            [deliveryLock unlock];
            atomic_store(&slot->busy,false);
        }
      }];
    CVPixelBufferRelease(buffer);
    if(!submitted)atomic_store(&slot->busy,false);
    if(copyError)errx(1,"Desktop GPU frame copy rejected: %s",copyError.localizedDescription.UTF8String);
    return submitted;
}

// Retain a cursor-free snapshot even when delivery is not backpressured. Cursor
// motion/hide must present on an otherwise idle desktop without reading a guest
// buffer that has since been reused or destroyed, or leaving cursor trails.
static BOOL presentOrRetainFrame(const struct rvgpu_virgl_params *scanout, GLsync *readComplete) {
    *readComplete=NULL;
    if(!pendingTexture) {
        glGenTextures(1,&pendingTexture);glBindTexture(GL_TEXTURE_2D,pendingTexture);
        glTexStorage2D(GL_TEXTURE_2D,1,GL_RGBA8,width,height);
        glGenFramebuffers(1,&pendingFramebuffer);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER,pendingFramebuffer);
        glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,pendingTexture,0);
        if(glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)errx(1,"Pending desktop framebuffer");
    }
    if(!readFramebuffer)glGenFramebuffers(1,&readFramebuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER,readFramebuffer);
    glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,scanout->tex_id,0);
    if(glCheckFramebufferStatus(GL_READ_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)errx(1,"Pending scanout framebuffer");
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER,pendingFramebuffer);glDisable(GL_SCISSOR_TEST);
    int y1=scanout->box.y+(scanout->y0_top?scanout->box.h:0);
    int y2=scanout->box.y+(scanout->y0_top?0:scanout->box.h);
    glBlitFramebuffer(scanout->box.x,y1,scanout->box.x+scanout->box.w,y2,
        0,0,width,height,GL_COLOR_BUFFER_BIT,GL_NEAREST);
    *readComplete=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);glFlush();
    if(!*readComplete||glGetError())errx(1,"Retain pending desktop frame");
    pendingFrame=YES;
    struct rvgpu_virgl_params saved={.tex_id=pendingTexture,.box={0,0,(unsigned)width,(unsigned)height}};
    GLsync consumed=NULL;
    BOOL submitted=submitFrame(&saved,&consumed);
    if(consumed)glDeleteSync(consumed);
    if(submitted)pendingFrame=NO;
    return submitted;
}

static void retryPendingFrame(struct rvgpu_egl_state *state) {
    @autoreleasepool {
        char bytes[256];ssize_t count;
        do {count=read(state->wake_fd,bytes,sizeof(bytes));} while(count>0||(count<0&&errno==EINTR));
        if(count<0&&errno!=EAGAIN)err(1,"Read desktop frame wake");
        if(stopRequested) {
            if(inputFD>=0)shutdown(inputFD,SHUT_RDWR);
            return;
        }
        if(!pendingFrame)return;
        EGLContext previous=eglGetCurrentContext();
        if(!eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context))errx(1,"Pending frame context");
        struct rvgpu_virgl_params saved={.tex_id=pendingTexture,.box={0,0,(unsigned)width,(unsigned)height}};
        GLsync consumed=NULL;
        if(submitFrame(&saved,&consumed))pendingFrame=NO;
        if(consumed)glDeleteSync(consumed);
        if(!eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,previous))errx(1,"Restore pending frame context");
    }
}

static void render(const char *capPath, const char *forward, NSString *videoPath, NSString *controlPath) {
    initEGL();
    initFrameWake();
    struct rvgpu_pr_params params={.sp=&graphics.scanouts[0].params,.nsp=1};
    // Initialize before connecting so the kernel probe receives actual capabilities.
    struct rvgpu_pr_state *renderer=rvgpu_pr_init(&graphics,&params,-1,-1);
    FILE *caps=fopen(capPath,"wb"); if(!caps) err(1,"capset");
    for(unsigned id=1;id<=2;id++) {
      uint32_t version,size; virgl_renderer_get_cap_set(id,&version,&size);
      if(!size||size>2048) errx(1,"Invalid capabilities");
      for(unsigned v=0;v<=version;v++) {
        uint8_t data[2048]={0}; struct capset h={id,v,size};
        virgl_renderer_fill_caps(id,v,data);
        if (id == 2) {
          // The proxy transports explicit TRANSFER_FROM_HOST_3D readbacks, not
          // host writes into guest staging buffers inside SUBMIT_3D. Advertising
          // that optimization makes Mesa read unsynchronized, zero-filled RAM.
          struct virgl_caps_v2 *capabilities = (void *)data;
          capabilities->capability_bits_v2 &= ~VIRGL_CAP_V2_COPY_TRANSFER_BOTH_DIRECTIONS;
        }
        if(fwrite(&h,sizeof(h),1,caps)!=1 || fwrite(data,size,1,caps)!=1)
            err(1,"Write GPU capabilities");
      }
    }
    if(fclose(caps)) err(1,"Close GPU capabilities");
    puts("{\"event\":\"capabilities_ready\"}"); fflush(stdout);
    // Re-create once with connected channels. Only one renderer lives at a time.
    rvgpu_pr_free(renderer);
    if(!eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context))errx(1,"Restore root EGL context");
    if(!forward) {
        eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
        eglDestroyContext(graphics.dpy,graphics.context);
        eglTerminate(graphics.dpy);
        fprintf(stderr,"CAPABILITIES READY\n");
        return;
    }
    int channels[2];
    struct sentinel_gpu_connection *connection=sentinel_gpu_connection_open(forward,channels);
    if(!connection)err(1,"Connect GPU transport");
    transportStopWake=sentinel_gpu_connection_stop_fd(connection);
    if(stopRequested)requestStop(0);
    int cmd=channels[0],res=channels[1];
    uint32_t nameLength;
    if(read_all(cmd,&nameLength,4)!=4) errx(1,"Missing proxy handshake");
    nameLength=ntohl(nameLength);
    if(nameLength>256) errx(1,"Invalid proxy handshake");
    char name[257]={0}; if(read_all(cmd,name,nameLength)!=nameLength) errx(1,"Short handshake");
    fprintf(stderr,"PROXY CONNECTED: %s\n",name);
    renderer=rvgpu_pr_init(&graphics,&params,cmd,res);
    inputFD=cmd;

    inputLock = [NSLock new];
    encoderLock = [NSLock new];
    display = [[DesktopDisplayServer alloc] initWithVideoPath:videoPath controlPath:controlPath
      input:^BOOL(uint16_t device, const DesktopInputEvent *events, uint16_t count) {
        return sendInput(device, events, count);
      } keyframe:^{ [encoderLock lock]; [encoder requestKeyframe]; [encoderLock unlock]; }
      ownedEndpoint:^(NSDictionary<NSString *, NSString *> *event) {
        NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:nil];
        fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout); fflush(stdout);
      }];
    fprintf(stderr,"VIDEO HOST READY\n");
    puts("{\"event\":\"ready\"}"); fflush(stdout);
    unsigned frames=0,skipped=0;
    while(!stopRequested) {
      unsigned resource=rvgpu_pr_dispatch(renderer); if(!resource) break;
      struct rvgpu_virgl_params *p=&graphics.scanouts[0].virgl;
      if(resource!=p->res_id || !p->tex_id) continue;
      @autoreleasepool {
        EGLContext producer=eglGetCurrentContext();
        GLsync sourceReady=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);
        glFlush();
        if(!sourceReady||!eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context))errx(1,"Scanout context switch");
        glWaitSync(sourceReady,0,GL_TIMEOUT_IGNORED);
        glDeleteSync(sourceReady);
        configureVideo(p->box.w,p->box.h);
        GLsync readComplete=NULL;
        if(presentOrRetainFrame(p,&readComplete))frames++;else skipped++;
        if(!eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,producer))errx(1,"Restore producer context");
        if(readComplete){glWaitSync(readComplete,0,GL_TIMEOUT_IGNORED);glDeleteSync(readComplete);}
      }
    }
    transportStopWake=-1;
    int transportError=sentinel_gpu_connection_close(connection);
    [frameCopy drain];
    [display close];
    [encoder close];
    stopWake=-1;
    close(frameWake[0]);close(frameWake[1]);frameWake[0]=frameWake[1]=-1;
    [inputLock lock];inputFD=-1;[inputLock unlock];
    eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context);
    releaseSlots();
    if(readFramebuffer)glDeleteFramebuffers(1,&readFramebuffer);
    rvgpu_pr_free(renderer);
    close(cmd);
    if (pool) CVPixelBufferPoolRelease(pool);
    eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
    eglDestroyContext(graphics.dpy, graphics.context);
    eglTerminate(graphics.dpy);
    frameCopy=nil;readyEvent=nil;cursorImage=nil;cursorDevice=nil;
    if(atomic_load(&copyFailed)) errx(1,"Desktop GPU frame delivery failed");
    if(transportError&&!stopRequested)errx(1,"GPU transport failed: %s",strerror(transportError));
    fprintf(stderr,"VIDEO HOST DISCONNECTED submitted=%u completed=%u skipped=%u\n",frames,atomic_load(&completedFrames),skipped);
}
int main(int argc,char **argv) {
    @autoreleasepool {
        signal(SIGPIPE,SIG_IGN);
        if (argc != 3 && argc != 9) errx(1,
            "Usage: sentinel-desktop-renderer CAPSET --capabilities | CAPSET FORWARD VIDEO CONTROL WIDTH HEIGHT FPS --serve");
        if (argc == 9) {
            width = atoi(argv[5]); height = atoi(argv[6]); fps = atoi(argv[7]);
            if (width < 320 || height < 240 || width > 4096 || height > 4096
                || width * height > 16000000 || width % 2 || height % 2 || fps < 1 || fps > 120
                || strcmp(argv[8], "--serve")) errx(1, "Invalid desktop dimensions/frame rate");
        } else if (strcmp(argv[2], "--capabilities")) errx(1, "Invalid renderer mode");
        @try {
            render(argv[1], argc == 9 ? argv[2] : NULL,
                argc == 9 ? @(argv[3]) : nil, argc == 9 ? @(argv[4]) : nil);
        } @catch (NSException *error) {
            fprintf(stderr, "Desktop renderer failed: %s\n", error.reason.UTF8String);
            return 1;
        }
    }
    return 0;
}
