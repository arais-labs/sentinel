project('sentinel-desktop-renderer', ['c', 'objc'],
  meson_version: '>=1.12.0',
  default_options: ['optimization=2', 'debug=false', 'warning_level=0', 'c_std=gnu11'])

work = get_option('graphics_work')
libraries = get_option('graphics_libraries')
if work == '' or libraries == ''
  error('graphics_work and graphics_libraries must name the prepared graphics directories')
endif
upstream = work / 'remote-virtio-gpu'
headers = include_directories(
  'renderer/include', 'video', 'transport',
  upstream / 'include',
  work / 'local/include',
  work / 'virgl/src',
  work / 'virgl-build/src',
  work / 'egl-registry/api',
  work / 'mesa-host/include',
  work / 'mesa-host/src/gallium/drivers/zink',
  work / 'mesa-host/src/kosmickrisp/vulkan',
)
bundled_libraries = [
  libraries / 'libvirglrenderer.1.dylib',
  libraries / 'libepoxy.0.dylib',
]
frameworks = dependency('appleframeworks', modules: [
  'Foundation', 'QuartzCore', 'Metal', 'VideoToolbox', 'CoreMedia',
  'CoreVideo', 'CoreGraphics', 'IOSurface', 'ImageIO',
])
executable('sentinel-desktop-renderer',
  'transport/gpu_transport.c',
  'renderer/GPUConnection.c',
  upstream / 'src/rvgpu-renderer/virgl/rvgpu-virgl.c',
  upstream / 'src/rvgpu-sanity/rvgpu-sanity.c',
  upstream / 'src/rvgpu-utils/rvgpu-utils.c',
  'renderer/DesktopRenderer.m',
  'video/DesktopVideoEncoder.m',
  'video/DesktopDisplayServer.m',
  'video/MetalFrameCopy.m',
  include_directories: headers,
  objc_args: ['-fobjc-arc', '-Wall', '-Wextra'],
  dependencies: frameworks,
  link_args: bundled_libraries,
  link_depends: bundled_libraries,
  build_rpath: '@executable_path',
)
