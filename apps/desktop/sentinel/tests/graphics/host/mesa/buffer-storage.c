#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glcorearb.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define GL_FUNCTIONS(X) \
 X(glGetString, PFNGLGETSTRINGPROC) \
 X(glGetStringi, PFNGLGETSTRINGIPROC) \
 X(glGetIntegerv, PFNGLGETINTEGERVPROC) \
 X(glGetError, PFNGLGETERRORPROC) \
 X(glGenBuffers, PFNGLGENBUFFERSPROC) \
 X(glDeleteBuffers, PFNGLDELETEBUFFERSPROC) \
 X(glBindBuffer, PFNGLBINDBUFFERPROC) \
 X(glBufferData, PFNGLBUFFERDATAPROC) \
 X(glBufferSubData, PFNGLBUFFERSUBDATAPROC) \
 X(glBufferStorage, PFNGLBUFFERSTORAGEPROC) \
 X(glMapBufferRange, PFNGLMAPBUFFERRANGEPROC) \
 X(glUnmapBuffer, PFNGLUNMAPBUFFERPROC) \
 X(glFlushMappedBufferRange, PFNGLFLUSHMAPPEDBUFFERRANGEPROC) \
 X(glCopyBufferSubData, PFNGLCOPYBUFFERSUBDATAPROC) \
 X(glGetBufferSubData, PFNGLGETBUFFERSUBDATAPROC) \
 X(glGetBufferParameteriv, PFNGLGETBUFFERPARAMETERIVPROC) \
 X(glFenceSync, PFNGLFENCESYNCPROC) \
 X(glClientWaitSync, PFNGLCLIENTWAITSYNCPROC) \
 X(glDeleteSync, PFNGLDELETESYNCPROC) \
 X(glFlush, PFNGLFLUSHPROC) \
 X(glMemoryBarrier, PFNGLMEMORYBARRIERPROC)
#define DECL(name, type) static type name;
GL_FUNCTIONS(DECL)
#undef DECL

static int fail(const char *what, unsigned line)
{
   fprintf(stderr, "FAIL line %u: %s\n", line, what);
   return 0;
}
#define REQUIRE(cond) do { if (!(cond)) return fail(#cond, __LINE__); } while (0)
#define CLEAN() REQUIRE(glGetError() == GL_NO_ERROR)

static uint64_t now_ns(void)
{
   struct timespec t;
   clock_gettime(CLOCK_MONOTONIC, &t);
   return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

static int fence_wait(int polling)
{
   GLsync fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
   REQUIRE(fence);
   glFlush();
   uint64_t deadline = now_ns() + 5000000000ull;
   GLenum result;
   do {
      result = glClientWaitSync(fence, 0, polling ? 0 : 100000000ull);
   } while (result == GL_TIMEOUT_EXPIRED && now_ns() < deadline);
   glDeleteSync(fence);
   REQUIRE(result == GL_ALREADY_SIGNALED || result == GL_CONDITION_SATISFIED);
   CLEAN();
   return 1;
}

static int mapped_read(int coherent)
{
   GLuint buffers[2];
   uint32_t expected[8], zeros[8] = {0};
   GLbitfield flags = GL_MAP_READ_BIT | GL_MAP_PERSISTENT_BIT |
                      (coherent ? GL_MAP_COHERENT_BIT : 0);
   glGenBuffers(2, buffers);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
   glBufferData(GL_COPY_READ_BUFFER, sizeof(expected), zeros, GL_DYNAMIC_COPY);
   glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[1]);
   glBufferStorage(GL_COPY_WRITE_BUFFER, sizeof(expected), zeros, flags);
   volatile uint32_t *mapped = glMapBufferRange(GL_COPY_WRITE_BUFFER, 0,
                                               sizeof(expected), flags);
   REQUIRE(mapped);
   GLint actual = 0;
   glGetBufferParameteriv(GL_COPY_WRITE_BUFFER, GL_BUFFER_IMMUTABLE_STORAGE, &actual);
   REQUIRE(actual == GL_TRUE);
   glGetBufferParameteriv(GL_COPY_WRITE_BUFFER, GL_BUFFER_STORAGE_FLAGS, &actual);
   REQUIRE((GLbitfield)actual == flags);
   glGetBufferParameteriv(GL_COPY_WRITE_BUFFER, GL_BUFFER_ACCESS_FLAGS, &actual);
   REQUIRE((GLbitfield)actual == flags);
   for (unsigned round = 0; round < 20; round++) {
      for (unsigned i = 0; i < 8; i++)
         expected[i] = 0x12340000u + 19 * round + i;
      glBufferSubData(GL_COPY_READ_BUFFER, 0, sizeof(expected), expected);
      glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, 0,
                          sizeof(expected));
      if (!coherent)
         glMemoryBarrier(GL_CLIENT_MAPPED_BUFFER_BARRIER_BIT);
      REQUIRE(fence_wait(round & 1));
      for (unsigned i = 0; i < 8; i++) {
         uint32_t value = mapped[i];
         if (value != expected[i]) {
            fprintf(stderr, "mapped coherent=%d round=%u word=%u got=%08x expected=%08x\n",
                    coherent, round, i, value, expected[i]);
            return 0;
         }
      }
   }
   REQUIRE(glUnmapBuffer(GL_COPY_WRITE_BUFFER));
   glGetBufferParameteriv(GL_COPY_WRITE_BUFFER, GL_BUFFER_MAPPED, &actual);
   REQUIRE(actual == GL_FALSE);
   glDeleteBuffers(2, buffers);
   CLEAN();
   printf("PASS 20 x 32-byte persistent READ, %s, blocking/poll fences, original pointer\n",
          coherent ? "coherent plain fence" : "CLIENT_MAPPED barrier");
   return 1;
}

