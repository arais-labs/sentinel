#define main desktop_renderer_main
#include "DesktopRenderer.m"
#undef main
#include <assert.h>
#include <math.h>

static atomic_uint checkedFrames;
static atomic_bool pixelsFailed;
static bool expectedFlip;
static CVPixelBufferRef lastFrame;
extern int test_renderer_idle_wake(struct rvgpu_egl_state *,int);
extern int test_cursor_protocol(void);
extern void test_cursor_resources(struct rvgpu_egl_state *);

@interface RingTestDisplay : NSObject
- (void)setFrame:(CVPixelBufferRef)buffer;
- (void)publish:(NSData *)packet;
@end
@implementation RingTestDisplay
- (void)publish:(NSData *)packet { (void)packet; }
- (void)setFrame:(CVPixelBufferRef)buffer {
    CVPixelBufferLockBaseAddress(buffer,kCVPixelBufferLock_ReadOnly);
    unsigned char *base=CVPixelBufferGetBaseAddress(buffer);
    size_t stride=CVPixelBufferGetBytesPerRow(buffer),w=CVPixelBufferGetWidth(buffer),h=CVPixelBufferGetHeight(buffer);
    for(unsigned half=0;half<2;half++) {
        unsigned char *pixel=base+((half?3:1)*h/4)*stride+(w/2)*4;
        bool red=(half==0)!=expectedFlip;
        if(pixel[0]||pixel[1]!=(red?0:255)||pixel[2]!=(red?255:0)||pixel[3]!=255) {
            fprintf(stderr,"Ring pixel mismatch flip=%d half=%u BGRA=%u,%u,%u,%u\n",expectedFlip,half,pixel[0],pixel[1],pixel[2],pixel[3]);
            atomic_store(&pixelsFailed,true);
        }
    }
    CVPixelBufferUnlockBaseAddress(buffer,kCVPixelBufferLock_ReadOnly);
    if(lastFrame)CVPixelBufferRelease(lastFrame);
    lastFrame=CVPixelBufferRetain(buffer);
    atomic_fetch_add(&checkedFrames,1);
}
@end

@interface RingTestEncoder : NSObject
- (BOOL)canAcceptFrame;
- (void)encode:(CVPixelBufferRef)buffer;
- (void)close;
@end
@implementation RingTestEncoder
- (BOOL)canAcceptFrame {return YES;}
- (void)encode:(CVPixelBufferRef)buffer {(void)buffer;}
- (void)close {}
@end

