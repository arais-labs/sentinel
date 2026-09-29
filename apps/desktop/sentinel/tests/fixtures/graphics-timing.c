/* Run inside an otherwise idle disposable X11 workspace, not timing-sensitive CI.
 * cc graphics-timing.c -lGL -lX11 -o graphics-timing && ./graphics-timing 120
 * Measures real swaps and the DRM-derived clock; never forces an application FPS.
 */
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glx.h>
#include <GL/glxext.h>
#include <X11/Xlib.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(test) do { if (!(test)) { fprintf(stderr, "Failed: %s\n", #test); return 1; } } while (0)

static double seconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec + now.tv_nsec / 1e9;
}

static int compare_double(const void *a, const void *b) {
    const double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void report(const char *name, double *samples, unsigned count) {
    double total = 0;
    for (unsigned i = 0; i < count; i++) total += samples[i];
    qsort(samples, count, sizeof(*samples), compare_double);
    printf("%s ms: mean %.3f p50 %.3f p95 %.3f max %.3f\n", name,
        total / count * 1000, samples[count / 2] * 1000,
        samples[count * 95 / 100] * 1000, samples[count - 1] * 1000);
}

int main(int argc, char **argv) {
    const double expected = argc == 2 ? atof(argv[1]) : 120;
    CHECK(expected > 0);
    Display *display = XOpenDisplay(NULL);
    CHECK(display);
    int attributes[] = {GLX_RGBA, GLX_DOUBLEBUFFER, None};
    XVisualInfo *visual = glXChooseVisual(display, DefaultScreen(display), attributes);
    CHECK(visual);
    XSetWindowAttributes wa = {.override_redirect = True,
        .colormap = XCreateColormap(display, RootWindow(display, visual->screen), visual->visual, AllocNone)};
    Window window = XCreateWindow(display, RootWindow(display, visual->screen),
        0, 0, 256, 256, 0, visual->depth, InputOutput, visual->visual,
        CWColormap | CWOverrideRedirect, &wa);
    XMapWindow(display, window);
    XSync(display, False);
    GLXContext context = glXCreateContext(display, visual, NULL, True);
    CHECK(context && glXMakeCurrent(display, window, context));
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    CHECK(renderer && strstr(renderer, "virgl"));
    const char *extensions = glXQueryExtensionsString(display, visual->screen);
    CHECK(strstr(extensions, "GLX_OML_sync_control"));
    CHECK(strstr(extensions, "GLX_EXT_swap_control"));
    PFNGLXGETSYNCVALUESOMLPROC sync = (void *)glXGetProcAddress((void *)"glXGetSyncValuesOML");
    PFNGLXGETMSCRATEOMLPROC rate = (void *)glXGetProcAddress((void *)"glXGetMscRateOML");
    PFNGLXSWAPINTERVALEXTPROC interval = (void *)glXGetProcAddress((void *)"glXSwapIntervalEXT");
    int32_t numerator, denominator;
    CHECK(rate(display, window, &numerator, &denominator) && denominator > 0);
    const double hz = (double)numerator / denominator;
    CHECK(hz > expected * .99 && hz < expected * 1.01);
    interval(display, window, 1);
    int64_t first_ust = 0, first_msc = 0, ust = 0, msc = 0, sbc;
    double start = 0;
    double clear_times[240], swap_times[240], query_times[240], frame_times[240];
    unsigned msc_steps[5] = {0};
    for (unsigned frame = 0; frame <= 248; frame++) {
        double before = seconds();
        int64_t previous_msc = msc;
        glClearColor(frame % 2, .5, .75, 1);
        glClear(GL_COLOR_BUFFER_BIT);
        double cleared = seconds();
        glXSwapBuffers(display, window);
        double swapped = seconds();
        CHECK(sync(display, window, &ust, &msc, &sbc));
        double queried = seconds();
        if (frame > 8) {
            unsigned index = frame - 9;
            clear_times[index] = cleared - before;
            swap_times[index] = swapped - cleared;
            query_times[index] = queried - swapped;
            frame_times[index] = queried - before;
            int64_t step = msc - previous_msc;
            msc_steps[step < 0 || step >= 4 ? 4 : step]++;
        }
        if (frame == 8) { start = seconds(); first_ust = ust; first_msc = msc; }
    }
    const double fps = 240 / (seconds() - start);
    CHECK(ust > first_ust && msc > first_msc);
    const double clock_hz = (msc - first_msc) * 1e6 / (ust - first_ust);
    report("clear", clear_times, 240);
    report("swap", swap_times, 240);
    report("query", query_times, 240);
    report("whole-frame", frame_times, 240);
    printf("MSC steps 0/1/2/3/other: %u/%u/%u/%u/%u\n", msc_steps[0],
        msc_steps[1], msc_steps[2], msc_steps[3], msc_steps[4]);
    printf("%s: mode %.2f Hz, vblank %.2f Hz, swaps %.2f FPS\n", renderer, hz, clock_hz, fps);
    CHECK(clock_hz > hz * .99 && clock_hz < hz * 1.01);
    /* Catch double-vblank throttling, with room for ordinary scheduling jitter. */
    const int timing_ok = fps > hz * .85 && fps < hz * 1.15;
    CHECK(glGetError() == GL_NO_ERROR);
    glXMakeCurrent(display, None, NULL);
    glXDestroyContext(display, context);
    XDestroyWindow(display, window);
    XFreeColormap(display, wa.colormap);
    XFree(visual);
    XCloseDisplay(display);
    CHECK(timing_ok);
    return 0;
}