static int partial_flush(void)
{
   GLuint buffers[2];
   unsigned char expected[64], output[64];
   memset(expected, 0xa5, sizeof(expected));
   glGenBuffers(2, buffers);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
   glBufferStorage(GL_COPY_READ_BUFFER, sizeof(expected), expected,
                   GL_MAP_WRITE_BIT | GL_MAP_PERSISTENT_BIT);
   volatile unsigned char *mapped = glMapBufferRange(GL_COPY_READ_BUFFER, 16, 32,
      GL_MAP_WRITE_BIT | GL_MAP_PERSISTENT_BIT | GL_MAP_FLUSH_EXPLICIT_BIT);
   REQUIRE(mapped);
   glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[1]);
   glBufferData(GL_COPY_WRITE_BUFFER, sizeof(output), NULL, GL_DYNAMIC_COPY);
   mapped[3] = 0x11;
   mapped[11] = 0x22;
   glFlushMappedBufferRange(GL_COPY_READ_BUFFER, 3, 1);
   glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 19, 0, 1);
   REQUIRE(fence_wait(0));
   glGetBufferSubData(GL_COPY_WRITE_BUFFER, 0, 1, output);
   REQUIRE(output[0] == 0x11);
   glFlushMappedBufferRange(GL_COPY_READ_BUFFER, 11, 1);
   glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, 0, sizeof(output));
   REQUIRE(fence_wait(1));
   glGetBufferSubData(GL_COPY_WRITE_BUFFER, 0, sizeof(output), output);
   expected[19] = 0x11;
   expected[27] = 0x22;
   REQUIRE(memcmp(expected, output, sizeof(output)) == 0);
   REQUIRE(glUnmapBuffer(GL_COPY_READ_BUFFER));
   glDeleteBuffers(2, buffers);
   CLEAN();
   puts("PASS explicit partial flush, nonzero mapping offset, persistent consumer, guards");
   return 1;
}

