/* Run in a disposable workspace with the provisioned desktop environment. */
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glext.h>
#include <GL/glx.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
  const int width = argc > 1 ? atoi(argv[1]) : 640;
  const int height = argc > 2 ? atoi(argv[2]) : 480;
  if (width < 1 || height < 1 || width > 2048 || height > 2048) return 1;
  Display *d = XOpenDisplay(NULL);
  if (!d) return 1;
  int attributes[] = {GLX_RGBA, GLX_RED_SIZE, 8, GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8, GLX_DOUBLEBUFFER, None};
  XVisualInfo *visual = glXChooseVisual(d, DefaultScreen(d), attributes);
  if (!visual) return 2;
  XSetWindowAttributes wa = {.override_redirect = True, .colormap = XCreateColormap(d, RootWindow(d, visual->screen), visual->visual, AllocNone)};
  Window window = XCreateWindow(d, RootWindow(d, visual->screen), 0, 0, width, height, 0, visual->depth, InputOutput, visual->visual, CWColormap | CWBorderPixel | CWOverrideRedirect, &wa);
  XMapWindow(d, window); XSync(d, False);
  GLXContext context = glXCreateContext(d, visual, NULL, True);
  if (!glXMakeCurrent(d, window, context)) return 3;
  const char *renderer = (const char *)glGetString(GL_RENDERER);
  printf("Renderer: %s\nVersion: %s\n", renderer, glGetString(GL_VERSION));
  if (!strstr(renderer, "virgl")) return 4;
  glViewport(0, 0, width, height);
  const char *source = "#version 120\nvoid main(){gl_FragColor=vec4(mod(floor(gl_FragCoord.x),256.0)/255.0,mod(floor(gl_FragCoord.y),256.0)/255.0,0.25,1.0);}";
  GLuint shader = glCreateShader(GL_FRAGMENT_SHADER);
  glShaderSource(shader, 1, &source, NULL); glCompileShader(shader);
  GLint valid; glGetShaderiv(shader, GL_COMPILE_STATUS, &valid);
  if (!valid) return 5;
  GLuint program = glCreateProgram(); glAttachShader(program, shader); glLinkProgram(program);
  glGetProgramiv(program, GL_LINK_STATUS, &valid);
  if (!valid) return 6;
  glUseProgram(program);
  glBegin(GL_TRIANGLES); glVertex2f(-1, -1); glVertex2f(3, -1); glVertex2f(-1, 3); glEnd();
  unsigned char *frame = malloc(width * height * 4);
  glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, frame);
  glXSwapBuffers(d, window); XSync(d, False);
  XImage *image = XGetImage(d, window, 0, 0, width, height, AllPlanes, ZPixmap);
  unsigned bad = 0; unsigned max_delta = 0;
  printf("Visual depth=%d masks=%lx,%lx,%lx\n",visual->depth,visual->red_mask,visual->green_mask,visual->blue_mask);
  for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) {
    unsigned char *pixel = frame + ((height - 1 - y) * width + x) * 4;
    unsigned long expected = ((unsigned long)pixel[0] << 16) | ((unsigned long)pixel[1] << 8) | pixel[2];
    unsigned long actual = XGetPixel(image, x, y) & 0xffffff;
    if (actual != expected) {
      if (bad < 4) printf("pixel %d,%d actual=%06lx expected=%06lx\n",x,y,actual,expected);
      for (int shift=0;shift<24;shift+=8) {unsigned delta=abs((int)((actual>>shift)&255)-(int)((expected>>shift)&255));if(delta>max_delta)max_delta=delta;}
      bad++;
    }
  }
  printf("Maximum channel delta: %u\n",max_delta);
  printf("Presentation mismatches: %u / %d; GL error: %x\n", bad, width * height, glGetError());
  XDestroyImage(image); free(frame); glXMakeCurrent(d, None, NULL);
  glXDestroyContext(d, context); XDestroyWindow(d, window); XCloseDisplay(d);
  return bad ? 7 : 0;
}
