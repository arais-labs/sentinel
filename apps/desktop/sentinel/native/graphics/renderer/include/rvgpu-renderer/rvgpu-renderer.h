#pragma once
#include <stdio.h>
#include <time.h>
#include <rvgpu-renderer/renderer/rvgpu-egl.h>
#include <librvgpu/rvgpu-protocol.h>
struct rvgpu_pr_state;
struct rvgpu_pr_params { FILE *capset; const struct rvgpu_scanout_params *sp; size_t nsp; };
struct rvgpu_pr_state *rvgpu_pr_init(struct rvgpu_egl_state*,const struct rvgpu_pr_params*,int,int);
unsigned int rvgpu_pr_dispatch(struct rvgpu_pr_state*);
void rvgpu_pr_free(struct rvgpu_pr_state*);
static inline double current_get_time_ms(void) {
  struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec*1000.0+t.tv_nsec/1e6;
}
static inline int clock_nanosleep(clockid_t c, int flags, const struct timespec *t, struct timespec *r) {
  (void)c; (void)flags; return nanosleep(t,r);
}
