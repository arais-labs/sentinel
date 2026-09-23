#include <epoxy/egl.h>
#include <epoxy/gl.h>
#include <stdio.h>
#include <stdlib.h>

#define REQUIRE(expression) do { \
   if (!(expression)) { \
      fprintf(stderr, "FAILED %s at %d\n", #expression, __LINE__); \
      abort(); \
   } \
} while (0)

static void check_pixel(const char *stage)
{
   unsigned char pixel[4];
   glReadPixels(32, 16, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
   REQUIRE(glGetError() == GL_NO_ERROR);
   printf("%s %u,%u,%u,%u\n", stage, pixel[0], pixel[1], pixel[2], pixel[3]);
   REQUIRE(pixel[0] == 255 && pixel[1] == 0 && pixel[2] == 0 && pixel[3] == 255);
}

int main(void)
{
   if (getenv("MESA_GL_VERSION_OVERRIDE") || getenv("MESA_GLSL_VERSION_OVERRIDE") ||
       getenv("MESA_EXTENSION_OVERRIDE"))
      return 2;
   setvbuf(stdout, NULL, _IONBF, 0);
   EGLDisplay display = eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
   REQUIRE(eglInitialize(display, NULL, NULL));
   REQUIRE(eglBindAPI(EGL_OPENGL_API));
   const EGLint config_attributes[] = {
      EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
      EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE,
   };
   EGLConfig config;
   EGLint count;
   REQUIRE(eglChooseConfig(display, config_attributes, &config, 1, &count) && count);
   const EGLint context_attributes[] = {
      EGL_CONTEXT_MAJOR_VERSION_KHR, 4, EGL_CONTEXT_MINOR_VERSION_KHR, 3,
      EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,
      EGL_NONE,
   };
   EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
   REQUIRE(context != EGL_NO_CONTEXT);
   REQUIRE(eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context));
   printf("Renderer: %s\n", glGetString(GL_RENDERER));

   for (unsigned mip = 0; mip < 3; mip++) {
      for (unsigned use_view = 0; use_view < 2; use_view++) {
         printf("CASE mip=%u view=%u\n", mip, use_view);
         GLuint textures[3], framebuffers[2];
         glGenTextures(3, textures);
         glGenFramebuffers(2, framebuffers);
         glBindTexture(GL_TEXTURE_2D, textures[0]);
         glTexStorage2D(GL_TEXTURE_2D, mip + 1, GL_RGBA8, 64 << mip, 32 << mip);
         GLuint source = textures[0];
         unsigned level = mip;
         if (use_view) {
            glTextureView(textures[1], GL_TEXTURE_2D, textures[0], GL_RGBA8, mip, 1, 0, 1);
            source = textures[1];
            level = 0;
         }
         glBindFramebuffer(GL_FRAMEBUFFER, framebuffers[0]);
         glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                                source, level);
         REQUIRE(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE);
         glClearColor(1, 0, 0, 1);
         glClear(GL_COLOR_BUFFER_BIT);
         check_pixel("source");
         glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                                textures[0], mip);
         check_pixel("storage same mip");
         glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                                source, level);
         glBindTexture(GL_TEXTURE_2D, textures[2]);
         glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, 128, 64);
         glBindFramebuffer(GL_FRAMEBUFFER, framebuffers[1]);
         glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                                textures[2], 0);
         REQUIRE(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE);
         glClearColor(0, 0, 1, 1);
         glClear(GL_COLOR_BUFFER_BIT);
         glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffers[0]);
         glBlitFramebuffer(0, 0, 64, 32, 0, 0, 128, 64, GL_COLOR_BUFFER_BIT, GL_NEAREST);
         glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffers[1]);
         check_pixel("scaled blit");
         glDeleteFramebuffers(2, framebuffers);
         glDeleteTextures(3, textures);
      }
   }

   REQUIRE(eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT));
   REQUIRE(eglDestroyContext(display, context));
   REQUIRE(eglTerminate(display));
   return 0;
}
