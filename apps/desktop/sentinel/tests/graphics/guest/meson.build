project('sentinel-guest-driver-tests', 'c',
  meson_version: '>=1.1.0',
  default_options: ['optimization=2', 'warning_level=2', 'werror=true', 'b_ndebug=false'])

driver = '../../../native/graphics/guest/driver'
headers = include_directories(driver)
threads = dependency('threads')
merge = static_library('mc-merge', driver / 'mc_merge.c', include_directories: headers)
test('mapped-mask', executable('mapped-mask', 'mapped-mask.c', include_directories: headers))
test('merge', executable('merge', 'merge.c', include_directories: headers, link_with: merge))

arm64 = host_machine.cpu_family() == 'aarch64'
linux = host_machine.system() == 'linux'
if not linux and (get_option('drm_planes') or get_option('egl_library') != '')
  error('The requested DRM/EGL runtime gates require a Linux host')
endif
if arm64 or linux
  tracker = static_library('dirty-tracker-driver', driver / 'dirty_tracker.c',
    include_directories: headers, dependencies: threads)
endif
if arm64
  # This fixture includes dirty_tracker.c with its own allocation guards.
  test('tracker-memory', executable('tracker-memory', 'tracker-memory.c',
    include_directories: headers, dependencies: threads))
  test('tracker-capture', executable('tracker-capture', 'tracker-capture.c',
    include_directories: headers, dependencies: threads, link_with: tracker))
  test('tracker-merge', executable('tracker-merge', 'tracker-merge.c',
    include_directories: headers, dependencies: threads, link_with: [tracker, merge]))
endif

mesa = get_option('mesa_source_dir')
if mesa != ''
  test('inline-write', executable('inline-write', 'inline-write.c',
    include_directories: [headers, include_directories(mesa / 'src/virtio')]))
endif
if linux
  test('dirty-tracker', executable('dirty-tracker', 'dirty-tracker.c',
    include_directories: headers, dependencies: threads, link_with: tracker))
  if get_option('drm_planes')
    test('drm-planes', executable('drm-planes', 'drm-planes.c', dependencies: dependency('libdrm')))
  endif
  if get_option('egl_library') != ''
    egl_headers = []
    if mesa != ''
      egl_headers += include_directories(mesa / 'include')
    endif
    gl = executable('gl-buffer-storage', 'gl-buffer-storage.c',
      include_directories: egl_headers, dependencies: meson.get_compiler('c').find_library('dl'))
    test('gl-buffer-storage', find_program('sh'),
      args: [files('run.sh'), '--egl-tests', gl, get_option('egl_library')],
      is_parallel: false, timeout: 300)
  endif
endif