static int cpu_revert(void)
{
   GLuint buffers[3];
   uint32_t zero = 0, seven = 7, actual;
   GLbitfield flags = GL_MAP_READ_BIT | GL_MAP_WRITE_BIT |
                      GL_MAP_PERSISTENT_BIT | GL_MAP_COHERENT_BIT;
   glGenBuffers(3, buffers);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
   glBufferData(GL_COPY_READ_BUFFER, 4, &seven, GL_DYNAMIC_COPY);
   glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[1]);
   glBufferStorage(GL_COPY_WRITE_BUFFER, 4, &zero, flags);
   volatile uint32_t *mapped = glMapBufferRange(GL_COPY_WRITE_BUFFER, 0, 4, flags);
   REQUIRE(mapped);
   glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[2]);
   glBufferData(GL_COPY_WRITE_BUFFER, 4, NULL, GL_DYNAMIC_COPY);
   for (unsigned round = 0; round < 20; round++) {
      glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
      glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[1]);
      glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, 0, 4);
      REQUIRE(fence_wait(round & 1));
      REQUIRE(*mapped == seven);
      *mapped = zero;
      glBindBuffer(GL_COPY_READ_BUFFER, buffers[1]);
      glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[2]);
      glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, 0, 4);
      REQUIRE(fence_wait(round & 1));
      actual = UINT32_MAX;
      glGetBufferSubData(GL_COPY_WRITE_BUFFER, 0, 4, &actual);
      REQUIRE(actual == zero);
   }
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[1]);
   REQUIRE(glUnmapBuffer(GL_COPY_READ_BUFFER));
   glDeleteBuffers(3, buffers);
   CLEAN();
   puts("PASS 20 x GPU 7 -> coherent pointer -> CPU 0 -> GPU consumer, no remapping");
   return 1;
}

static int transient_access(void)
{
   GLuint buffer;
   unsigned char expected[128], actual[128];
   memset(expected, 0xa5, sizeof(expected));
   GLbitfield access = GL_MAP_READ_BIT | GL_MAP_WRITE_BIT |
                       GL_MAP_PERSISTENT_BIT | GL_MAP_COHERENT_BIT;
   glGenBuffers(1, &buffer);
   glBindBuffer(GL_COPY_READ_BUFFER, buffer);
   glBufferStorage(GL_COPY_READ_BUFFER, sizeof(expected), expected,
                   access | GL_DYNAMIC_STORAGE_BIT);
   volatile unsigned char *mapped = glMapBufferRange(GL_COPY_READ_BUFFER,
      0, sizeof(expected), access);
   REQUIRE(mapped);
   mapped[99] = expected[99] = 0x33;
   unsigned char value = expected[13] = 0x72;
   glBufferSubData(GL_COPY_READ_BUFFER, 13, 1, &value);
   CLEAN();
   REQUIRE(fence_wait(0));
   for (unsigned i = 0; i < sizeof(expected); i++)
      REQUIRE(mapped[i] == expected[i]);
   glGetBufferSubData(GL_COPY_READ_BUFFER, 0, sizeof(actual), actual);
   CLEAN();
   REQUIRE(!memcmp(actual, expected, sizeof(expected)));
   GLint active = 0;
   glGetBufferParameteriv(GL_COPY_READ_BUFFER, GL_BUFFER_MAPPED, &active);
   REQUIRE(active == GL_TRUE);
   REQUIRE(glUnmapBuffer(GL_COPY_READ_BUFFER));

   unsigned char *temporary = glMapBufferRange(GL_COPY_READ_BUFFER,
      17, 31, GL_MAP_READ_BIT | GL_MAP_WRITE_BIT);
   REQUIRE(temporary);
   REQUIRE(!memcmp(temporary, expected + 17, 31));
   temporary[2] = expected[19] = 0x44;
   REQUIRE(glUnmapBuffer(GL_COPY_READ_BUFFER));
   REQUIRE(fence_wait(1));
   glGetBufferSubData(GL_COPY_READ_BUFFER, 0, sizeof(actual), actual);
   CLEAN();
   REQUIRE(!memcmp(actual, expected, sizeof(expected)));
   glDeleteBuffers(1, &buffer);
   CLEAN();
   puts("PASS subdata/get while persistently mapped, disjoint coherent CPU write, temporary READWRITE map and guards");
   return 1;
}

