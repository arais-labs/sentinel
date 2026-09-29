"""Compile the actual patched kernel mode function with mocked DRM lists.

Usage: python3 tests/graphics/kernel/mode-advertisement.py PRISTINE_LINUX_TREE
This source contract test does not substitute for guest KMS timing.
"""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

DESKTOP = Path(__file__).resolve().parents[3]
source = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="sentinel-kernel-modes-") as temporary:
    root = Path(temporary)
    target = root / "drivers/gpu/drm/virtio"
    target.mkdir(parents=True)
    for name in ("virtgpu_display.c", "virtgpu_drv.h"):
        shutil.copy2(source / "drivers/gpu/drm/virtio" / name, target / name)
    subprocess.run(
        [
            os.environ.get("PATCH", "patch"),
            "--batch",
            "--forward",
            "--fuzz=0",
            "-p1",
            "-i",
            str(DESKTOP / "native/graphics/patches/kernel-virtio-vblank.patch"),
        ],
        cwd=root,
        check=True,
    )
    header = subprocess.check_output(
        [
            sys.executable,
            str(DESKTOP / "scripts/packaging/graphics/generate-display-modes.py"),
            str(DESKTOP / "native/graphics/display/modes.json"),
        ],
        text=True,
    )
    (root / "sentinel_display_modes.h").write_text(header)
    patched = (target / "virtgpu_display.c").read_text()
    begin = patched.index("static int virtio_gpu_add_display_mode(")
    end = patched.index("static enum drm_mode_status", begin)
    function = patched[begin:end]
    harness = r"""
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include "sentinel_display_modes.h"
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define XRES_DEF 1024
#define YRES_DEF 768
#define XRES_MAX 8192
#define YRES_MAX 8192
#define DRM_MODE_TYPE_PREFERRED 8
#define DRM_DEBUG(...) ((void)0)
#define le32_to_cpu(x) (x)
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))
#define list_for_each_entry(pos, headptr, member) \
    for (struct list_head *_n = (headptr)->next; \
         _n != (headptr) && ((pos) = container_of(_n, __typeof__(*(pos)), member), 1); _n = _n->next)
#define list_for_each_entry_safe(pos, nxt, headptr, member) \
    for (struct list_head *_n = (headptr)->next, *_next = _n->next; \
         _n != (headptr) && ((pos) = container_of(_n, __typeof__(*(pos)), member), \
         (nxt) = _next == (headptr) ? NULL : container_of(_next, __typeof__(*(nxt)), member), (void)(nxt), 1); \
         _n = _next, _next = _n->next)
static void list_add_tail(struct list_head *node, struct list_head *head)
{ node->prev = head->prev; node->next = head; head->prev->next = node; head->prev = node; }
static void list_del_init(struct list_head *node)
{ node->next->prev = node->prev; node->prev->next = node->next; node->next = node->prev = node; }
struct drm_connector { void *dev; struct list_head probed_modes; };
struct drm_display_mode { int type, hdisplay, vdisplay, refresh, rb; struct list_head head; };
struct virtio_gpu_output { struct { struct { int width, height; } r; } info; };
static struct virtio_gpu_output output;
static int edid, fallback, cvt, added, destroyed, allocation_failures;
static bool fail_all, matching_fallback;
static struct virtio_gpu_output *drm_connector_to_virtio_gpu_output(struct drm_connector *c)
{ (void)c; return &output; }
static int drm_edid_connector_add_modes(struct drm_connector *c)
{ (void)c; return edid; }
static struct drm_display_mode *make_mode(int w, int h, int rate, int rb)
{
    struct drm_display_mode *m = calloc(1, sizeof(*m)); assert(m);
    m->hdisplay = w; m->vdisplay = h; m->refresh = rate; m->rb = rb;
    m->head.next = m->head.prev = &m->head; return m;
}
static int drm_add_modes_noedid(struct drm_connector *c, int w, int h)
{
    assert(w == XRES_MAX && h == YRES_MAX); fallback++;
    /* Duplicate 1920 geometry and CVT-rounded 1366/1360 exercise dedup. */
    const int modes[][3] = {{1280,800,60},{1920,1200,60},{1920,1200,75},
                           {1366,768,60},{1360,768,60}};
    for (unsigned i = 0; i < sizeof(modes)/sizeof(modes[0]); i++) {
        struct drm_display_mode *m = make_mode(modes[i][0], modes[i][1], modes[i][2], 0);
        list_add_tail(&m->head, &c->probed_modes);
    }
    if (matching_fallback) {
        struct drm_display_mode *m = make_mode(1920,1200,120,1);
        list_add_tail(&m->head, &c->probed_modes);
    }
    return 5 + matching_fallback;
}
static struct drm_display_mode *drm_cvt_mode(void *dev, int w, int h, int rate,
                                           bool reduced, bool interlace, bool margins)
{
    (void)dev; cvt++; assert(cvt < 40);
    assert(rate == 120 && reduced && !interlace && !margins);
    if (fail_all) return NULL;
    if (allocation_failures) { allocation_failures--; return NULL; }
    return make_mode(w & ~7, h, rate, reduced);
}
static bool drm_mode_equal(const struct drm_display_mode *a, const struct drm_display_mode *b)
{ return a->hdisplay == b->hdisplay && a->vdisplay == b->vdisplay && a->refresh == b->refresh && a->rb == b->rb; }
static void drm_mode_destroy(void *dev, struct drm_display_mode *m)
{ (void)dev; destroyed++; free(m); }
static void drm_mode_probed_add(struct drm_connector *c, struct drm_display_mode *m)
{ added++; list_add_tail(&m->head, &c->probed_modes); }
"""
    harness += function
    harness += r"""
static void check(int w, int h, int edid_count, bool all_fail, int fail_count, bool match)
{
    struct drm_connector c = {0};
    c.probed_modes.next = c.probed_modes.prev = &c.probed_modes;
    output.info.r.width = w; output.info.r.height = h;
    edid = edid_count; fail_all = all_fail; allocation_failures = fail_count;
    matching_fallback = match; fallback = cvt = added = destroyed = 0;
    int count = virtio_gpu_conn_get_modes(&c);
    if (edid_count) {
        assert(count == edid_count && !fallback && !cvt && !added);
        assert(c.probed_modes.next == &c.probed_modes); return;
    }
    assert(fallback == 1 && count == 5 + match + added);
    int total = 0, preferred = 0, initial120 = 0, resized120 = 0, rounded120 = 0, old_modes = 0;
    int ew = w && h ? w : XRES_DEF, eh = w && h ? h : YRES_DEF;
    struct drm_display_mode *m, *other, *next;
    list_for_each_entry(m, &c.probed_modes, head) {
        total++;
        if (m->refresh != 120) old_modes++;
        if (m->type & DRM_MODE_TYPE_PREFERRED) {
            preferred++; assert(m->hdisplay == (ew & ~7) && m->vdisplay == eh && m->refresh == 120);
        }
        if (m->refresh == 120) {
            initial120 += m->hdisplay == 1280 && m->vdisplay == 800;
            resized120 += m->hdisplay == 1920 && m->vdisplay == 1200;
            rounded120 += m->hdisplay == 1360 && m->vdisplay == 768;
            list_for_each_entry(other, &c.probed_modes, head)
                assert(other == m || !drm_mode_equal(other, m));
        }
    }
    assert(total == count && old_modes == 5);
    if (all_fail) assert(added == 0 && preferred == 0);
    else {
        assert(preferred == 1);
        assert(initial120 == 1 && resized120 == 1 && rounded120 == 0);
        for (unsigned i = 0; i < ARRAY_SIZE(sentinel_display_modes); i++) {
            int found = 0;
            list_for_each_entry(m, &c.probed_modes, head)
                found += m->hdisplay == (int)sentinel_display_modes[i].width
                      && m->vdisplay == (int)sentinel_display_modes[i].height
                      && m->refresh == SENTINEL_DISPLAY_REFRESH_HZ;
            assert(found == 1);
        }
        if (match) assert(added == (int)ARRAY_SIZE(sentinel_display_modes) - 1);
    }
    list_for_each_entry_safe(m, next, &c.probed_modes, head) {
        list_del_init(&m->head); free(m);
    }
}
int main(void)
{
    check(1280,800,0,false,0,false);
    check(1920,1200,0,false,0,false);
    check(2880,1800,0,false,0,false);
    check(3840,2400,0,false,0,false);
    check(1600,1000,0,false,0,false);
    check(0,0,0,false,0,false);
    check(1920,0,0,false,0,false);
    check(1280,800,3,false,0,false);
    check(1280,800,0,true,0,false);
    check(1280,800,0,false,1,false);
    check(1280,800,0,false,0,true);
    check(1920,1200,0,false,0,true);
    puts("PASS: 12 native virtual-monitor mode advertisement cases");
}
"""
    test = root / "mode-contract.c"
    test.write_text(harness)
    binary = root / "mode-contract"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(test), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
