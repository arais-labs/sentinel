/* Compare packaged VirGL capabilities with the actual Metal-backed GL context. */
#include <epoxy/egl.h>
#include <epoxy/gl.h>
#include <virgl_hw.h>
#include <stdio.h>
#include <stdlib.h>

#define CHECK(test) do { if (!(test)) { fprintf(stderr, "Failed: %s\n", #test); return 1; } } while (0)

int main(int argc, char **argv) {
    CHECK(argc == 2);
    EGLDisplay display = eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
    CHECK(eglInitialize(display, NULL, NULL));
    CHECK(eglBindAPI(EGL_OPENGL_API));
    const EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE};
    EGLConfig config;
    EGLint count;
    CHECK(eglChooseConfig(display, attributes, &config, 1, &count) && count == 1);
    const EGLint version[] = {EGL_CONTEXT_MAJOR_VERSION_KHR, 4,
        EGL_CONTEXT_MINOR_VERSION_KHR, 3, EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,
        EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR, EGL_NONE};
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, version);
    CHECK(context != EGL_NO_CONTEXT);
    CHECK(eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context));
    GLint timestamp_bits = 0, elapsed_bits = 0;
    CHECK(epoxy_is_desktop_gl());
    if (epoxy_gl_version() >= 33 || epoxy_has_gl_extension("GL_ARB_timer_query")) {
        glGetQueryiv(GL_TIMESTAMP, GL_QUERY_COUNTER_BITS, &timestamp_bits);
        glGetQueryiv(GL_TIME_ELAPSED, GL_QUERY_COUNTER_BITS, &elapsed_bits);
    }
    CHECK(glGetError() == GL_NO_ERROR);
    FILE *file = fopen(argv[1], "rb");
    CHECK(file);
    unsigned checked = 0;
    uint32_t header[3];
    while (fread(header, sizeof(header), 1, file) == 1) {
        union virgl_caps caps = {0};
        CHECK(header[2] <= sizeof(caps));
        CHECK(fread(&caps, header[2], 1, file) == 1);
        CHECK(caps.v1.bset.timer_query == (timestamp_bits > 0 && elapsed_bits > 0));
        if (header[0] == 2) {
            /* Mesa ignores timer_query below protocol feature-check version 15. */
            CHECK(caps.v2.host_feature_check_version >= 15);
            checked++;
        }
    }
    CHECK(feof(file) && !ferror(file) && checked);
    fclose(file);
    printf("Timer capabilities verified: timestamp=%d bits, elapsed=%d bits\n",
           timestamp_bits, elapsed_bits);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglTerminate(display);
    return 0;
}