static int retained_fence(void)
{
   GLuint buffers[2];
   uint32_t value = 0x92;
   glGenBuffers(2, buffers);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
   glBufferData(GL_COPY_READ_BUFFER, sizeof(value), &value, GL_DYNAMIC_COPY);
   glBindBuffer(GL_COPY_WRITE_BUFFER, buffers[1]);
   glBufferData(GL_COPY_WRITE_BUFFER, sizeof(value), NULL, GL_DYNAMIC_COPY);
   GLsync old[64];
   for (unsigned round = 0; round < 64; round++) {
      glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, 0, sizeof(value));
      old[round] = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
      REQUIRE(old[round]);
      REQUIRE(fence_wait(0));
   }
   for (unsigned round = 0; round < 64; round++) {
      value++;
      glBufferSubData(GL_COPY_READ_BUFFER, 0, sizeof(value), &value);
      glCopyBufferSubData(GL_COPY_READ_BUFFER, GL_COPY_WRITE_BUFFER, 0, 0, sizeof(value));
      glFlush();
      REQUIRE(glClientWaitSync(old[round], 0, 0) == GL_ALREADY_SIGNALED);
      glDeleteSync(old[round]);
      REQUIRE(fence_wait(round & 1));
   }
   glDeleteBuffers(2, buffers);
   CLEAN();
   puts("PASS retained completed fence remains immediately signaled during 64 later submissions");
   return 1;
}

static int invalid_flags(void)
{
   GLuint buffers[3];
   glGenBuffers(3, buffers);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
   glBufferStorage(GL_COPY_READ_BUFFER, 32, NULL, GL_MAP_READ_BIT | GL_MAP_COHERENT_BIT);
   REQUIRE(glGetError() == GL_INVALID_VALUE);
   glBufferStorage(GL_COPY_READ_BUFFER, 32, NULL, GL_MAP_READ_BIT | GL_MAP_PERSISTENT_BIT);
   REQUIRE(!glMapBufferRange(GL_COPY_READ_BUFFER, 0, 32,
      GL_MAP_READ_BIT | GL_MAP_PERSISTENT_BIT | GL_MAP_COHERENT_BIT));
   REQUIRE(glGetError() == GL_INVALID_OPERATION);
   REQUIRE(glMapBufferRange(GL_COPY_READ_BUFFER, 0, 32, GL_MAP_READ_BIT | GL_MAP_PERSISTENT_BIT));
   REQUIRE(glUnmapBuffer(GL_COPY_READ_BUFFER));
   glBufferData(GL_COPY_READ_BUFFER, 32, NULL, GL_DYNAMIC_COPY);
   REQUIRE(glGetError() == GL_INVALID_OPERATION);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[1]);
   glBufferData(GL_COPY_READ_BUFFER, 32, NULL, GL_DYNAMIC_COPY);
   REQUIRE(!glMapBufferRange(GL_COPY_READ_BUFFER, 0, 32, GL_MAP_READ_BIT | GL_MAP_PERSISTENT_BIT));
   REQUIRE(glGetError() == GL_INVALID_OPERATION);
   glBindBuffer(GL_COPY_READ_BUFFER, buffers[2]);
   glBufferStorage(GL_COPY_READ_BUFFER, 32, NULL, GL_MAP_PERSISTENT_BIT);
   REQUIRE(glGetError() == GL_INVALID_VALUE);
   glDeleteBuffers(3, buffers);
   CLEAN();
   puts("PASS storage/map flag validation, immutable redefinition errors, mapping recovery");
   return 1;
}

int main(int argc, char **argv)
{
   setvbuf(stdout, NULL, _IONBF, 0);
   if (argc != 2 || getenv("MESA_GL_VERSION_OVERRIDE") ||
       getenv("MESA_GLSL_VERSION_OVERRIDE") || getenv("MESA_EXTENSION_OVERRIDE")) {
      fputs("Usage: gl-buffer-storage /absolute/libEGL; no version/extension overrides\n", stderr);
      return 2;
   }
   void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
   if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 2; }