// Compare every output pixel against the cursor-free retained desktop. This
// catches channel order, image orientation, premultiplied alpha, clipping,
// hotspot offsets, and trails after cursor-only updates.
static void checkCursorPixels(NSData *background,const uint8_t *image,unsigned cw,unsigned ch,int64_t x,int64_t y) {
    CVPixelBufferLockBaseAddress(lastFrame,kCVPixelBufferLock_ReadOnly);
    size_t row=CVPixelBufferGetBytesPerRow(lastFrame);
    const uint8_t *actual=CVPixelBufferGetBaseAddress(lastFrame),*base=background.bytes;
    for(int py=0;py<height;py++)for(int px=0;px<width;px++)for(unsigned c=0;c<4;c++) {
        unsigned expected=base[py*row+px*4+c];
        if(image && px>=x && py>=y && px<x+cw && py<y+ch) {
            const uint8_t *pixel=image+((py-y)*cw+px-x)*4;
            expected=pixel[c]+(unsigned)lround(expected*(255-pixel[3])/255.0);
        }
        unsigned value=actual[py*row+px*4+c];
        if(abs((int)value-(int)expected)>1) {
            fprintf(stderr,"Cursor pixel %d,%d channel %u: got %u expected %u frames=%u serial=%llu last=%llu pending=%d cursor=%lld,%lld hot=%u,%u\n",px,py,c,value,expected,atomic_load(&checkedFrames),frameSerial,lastDelivered,pendingFrame,cursorX,cursorY,cursorHotX,cursorHotY);
            abort();
        }
    }
    CVPixelBufferUnlockBaseAddress(lastFrame,kCVPixelBufferLock_ReadOnly);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        alarm(60);
        assert(test_cursor_protocol());
        int transport[2];
        assert(socketpair(AF_UNIX,SOCK_STREAM,0,transport)==0);
        inputFD=transport[0];
        markFrameFailure();markFrameFailure();
        char ignored;
        assert(read(transport[1],&ignored,1)==0&&atomic_load(&copyFailed));
        close(transport[0]);close(transport[1]);inputFD=-1;
        atomic_store(&copyFailed,false);
        puts("PASS asynchronous frame failure wakes idle transport");
        initEGL();
        if(argc>1&&!strcmp(argv[1],"cursor-resources")) {
            test_cursor_resources(&graphics);
            return 0;
        }
        initFrameWake();
        encoderLock=[NSLock new];
        display=(id)[RingTestDisplay new];
        EGLContext producer=rvgpu_egl_create_context(&graphics,4,3,1);
        assert(producer);
        unsigned submitted=0;
        const char *format=argc>1?argv[1]:"rgba8";
        GLenum internal=format&&!strcmp(format,"rgb8")?GL_RGB8:format&&!strcmp(format,"srgb8")?GL_SRGB8_ALPHA8:GL_RGBA8;
        unsigned scale=argc>2?(unsigned)atoi(argv[2]):1;
        unsigned mip=argc>3?(unsigned)atoi(argv[3]):0;
        assert((scale==1||scale==2)&&mip<=1);
        fprintf(stderr,"Ring variant format=%x source_scale=%u mip=%u\n",internal,scale,mip);
        for(unsigned phase=0;phase<4;phase++) {
            @autoreleasepool {
                int w=phase<2?320:640,h=phase<2?240:360;
                int sw=w*scale,sh=h*scale;
                expectedFlip=phase&1;
                assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context));
                configureVideo(w,h);
                [encoder close];encoder=(id)[RingTestEncoder new];
                for(unsigned i=0;i<3;i++)atomic_store(&slots[i]->busy,true);
                struct rvgpu_virgl_params scanout={.box={0,0,sw,sh},.y0_top=expectedFlip};
                GLsync readComplete=NULL;
                assert(!submitFrame(&scanout,&readComplete));
                for(unsigned i=0;i<3;i++)atomic_store(&slots[i]->busy,false);
                assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,producer));
                GLuint source,storage,fbo;
                glGenTextures(1,&source);glBindTexture(GL_TEXTURE_2D,source);
                glTexStorage2D(GL_TEXTURE_2D,mip+1,internal,sw<<mip,sh<<mip);
                storage=source;
                if(mip){glGenTextures(1,&source);glTextureView(source,GL_TEXTURE_2D,storage,internal,mip,1,0,1);}
                glGenFramebuffers(1,&fbo);glBindFramebuffer(GL_FRAMEBUFFER,fbo);
                glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,source,0);
                assert(glCheckFramebufferStatus(GL_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE);
                assert(glGetError()==GL_NO_ERROR);
                scanout.tex_id=source;
                for(unsigned batch=0;batch<8;batch++) {
                    for(unsigned frame=0;frame<3;frame++) {
                        assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,producer));
                        glDisable(GL_SCISSOR_TEST);glClearColor(0,1,0,1);glClear(GL_COLOR_BUFFER_BIT);
                        glEnable(GL_SCISSOR_TEST);glScissor(0,0,sw,sh/2);glClearColor(1,0,0,1);glClear(GL_COLOR_BUFFER_BIT);
                        glDisable(GL_SCISSOR_TEST);
                        assert(glGetError()==GL_NO_ERROR);
                        GLsync ready=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);glFlush();assert(ready);
                        assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context));
                        glWaitSync(ready,0,GL_TIMEOUT_IGNORED);glDeleteSync(ready);
                        if(submitFrame(&scanout,&readComplete))submitted++;
                        assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,producer));
                        if(readComplete){glWaitSync(readComplete,0,GL_TIMEOUT_IGNORED);glDeleteSync(readComplete);}
                    }
                    [frameCopy drain];
                    assert(!atomic_load(&copyFailed));
                    if(atomic_load(&pixelsFailed)) {
                        unsigned char pixel[4];
                        glBindFramebuffer(GL_READ_FRAMEBUFFER,fbo);
                        glReadPixels(sw/2,sh/4,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Failed phase=%u batch=%u producer source=%u RGBA=%u,%u,%u,%u error=%x\n",
                            phase,batch,source,pixel[0],pixel[1],pixel[2],pixel[3],glGetError());
                        GLint draw=0,attachment=0;
                        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&draw);
                        glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME,&attachment);
                        fprintf(stderr,"Failed producer drawFBO=%d expected=%u attachment=%d\n",draw,fbo,attachment);
                        glClearColor(0,1,0,1);glClear(GL_COLOR_BUFFER_BIT);glFinish();
                        glReadPixels(sw/2,sh/4,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Diagnostic reclear RGBA=%u,%u,%u,%u error=%x\n",pixel[0],pixel[1],pixel[2],pixel[3],glGetError());
                        GLuint probe,probeFB;
                        glGenTextures(1,&probe);glBindTexture(GL_TEXTURE_2D,probe);
                        glTexStorage2D(GL_TEXTURE_2D,1,GL_RGBA8,1,1);
                        glGenFramebuffers(1,&probeFB);glBindFramebuffer(GL_FRAMEBUFFER,probeFB);
                        glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,probe,0);
                        GLboolean mask[4];GLint drawBuffer;
                        glGetBooleanv(GL_COLOR_WRITEMASK,mask);glGetIntegerv(GL_DRAW_BUFFER0,&drawBuffer);
                        glClearColor(1,0,1,1);glClear(GL_COLOR_BUFFER_BIT);glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Diagnostic fresh clear RGBA=%u,%u,%u,%u mask=%d%d%d%d draw=%x error=%x\n",pixel[0],pixel[1],pixel[2],pixel[3],mask[0],mask[1],mask[2],mask[3],drawBuffer,glGetError());
                        const char *vs="#version 330 core\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}";
                        const char *fs="#version 330 core\nuniform sampler2D s;out vec4 color;void main(){color=texelFetch(s,textureSize(s,0)/4,0);}";
                        GLuint v=glCreateShader(GL_VERTEX_SHADER),f=glCreateShader(GL_FRAGMENT_SHADER),program=glCreateProgram(),vao;
                        glShaderSource(v,1,&vs,NULL);glCompileShader(v);glShaderSource(f,1,&fs,NULL);glCompileShader(f);
                        glAttachShader(program,v);glAttachShader(program,f);glLinkProgram(program);glUseProgram(program);
                        glUniform1i(glGetUniformLocation(program,"s"),0);glActiveTexture(GL_TEXTURE0);glBindTexture(GL_TEXTURE_2D,source);
                        glGenVertexArrays(1,&vao);glBindVertexArray(vao);glViewport(0,0,1,1);glDrawArrays(GL_TRIANGLES,0,3);
                        glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Diagnostic fresh shader RGBA=%u,%u,%u,%u error=%x\n",pixel[0],pixel[1],pixel[2],pixel[3],glGetError());
                        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_SWIZZLE_R,GL_BLUE);
                        glDrawArrays(GL_TRIANGLES,0,3);glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Diagnostic changed view RGBA=%u,%u,%u,%u error=%x\n",pixel[0],pixel[1],pixel[2],pixel[3],glGetError());
                        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_SWIZZLE_R,GL_RED);
                        glDrawArrays(GL_TRIANGLES,0,3);glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Diagnostic restored view RGBA=%u,%u,%u,%u error=%x\n",pixel[0],pixel[1],pixel[2],pixel[3],glGetError());
                        GLuint clone;glGenTextures(1,&clone);glBindTexture(GL_TEXTURE_2D,clone);
                        glTexStorage2D(GL_TEXTURE_2D,1,internal,sw,sh);
                        glCopyImageSubData(source,GL_TEXTURE_2D,0,0,0,0,clone,GL_TEXTURE_2D,0,0,0,0,sw,sh,1);
                        glDrawArrays(GL_TRIANGLES,0,3);glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                        fprintf(stderr,"Diagnostic compatible image copy shader RGBA=%u,%u,%u,%u error=%x\n",pixel[0],pixel[1],pixel[2],pixel[3],glGetError());
                        assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context));
                        for(unsigned i=0;i<3;i++) {
                            glBindFramebuffer(GL_READ_FRAMEBUFFER,slots[i]->framebuffer);
                            glReadPixels(w/2,h/4,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
                            fprintf(stderr,"Failed slot=%u texture=%u native=%p RGBA=%u,%u,%u,%u\n",
                                i,slots[i]->texture,(__bridge void*)slots[i]->nativeTexture,pixel[0],pixel[1],pixel[2],pixel[3]);
                        }
                    }
                    assert(!atomic_load(&pixelsFailed));
                }
                // Saturate the ring, then retain two updates. Only the newest may
                // appear, even after the guest's source texture has been destroyed.
                [frameCopy drain];
                retryPendingFrame(&graphics);
                unsigned before=atomic_load(&checkedFrames);
                for(unsigned i=0;i<3;i++)atomic_store(&slots[i]->busy,true);
                for(unsigned update=0;update<2;update++) {
                    assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,producer));
                    glBindFramebuffer(GL_FRAMEBUFFER,fbo);glDisable(GL_SCISSOR_TEST);
                    glClearColor(1,0,1,1);glClear(GL_COLOR_BUFFER_BIT);
                    if(update) {
                        glClearColor(0,1,0,1);glClear(GL_COLOR_BUFFER_BIT);
                        glEnable(GL_SCISSOR_TEST);glScissor(0,0,sw,sh/2);
                        glClearColor(1,0,0,1);glClear(GL_COLOR_BUFFER_BIT);glDisable(GL_SCISSOR_TEST);
                    }
                    GLsync ready=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);glFlush();
                    assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context));
                    glWaitSync(ready,0,GL_TIMEOUT_IGNORED);glDeleteSync(ready);
                    assert(!presentOrRetainFrame(&scanout,&readComplete)&&pendingFrame);
                    assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,producer));
                    glWaitSync(readComplete,0,GL_TIMEOUT_IGNORED);glDeleteSync(readComplete);
                }
                glDeleteFramebuffers(1,&fbo);glDeleteTextures(1,&source);glFlush();
                if(storage!=source)glDeleteTextures(1,&storage);
                int idle[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,idle)==0);
                dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{
                    for(unsigned i=0;i<3;i++)atomic_store(&slots[i]->busy,false);
                    wakeFrameCapacity();
                });
                // No byte is ever written to idle[1]. The actual dispatcher poll
                // must be awakened by completion capacity, not another guest frame.
                assert(test_renderer_idle_wake(&graphics,idle[0])==1);
                assert(!pendingFrame&&eglGetCurrentContext()==producer);
                [frameCopy drain];
                assert(!atomic_load(&pixelsFailed)&&atomic_load(&checkedFrames)==before+1);
                submitted++;
                close(idle[0]);close(idle[1]);
                printf("PASS idle latest-frame retry phase %u: coalesced updates, deleted guest source, no guest traffic\n",phase);
                printf("PASS ring phase %u: %dx%d flip=%d, 3-slot busy rejection, shared-context GPU ordering\n",phase,w,h,expectedFlip);
            }
        }
        assert(submitted>=32&&submitted==atomic_load(&checkedFrames));
        // There is no surviving guest source here, only our retained primary.
        CVPixelBufferLockBaseAddress(lastFrame,kCVPixelBufferLock_ReadOnly);
        NSData *background=[NSData dataWithBytes:CVPixelBufferGetBaseAddress(lastFrame)
            length:CVPixelBufferGetBytesPerRow(lastFrame)*height];
        CVPixelBufferUnlockBaseAddress(lastFrame,kCVPixelBufferLock_ReadOnly);
        const uint8_t cursorPixels[]={0,0,255,255, 0,128,0,128, 0,0,0,0,
                                      255,0,0,255, 64,0,64,128, 0,255,255,255};
        struct virtio_gpu_update_cursor cursor={.resource_id=7,.hot_x=1,.hot_y=1,.pos={.x=50,.y=60}};
        setGuestCursor(&graphics,&cursor,3,2,VIRTIO_GPU_FORMAT_B8G8R8A8_UNORM,cursorPixels);
        retryPendingFrame(&graphics);assert(!pendingFrame);[frameCopy drain];
        checkCursorPixels(background,cursorPixels,3,2,49,59);
        // Dumb-buffer allocation labels must not discard cursor-plane alpha.
        uint8_t rgbaPixels[sizeof(cursorPixels)];
        memcpy(rgbaPixels,cursorPixels,sizeof(cursorPixels));
        for(unsigned i=0;i<sizeof(cursorPixels);i+=4) {
            rgbaPixels[i]=cursorPixels[i+2];rgbaPixels[i+2]=cursorPixels[i];
        }
        setGuestCursor(&graphics,&cursor,3,2,VIRTIO_GPU_FORMAT_B8G8R8X8_UNORM,cursorPixels);
        retryPendingFrame(&graphics);assert(!pendingFrame);[frameCopy drain];
        checkCursorPixels(background,cursorPixels,3,2,49,59);
        setGuestCursor(&graphics,&cursor,3,2,VIRTIO_GPU_FORMAT_R8G8B8X8_UNORM,rgbaPixels);
        retryPendingFrame(&graphics);assert(!pendingFrame);[frameCopy drain];
        checkCursorPixels(background,cursorPixels,3,2,49,59);
        setGuestCursor(&graphics,&cursor,3,2,VIRTIO_GPU_FORMAT_B8G8R8A8_UNORM,cursorPixels);
        const int positions[][2]={{0,0},{639,359},{-10,20},{80,90}};
        for(unsigned i=0;i<4;i++) {
            cursor.pos.x=positions[i][0];cursor.pos.y=positions[i][1];
            moveGuestCursor(&graphics,&cursor);
            retryPendingFrame(&graphics);assert(!pendingFrame);[frameCopy drain];
            checkCursorPixels(background,cursorPixels,3,2,(int32_t)cursor.pos.x-1,(int32_t)cursor.pos.y-1);
        }
        for(unsigned i=0;i<3;i++)atomic_store(&slots[i]->busy,true);
        cursor.pos.x=200;moveGuestCursor(&graphics,&cursor);
        retryPendingFrame(&graphics);assert(pendingFrame);
        cursor.resource_id=0;setGuestCursor(&graphics,&cursor,0,0,0,NULL);
        for(unsigned i=0;i<3;i++)atomic_store(&slots[i]->busy,false);
        retryPendingFrame(&graphics);assert(!pendingFrame);[frameCopy drain];
        checkCursorPixels(background,NULL,0,0,0,0);
        // Moving an invisible cursor must never resurrect the previous image.
        cursor.pos.x=30;moveGuestCursor(&graphics,&cursor);
        retryPendingFrame(&graphics);assert(!pendingFrame);[frameCopy drain];
        checkCursorPixels(background,NULL,0,0,0,0);
        puts("PASS guest cursor: hotspot, BGRA, alpha, orientation, edge clipping, idle motion, coalesced hide, no trails");
        dispatch_semaphore_t encoded=dispatch_semaphore_create(0);
        __block BOOL configuration=NO,keyframe=NO,invalidPacket=NO;
        DesktopVideoEncoder *hardware=[[DesktopVideoEncoder alloc]
            initWithWidth:width height:height fps:120 bitrate:24000000 output:^(NSData *packet) {
                const uint8_t *bytes=packet.bytes;
                if(packet.length<16){invalidPacket=YES;dispatch_semaphore_signal(encoded);return;}
                uint32_t length;memcpy(&length,bytes+4,4);
                if(ntohl(length)!=packet.length-16){invalidPacket=YES;dispatch_semaphore_signal(encoded);return;}
                if(bytes[0]==1&&packet.length>=31) {
                    uint32_t w,h;memcpy(&w,bytes+16,4);memcpy(&h,bytes+20,4);
                    configuration=ntohl(w)==(unsigned)width&&ntohl(h)==(unsigned)height;
                } else if(bytes[0]==2) {
                    keyframe=(bytes[1]&1)!=0&&packet.length>16;
                    dispatch_semaphore_signal(encoded);
                } else {invalidPacket=YES;dispatch_semaphore_signal(encoded);}
            }];
        assert(lastFrame);
        [hardware encode:lastFrame];
        assert(dispatch_semaphore_wait(encoded,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC))==0);
        [hardware close];
        assert(configuration&&keyframe&&!invalidPacket);
        CVPixelBufferRelease(lastFrame);lastFrame=NULL;
        puts("PASS hardware H264 configuration and first keyframe from native Metal output");
        assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,graphics.context));
        releaseSlots();
        close(frameWake[0]);close(frameWake[1]);frameWake[0]=frameWake[1]=-1;
        glDeleteFramebuffers(1,&readFramebuffer);
        frameCopy=nil;readyEvent=nil;
        if(pool)CVPixelBufferPoolRelease(pool);
        assert(eglDestroyContext(graphics.dpy,producer));
        assert(eglMakeCurrent(graphics.dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT));
        assert(eglDestroyContext(graphics.dpy,graphics.context));
        assert(eglTerminate(graphics.dpy));
        printf("PASS %u native pixel-checked ring frames, resize and orderly shutdown; no rendering glFinish/readback\n",submitted);
    }
    return 0;
}
