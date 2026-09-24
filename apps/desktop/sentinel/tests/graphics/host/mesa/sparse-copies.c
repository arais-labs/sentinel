#include <epoxy/egl.h>
#include <epoxy/gl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define REQUIRE(x)                                                                                 \
   do {                                                                                            \
      if (!(x)) {                                                                                  \
         fprintf(stderr, "FAIL %s line %d\n", #x, __LINE__);                                       \
         return 1;                                                                                 \
      }                                                                                            \
   } while (0)
static double now(void)
{
   struct timespec t;
   clock_gettime(CLOCK_MONOTONIC, &t);
   return t.tv_sec + t.tv_nsec / 1e9;
}
int main(void)
{
   if (getenv("MESA_GL_VERSION_OVERRIDE") || getenv("MESA_GLSL_VERSION_OVERRIDE") ||
       getenv("MESA_EXTENSION_OVERRIDE"))
      return 2;
   EGLDisplay d = eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
   REQUIRE(eglInitialize(d, NULL, NULL) && eglBindAPI(EGL_OPENGL_API));
   EGLConfig config;
   EGLint count;
   const EGLint attrs[] = {EGL_SURFACE_TYPE,
                           EGL_PBUFFER_BIT,
                           EGL_RENDERABLE_TYPE,
                           EGL_OPENGL_BIT,
                           EGL_RED_SIZE,
                           8,
                           EGL_GREEN_SIZE,
                           8,
                           EGL_BLUE_SIZE,
                           8,
                           EGL_ALPHA_SIZE,
                           8,
                           EGL_NONE};
   REQUIRE(eglChooseConfig(d, attrs, &config, 1, &count) && count);
   const EGLint ctxattrs[] = {EGL_CONTEXT_MAJOR_VERSION,
                              4,
                              EGL_CONTEXT_MINOR_VERSION,
                              3,
                              EGL_CONTEXT_OPENGL_PROFILE_MASK,
                              EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
                              EGL_NONE};
   EGLContext context = eglCreateContext(d, config, EGL_NO_CONTEXT, ctxattrs);
   REQUIRE(context != EGL_NO_CONTEXT && eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, context));
   printf("Renderer: %s\n", glGetString(GL_RENDERER));
   for (unsigned copies = 1024; copies <= 32768; copies *= 2) {
      size_t size = copies * 8;
      unsigned char *expected = malloc(size), *actual = malloc(size);
      REQUIRE(expected && actual);
      memset(expected, 0xcd, size);
      GLuint buffers[2];
      glGenBuffers(2, buffers);
      glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
      const unsigned char source[] = {0x12, 0x34, 0x56};
      glBufferData(GL_COPY_READ_BUFFER, sizeof(source), source, GL_STATIC_DRAW);
      glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[1]);
      glBufferData(GL_COPY_WRITE_BUFFER, size, expected, GL_DYNAMIC_DRAW);
      glGetBufferSubData(GL_COPY_WRITE_BUFFER, 0, size, actual);
      REQUIRE(!memcmp(expected, actual, size));
      glFinish();
      double start = now();
      for (unsigned i = 0; i < copies; i++) {
         glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, i * 8, 3);
         memcpy(expected + i * 8, source, 3);
      }
      glFinish();
      double elapsed = now() - start;
      glGetBufferSubData(GL_COPY_WRITE_BUFFER, 0, size, actual);
      REQUIRE(glGetError() == GL_NO_ERROR && !memcmp(expected, actual, size));
      printf("PASS %u disjoint 3-byte copies + untouched guards %.3f ms\n", copies, elapsed * 1000);
      for (unsigned i = 0; i < copies; i++) {
         glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 2, i * 8 + 1, 1);
         expected[i * 8 + 1] = source[2];
      }
      for (unsigned i = 0; i < copies; i++) {
         glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, i * 8 + 2, 3);
         memcpy(expected + i * 8 + 2, source, 3);
      }
      glFinish();
      glGetBufferSubData(GL_COPY_WRITE_BUFFER, 0, size, actual);
      REQUIRE(glGetError() == GL_NO_ERROR && !memcmp(expected, actual, size));
      printf("PASS %u overlapping writes + untouched guards\n", copies);
      glDeleteBuffers(2, buffers);
      free(expected);
      free(actual);
   }
   eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
   eglDestroyContext(d, context);
   eglTerminate(d);
   return 0;
}