#define EGL_LOAD(name, type) type name = (type)dlsym(lib, #name); if (!name) return 2
   EGL_LOAD(eglGetPlatformDisplay, PFNEGLGETPLATFORMDISPLAYPROC);
   EGL_LOAD(eglInitialize, PFNEGLINITIALIZEPROC);
   EGL_LOAD(eglBindAPI, PFNEGLBINDAPIPROC);
   EGL_LOAD(eglChooseConfig, PFNEGLCHOOSECONFIGPROC);
   EGL_LOAD(eglCreateContext, PFNEGLCREATECONTEXTPROC);
   EGL_LOAD(eglCreatePbufferSurface, PFNEGLCREATEPBUFFERSURFACEPROC);
   EGL_LOAD(eglMakeCurrent, PFNEGLMAKECURRENTPROC);
   EGL_LOAD(eglGetProcAddress, PFNEGLGETPROCADDRESSPROC);
   EGL_LOAD(eglDestroyContext, PFNEGLDESTROYCONTEXTPROC);
   EGL_LOAD(eglDestroySurface, PFNEGLDESTROYSURFACEPROC);
   EGL_LOAD(eglTerminate, PFNEGLTERMINATEPROC);
#undef EGL_LOAD
   EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
   if (!eglInitialize(display, NULL, NULL) || !eglBindAPI(EGL_OPENGL_API)) return 2;
   EGLint config_attrs[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
      EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8,
      EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE };
   EGLConfig config;
   EGLint count;
   if (!eglChooseConfig(display, config_attrs, &config, 1, &count) || !count) return 2;
   EGLint surface_attrs[] = { EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE };
   EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attrs);
   EGLint context_attrs[] = { EGL_CONTEXT_MAJOR_VERSION, 4,
      EGL_CONTEXT_MINOR_VERSION, 3, EGL_CONTEXT_OPENGL_PROFILE_MASK,
      EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE };
   EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attrs);
   if (context == EGL_NO_CONTEXT || !eglMakeCurrent(display, surface, surface, context)) return 2;
   glGetString = (PFNGLGETSTRINGPROC)eglGetProcAddress("glGetString");
   glGetStringi = (PFNGLGETSTRINGIPROC)eglGetProcAddress("glGetStringi");
   glGetIntegerv = (PFNGLGETINTEGERVPROC)eglGetProcAddress("glGetIntegerv");
   if (!glGetString || !glGetStringi || !glGetIntegerv) return 2;
   const char *renderer = (const char *)glGetString(GL_RENDERER);
   printf("GL_VERSION=%s\nGL_RENDERER=%s\n", glGetString(GL_VERSION), renderer);
   GLint major = 0, minor = 0, extensions = 0;
   glGetIntegerv(GL_MAJOR_VERSION, &major);
   glGetIntegerv(GL_MINOR_VERSION, &minor);
   glGetIntegerv(GL_NUM_EXTENSIONS, &extensions);
   int supported = major > 4 || (major == 4 && minor >= 4);
   for (GLint i = 0; i < extensions; i++)
      supported |= !strcmp((const char *)glGetStringi(GL_EXTENSIONS, i), "GL_ARB_buffer_storage");
   int result = 77;
   if (!supported) {
      puts("SKIP: genuine ARB_buffer_storage/core 4.4 support absent; no storage calls issued");
   } else if (!renderer || strstr(renderer, "llvmpipe") || strstr(renderer, "softpipe") ||
              strstr(renderer, "SwiftShader")) {
      fputs("FAIL: software renderer is not this hardware acceptance gate\n", stderr);
      result = 1;
   } else {
#define LOAD(name, type) name = (type)eglGetProcAddress(#name); if (!name) return 2;
      GL_FUNCTIONS(LOAD)
#undef LOAD
      int ok = invalid_flags() && mapped_read(0) && mapped_read(1) &&
               partial_flush() && cpu_revert() && transient_access() && retained_fence();
      result = ok ? 0 : 1;
      if (ok) puts("PASS bounded real GL buffer-storage API gate (not full conformance)");
   }
   eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
   eglDestroyContext(display, context);
   eglDestroySurface(display, surface);
   eglTerminate(display);
   dlclose(lib);
   return result;
}
