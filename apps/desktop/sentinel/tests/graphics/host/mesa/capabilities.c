#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glcorearb.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
   if (argc != 2 || getenv("MESA_GL_VERSION_OVERRIDE") ||
       getenv("MESA_GLSL_VERSION_OVERRIDE") || getenv("MESA_EXTENSION_OVERRIDE"))
      return 2;
   void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
   if (!library) {
      fprintf(stderr, "%s\n", dlerror());
      return 2;
   }
#define LOAD(name, type) type name = (type)dlsym(library, #name); if (!name) return 2
   LOAD(eglGetPlatformDisplay, PFNEGLGETPLATFORMDISPLAYPROC);
   LOAD(eglInitialize, PFNEGLINITIALIZEPROC);
   LOAD(eglBindAPI, PFNEGLBINDAPIPROC);
   LOAD(eglChooseConfig, PFNEGLCHOOSECONFIGPROC);
   LOAD(eglCreateContext, PFNEGLCREATECONTEXTPROC);
   LOAD(eglMakeCurrent, PFNEGLMAKECURRENTPROC);
   LOAD(eglGetProcAddress, PFNEGLGETPROCADDRESSPROC);
   LOAD(eglDestroyContext, PFNEGLDESTROYCONTEXTPROC);
   LOAD(eglTerminate, PFNEGLTERMINATEPROC);
#undef LOAD
   EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
   if (!eglInitialize(display, NULL, NULL) || !eglBindAPI(EGL_OPENGL_API))
      return 2;
   const EGLint config_attributes[] = {
      EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
      EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE,
   };
   EGLConfig config;
   EGLint count;
   if (!eglChooseConfig(display, config_attributes, &config, 1, &count) || !count)
      return 2;
   const EGLint context_attributes[] = {
      EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 3,
      EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE,
   };
   EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
   if (context == EGL_NO_CONTEXT ||
       !eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context))
      return 2;
   PFNGLGETSTRINGPROC get_string = (PFNGLGETSTRINGPROC)eglGetProcAddress("glGetString");
   PFNGLGETINTEGERVPROC get_integer = (PFNGLGETINTEGERVPROC)eglGetProcAddress("glGetIntegerv");
   PFNGLGETERRORPROC get_error = (PFNGLGETERRORPROC)eglGetProcAddress("glGetError");
   if (!get_string || !get_integer || !get_error)
      return 2;
   const char *renderer = (const char *)get_string(GL_RENDERER);
   if (!renderer || !strstr(renderer, "zink") || !strstr(renderer, "KOSMICKRISP"))
      return 1;
   printf("GL_VERSION=%s\nGL_RENDERER=%s\n", get_string(GL_VERSION), renderer);
   struct limit {
      GLenum name;
      const char *label;
      GLint minimum;
      GLint maximum;
   } limits[] = {
      {GL_MAX_VERTEX_SHADER_STORAGE_BLOCKS, "vertex SSBO", 8, 2147483647},
      {GL_MAX_TESS_CONTROL_SHADER_STORAGE_BLOCKS, "tess-control SSBO", 8, 2147483647},
      {GL_MAX_TESS_EVALUATION_SHADER_STORAGE_BLOCKS, "tess-evaluation SSBO", 8, 2147483647},
      {GL_MAX_GEOMETRY_SHADER_STORAGE_BLOCKS, "geometry SSBO", 8, 2147483647},
      {GL_MAX_VERTEX_IMAGE_UNIFORMS, "vertex image", 8, 2147483647},
      {GL_MAX_TESS_CONTROL_IMAGE_UNIFORMS, "tess-control image", 8, 2147483647},
      {GL_MAX_TESS_EVALUATION_IMAGE_UNIFORMS, "tess-evaluation image", 8, 2147483647},
      {GL_MAX_GEOMETRY_IMAGE_UNIFORMS, "geometry image", 8, 2147483647},
      {GL_MAX_FRAGMENT_SHADER_STORAGE_BLOCKS, "fragment SSBO", 8, 2147483647},
      {GL_MAX_COMPUTE_SHADER_STORAGE_BLOCKS, "compute SSBO", 8, 2147483647},
      {GL_MAX_FRAGMENT_IMAGE_UNIFORMS, "fragment image", 8, 2147483647},
      {GL_MAX_COMPUTE_IMAGE_UNIFORMS, "compute image", 8, 2147483647},
   };
   int status = 0;
   for (unsigned i = 0; i < sizeof(limits) / sizeof(limits[0]); i++) {
      GLint value = -1;
      get_integer(limits[i].name, &value);
      printf("%s=%d\n", limits[i].label, value);
      if (get_error() != GL_NO_ERROR || value < limits[i].minimum || value > limits[i].maximum)
         status = 1;
   }
   eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
   eglDestroyContext(display, context);
   eglTerminate(display);
   dlclose(library);
   if (!status)
      puts("PASS honest stage-memory capability limits");
   return status;
}
