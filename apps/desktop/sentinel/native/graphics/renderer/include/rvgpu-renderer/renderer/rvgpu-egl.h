#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <epoxy/egl.h>
#include <epoxy/gl.h>
#include <linux/virtio_gpu.h>
struct rvgpu_box { unsigned x,y,w,h; };
struct rvgpu_virgl_params { struct rvgpu_box box; uint32_t res_id,tex_id; struct rvgpu_box tex; int y0_top; };
struct rvgpu_scanout_params { struct rvgpu_box box; uint32_t id; bool enabled,boxed; };
struct rvgpu_fps_params { bool show_fps; double virgl_cmd_time_ms; };
struct rvgpu_scanout { struct rvgpu_virgl_params virgl; struct rvgpu_scanout_params params; struct rvgpu_fps_params fps_params; };
struct rvgpu_egl_state;
struct rvgpu_egl_callbacks {
  void (*set_cursor)(struct rvgpu_egl_state*,const struct virtio_gpu_update_cursor*,uint32_t,uint32_t,uint32_t,const void*);
  void (*move_cursor)(struct rvgpu_egl_state*,const struct virtio_gpu_update_cursor*);
  void (*process_wake)(struct rvgpu_egl_state*);
};
struct rvgpu_egl_state {
  struct rvgpu_scanout scanouts[VIRTIO_GPU_MAX_SCANOUTS];
  const struct rvgpu_egl_callbacks *cb;
  EGLDisplay dpy; EGLConfig config; EGLContext context; EGLSurface surface;
  bool has_submit_3d_draw;
  int wake_fd;
};
void *rvgpu_egl_create_context(struct rvgpu_egl_state*,int,int,int);
void rvgpu_egl_destroy_context(struct rvgpu_egl_state*,void*);
int rvgpu_egl_make_context_current(struct rvgpu_egl_state*,void*);
void rvgpu_egl_set_scanout(struct rvgpu_egl_state*,struct rvgpu_scanout*,const struct rvgpu_virgl_params*);
