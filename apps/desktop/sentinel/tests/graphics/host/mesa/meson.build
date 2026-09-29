project('sentinel-host-mesa-tests', 'c', meson_version: '>=1.4.0',
  default_options: ['c_std=c11', 'optimization=2', 'warning_level=2', 'werror=true'])
if host_machine.system() != 'darwin'
  error('Host Metal qualification requires macOS')
endif
mesa = get_option('mesa_source')
build = get_option('mesa_build')
libraries = get_option('library_dir')
headers = include_directories(mesa / 'include')
internal_headers = include_directories(mesa / 'src', build / 'src')
test('buffer-ranges', executable('buffer-ranges', 'buffer-ranges.c',
  include_directories: [headers, internal_headers,
    include_directories(mesa / 'src/gallium/drivers/zink', mesa / 'src/gallium/include')],
  override_options: ['b_sanitize=address,undefined']))

# Compile the upstream regression with the same ABI feature defines as u_queue.c,
# and link the real driver build's archives, not a separately rebuilt queue.
queue_archives = [build / 'src/util/libmesa_util.a', build / 'src/c11/impl/libmesa_util_c11.a']
test('queue-finish', executable('queue-finish', mesa / 'src/util/tests/queue_finish_test.c',
  include_directories: [headers, internal_headers], c_args: get_option('queue_defines'),
  dependencies: dependency('threads'), link_args: queue_archives, link_depends: queue_archives),
  timeout: 30)

gpu_env = environment()
gpu_env.unset('DYLD_LIBRARY_PATH')
gpu_env.unset('DYLD_FALLBACK_LIBRARY_PATH')
gpu_env.set('MESA_LOADER_DRIVER_OVERRIDE', 'zink')
gpu_env.set('VK_DRIVER_FILES', get_option('icd'))
capabilities = executable('capabilities', 'capabilities.c', include_directories: headers)
test('capabilities', capabilities, args: libraries / 'libEGL.dylib',
  env: gpu_env, is_parallel: false, timeout: 120)
storage = executable('buffer-storage', 'buffer-storage.c', include_directories: headers)
foreach name : ['mip-views', 'prefragment-storage', 'sparse-copies']
  epoxy = libraries / 'libepoxy.0.dylib'
  fixture = executable(name, name + '.c',
    include_directories: [headers, include_directories(get_option('epoxy_source') / 'include',
      get_option('epoxy_build') / 'include')],
    link_args: [epoxy, '-Wl,-headerpad_max_install_names'], link_depends: epoxy,
    build_rpath: libraries)
  test(name, fixture, env: gpu_env, is_parallel: false, timeout: 120)
endforeach
foreach repetition : range(get_option('repetitions'))
  test('buffer-storage-' + (repetition + 1).to_string(), storage,
    args: libraries / 'libEGL.dylib', env: gpu_env, is_parallel: false, timeout: 120)
endforeach
