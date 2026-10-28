// sentineld vm: one workspace's virtual machine, a child sentineld starts (DESIGN.md §15). libkrun
// on Hypervisor.framework (one VM per process), booting the workspace's disks through EFI firmware;
// with a desktop, a virtio-gpu (Venus on KosmicKrisp; with virgl and Neptune for Windows; or 2D) whose
// scanout is encoded for viewers.
//
// fd 0: /dev/null. fd 1: the guest console (virtio console and the firmware's serial port). fd 2: this
// process's log. fd 3: the control socket: sentineld writes the config as one JSON line, then this
// process writes {"event":"running"} once the VM is built. fd 4 (with a desktop): the media channel.
// The VM ends with this process: when the guest powers off, or on SIGTERM (a hard power off).
#import <Foundation/Foundation.h>

#include <fcntl.h>
#include <libkrun.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <sandbox.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

#import "display.h"
#include "audio.h"
#include "input.h"
#include "net.h"
#import "service.h"

static const uint32_t kAgentPort = 1;      // the guest agent (sentineld core/agent.hpp)
static const uint32_t kDisplayPort = 1101;  // the display service for computer use (service.h)

static bool printError(void *userdata, KrunStr s) {
  (void)userdata;
  fwrite(s.data, 1, s.len, stderr);
  return true;
}

static void fail(const char *what, KrunError err) {
  fprintf(stderr, "sentineld vm: %s failed", what);
  if (err) {
    fputs(": ", stderr);
    KrunVtableHandle writer =
        KRUN_VTABLE_HANDLE(KRUN_PUSH_STR_TYPE_TAG, ((KrunPushStrVtable){.push = printError}), NULL);
    krun_error_message(err, &writer);
  }
  fputc('\n', stderr);
  exit(1);
}

#define CHECK(call)              \
  ({                             \
    KrunError err = NULL;        \
    __auto_type result = (call); \
    if (err) fail(#call, err);   \
    result;                      \
  })

static KrunStr str(NSString *s) { return (KrunStr){.data = s.UTF8String, .len = strlen(s.UTF8String)}; }

// <root>/bin/sentineld: lib/ and share/ are its siblings.
static NSString *root(void) {
  char executable[PATH_MAX], resolved[PATH_MAX];
  uint32_t size = sizeof executable;
  if (_NSGetExecutablePath(executable, &size) || !realpath(executable, resolved)) {
    fprintf(stderr, "sentineld vm: cannot resolve its own path\n");
    exit(1);
  }
  return @(resolved).stringByDeletingLastPathComponent.stringByDeletingLastPathComponent;
}

// The config line from fd 3.
static NSDictionary *readConfig(void) {
  NSMutableData *line = [NSMutableData data];
  char c;
  for (;;) {
    ssize_t n = read(3, &c, 1);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0 || line.length > (1 << 20)) {
      fprintf(stderr, "sentineld vm: no config on fd 3\n");
      exit(2);
    }
    if (c == '\n') break;
    [line appendBytes:&c length:1];
  }
  id config = [NSJSONSerialization JSONObjectWithData:line options:0 error:NULL];
  if (![config isKindOfClass:NSDictionary.class]) {
    fprintf(stderr, "sentineld vm: invalid config\n");
    exit(2);
  }
  return config;
}

static NSString *text(NSDictionary *d, NSString *key) {
  id v = d[key];
  if (![v isKindOfClass:NSString.class] || ![v length]) {
    fprintf(stderr, "sentineld vm: config needs %s\n", key.UTF8String);
    exit(2);
  }
  return v;
}

static uint32_t number(NSDictionary *d, NSString *key) {
  id v = d[key];
  if (![v isKindOfClass:NSNumber.class] || [v longLongValue] <= 0 || [v longLongValue] > UINT32_MAX) {
    fprintf(stderr, "sentineld vm: config needs %s\n", key.UTF8String);
    exit(2);
  }
  return [v unsignedIntValue];
}

// A pollable descriptor that never delivers input (kqueue rejects /dev/null).
static int quietInput(void) {
  int fds[2];
  if (pipe(fds)) {
    perror("sentineld vm: pipe");
    exit(1);
  }
  return fds[0];
}

// Mesa's shader cache in the per-user cache directory (its default, ~/.cache, is outside the
// sandbox): without it every start compiles every shader again.
static NSString *shaderCache(void) {
  char cache[PATH_MAX];
  if (!confstr(_CS_DARWIN_USER_CACHE_DIR, cache, sizeof cache)) return nil;
  NSString *dir = [@(cache) stringByAppendingPathComponent:@"sentinel-workspace-shaders"];
  [NSFileManager.defaultManager createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:NULL];
  return dir;
}

static NSString *quoted(NSString *path) {
  char resolved[PATH_MAX];
  NSString *p = realpath(path.fileSystemRepresentation, resolved) ? @(resolved) : path;
  return [NSString stringWithFormat:@"\"%@\"", [[p stringByReplacingOccurrencesOfString:@"\\" withString:@"\\\\"]
                                                   stringByReplacingOccurrencesOfString:@"\""
                                                                             withString:@"\\\""]];
}

// The guest drives large C libraries (virtio devices, virglrenderer, the GPU driver): once the VM is
// built, this process may not start programs, and writes only its disks, writable shares, sockets
// and caches. The network stays open (libslirp is the guest's network).
static void enterSandbox(NSArray<NSString *> *writable) {
  char cache[PATH_MAX], temp[PATH_MAX];
  NSMutableArray *paths = [writable mutableCopy];
  if (confstr(_CS_DARWIN_USER_CACHE_DIR, cache, sizeof cache)) [paths addObject:@(cache)];
  if (confstr(_CS_DARWIN_USER_TEMP_DIR, temp, sizeof temp)) [paths addObject:@(temp)];
  NSMutableString *allow = [NSMutableString string];
  for (NSString *path in paths) [allow appendFormat:@" (subpath %@)", quoted(path)];
  NSString *profile = [NSString stringWithFormat:@"(version 1)(allow default)(deny process-exec*)(deny process-fork)"
                                                  "(deny file-write*)(allow file-write*%@)",
                                                 allow];
  char *error = NULL;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
  if (sandbox_init(profile.UTF8String, 0, &error)) {
    fprintf(stderr, "sentineld vm: sandbox: %s\n", error);
    exit(1);
  }
#pragma clang diagnostic pop
}

static void powerOff(int signal) {
  (void)signal;
  _exit(0);  // a hard power off: graceful shutdown is the guest agent's
}

int sentineld_vm(void) {
  @autoreleasepool {
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, powerOff);
    signal(SIGINT, powerOff);
    // Whether sentineld passed the media channel, decided before this process opens anything: a
    // descriptor opened later (the console's pseudo-terminal) can be fd 4, and the media stream
    // written to it would reach the guest's console as input.
    const bool media = fcntl(4, F_GETFD) >= 0;
    NSDictionary *config = readConfig();
    NSString *base = root();
    NSMutableArray<NSString *> *writable = [NSMutableArray array];

    KrunError err = NULL;
    // libkrun logs warnings; SENTINEL_KRUN_LOG=debug|trace, for development, says more.
    const char *level = getenv("SENTINEL_KRUN_LOG");
    const uint32_t logLevel = level && !strcmp(level, "trace")   ? KRUN_LOG_LEVEL_TRACE
                              : level && !strcmp(level, "debug") ? KRUN_LOG_LEVEL_DEBUG
                                                                 : KRUN_LOG_LEVEL_WARN;
    if (krun_init_log(STDERR_FILENO, logLevel, KRUN_LOG_STYLE_NEVER, KRUN_LOG_OPTIONS_NO_ENV, &err) !=
        KRUN_RESULT_SUCCESS)
      fail("log", err);
    KrunMmioDeviceManager devices = krun_mmio_device_manager_new();
    // The machine's virtio transport (config "transport"): "pci" puts the devices on a PCI bus;
    // otherwise every device is virtio-mmio.
    KrunPciDeviceManager pci = [config[@"transport"] isEqual:@"pci"] ? krun_pci_device_manager_new() : NULL;
    void (^add)(KrunAttachDevice) = ^(KrunAttachDevice device) {
      if (pci) krun_pci_device_manager_add(pci, device);
      else krun_mmio_device_manager_add(devices, device);
    };

    // The guest console (hvc0). libkrun makes port 0 a console only for a terminal, so it gets a
    // pseudo-terminal whose output is copied to fd 1, where sentineld keeps it.
    {
      const int master = posix_openpt(O_RDWR | O_NOCTTY);
      if (master < 0 || grantpt(master) || unlockpt(master)) fail("console terminal", NULL);
      const int terminal = open(ptsname(master), O_RDWR | O_NOCTTY | O_CLOEXEC);
      if (terminal < 0) fail("console terminal", NULL);
      fcntl(master, F_SETFD, FD_CLOEXEC);
      dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        char buffer[16384];
        for (;;) {
          const ssize_t n = read(master, buffer, sizeof buffer);
          if (n < 0 && errno == EINTR) continue;
          if (n <= 0) return;
          (void)!write(STDOUT_FILENO, buffer, (size_t)n);
        }
      });
      KrunConsoleBuilder builder = krun_console_device_builder();
      uint32_t port = 0;
      if (krun_console_builder_add_tty_port(builder, (KrunStr){.data = "console", .len = 7}, terminal, &port, &err) !=
          KRUN_RESULT_SUCCESS)
        fail("console", err);
      add(CHECK(krun_console_builder_build(builder, &err)));
    }

    // vsock: sentineld reaches the agent through a unix socket libkrun listens on; the guest reaches
    // the display service through one this process listens on.
    KrunVsockDevice vsock = CHECK(krun_vsock_device_new(3, 0, &err));
    NSString *agentSocket = text(config, @"agent_socket");
    krun_vsock_device_add_unix_port(vsock, kAgentPort, str(agentSocket), true);
    [writable addObject:agentSocket.stringByDeletingLastPathComponent];

    // The desktop: GPU, display and input, first on the transport: their addresses stay the same
    // whatever else the machine has (a disk, a network), so a guest that keys its drivers' settings
    // to the address (Windows) keeps them from install to workspace.
    NSDictionary *desktop = [config[@"display"] isKindOfClass:NSDictionary.class] ? config[@"display"] : nil;
    if (desktop) {
      setenv("VK_DRIVER_FILES", [base stringByAppendingPathComponent:@"lib/kosmickrisp_icd.json"].UTF8String, 1);
      NSString *cache = shaderCache();
      if (cache && !getenv("MESA_SHADER_CACHE_DIR")) setenv("MESA_SHADER_CACHE_DIR", cache.UTF8String, 1);
      const uint32_t width = number(desktop, @"width"), height = number(desktop, @"height"), hz = number(desktop, @"hz");
      // The screen from power on: the firmware's framebuffer (its logo, the boot loader, the OS
      // starting), until the guest's GPU driver shows its first frame. A guest without a virtio GPU
      // (config "gpu": "none") keeps drawing into it.
      krun_mmio_device_manager_add(devices, krun_ramfb_device_new(display_backend(width, height, hz)));
      // The GPU (config "gpu"), on the machine's transport, with its window onto GPU memory the
      // guest maps (a virtio-mmio shared memory region, or on PCI a 64-bit BAR): by default Venus
      // (Vulkan; a Linux guest runs OpenGL on it through Zink); "3d": every context the renderer
      // has, Venus, virgl (OpenGL on ANGLE) and Neptune (Direct3D 11 on DXMT), for Windows'
      // viogpu3d, whose desktop is drawn through virgl and Direct3D; "2d": framebuffers and a
      // cursor in guest memory, no window and no renderer (Windows' viogpudo drives only that);
      // "none": no virtio GPU.
      if ([config[@"gpu"] isEqual:@"2d"]) {
        KrunGpuDevice gpu = krun_gpu_device_new(0, display_backend(width, height, hz));
        krun_gpu_device_shm_size(&gpu, 0);
        add(gpu);
      } else if (![config[@"gpu"] isEqual:@"none"]) {
        const uint32_t venus = KRUN_VIRGL_RENDERER_FLAGS_VENUS | KRUN_VIRGL_RENDERER_FLAGS_RENDER_SERVER;
        const BOOL windows3d = [config[@"gpu"] isEqual:@"3d"];
        KrunGpuDevice gpu = krun_gpu_device_new(
            windows3d
                ? venus | KRUN_VIRGL_RENDERER_FLAGS_NEPTUNE | KRUN_VIRGL_RENDERER_FLAGS_USE_EGL |
                      KRUN_VIRGL_RENDERER_FLAGS_USE_GLES | KRUN_VIRGL_RENDERER_FLAGS_USE_SURFACELESS |
                      KRUN_VIRGL_RENDERER_FLAGS_THREAD_SYNC | KRUN_VIRGL_RENDERER_FLAGS_NATIVE_SHARE_TEXTURE
                : venus | KRUN_VIRGL_RENDERER_FLAGS_NO_VIRGL,
            display_backend(width, height, hz));
        krun_gpu_device_shm_size(&gpu, (size_t)8 << 30);
        add(gpu);
      }
      add(input_keyboard());
      add(input_pointer());
      add(audio_device());
      NSString *service = text(desktop, @"service_socket");
      service_start(service.fileSystemRepresentation);
      krun_vsock_device_add_unix_port(vsock, kDisplayPort, str(service), false);
      if (media) {
        fcntl(4, F_SETFD, FD_CLOEXEC);
        display_media(4);
      }
    }

    // Disks: the boot disk first (the firmware boots its EFI system partition), then the root, when
    // the distribution keeps it on a disk of its own.
    const uint32_t sync = [config[@"sync"] isEqual:@"full"] ? KRUN_SYNC_MODE_FULL : KRUN_SYNC_MODE_RELAXED;
    for (NSString *key in @[ @"boot_disk", @"root_disk" ]) {
      if ([key isEqual:@"root_disk"] && !config[key]) continue;
      NSString *path = text(config, key);
      KrunBlockDevice disk = CHECK(krun_block_device_new(str(key), str(path), KRUN_DISK_FORMAT_RAW, &err));
      krun_block_device_set_sync_mode(disk, sync);
      add(disk);
      [writable addObject:path];
    }
    // The share as a read-only disk, for guests without virtiofs (config "share_disk").
    if ([config[@"share_disk"] isKindOfClass:NSString.class]) {
      KrunBlockDevice share = CHECK(krun_block_device_new(str(@"share_disk"), str(config[@"share_disk"]), KRUN_DISK_FORMAT_RAW, &err));
      krun_block_device_set_read_only(share, true);
      add(share);
    }

    // Network (config "mac"; none: the machine has no network), entropy (without it early boot stalls
    // on the random seed).
    if (config[@"mac"]) {
      uint8_t mac[6];
      if (sscanf(text(config, @"mac").UTF8String, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx", &mac[0], &mac[1], &mac[2], &mac[3],
                 &mac[4], &mac[5]) != 6) {
        fprintf(stderr, "sentineld vm: invalid mac\n");
        return 2;
      }
      add(net_device(mac));
    }
    add(CHECK(krun_rng_device_new(&err)));
    // Free-page reporting: memory the guest frees goes back to macOS instead of staying with the VM.
    add(CHECK(krun_balloon_device_new(&err)));

    // Shared directories (virtiofs), each under its tag.
    for (NSDictionary *share in [config[@"shares"] isKindOfClass:NSArray.class] ? config[@"shares"] : @[]) {
      NSString *tag = text(share, @"tag"), *path = text(share, @"path");
      const BOOL readOnly = [share[@"read_only"] boolValue];
      add(readOnly ? CHECK(krun_fs_device_new_read_only(str(tag), str(path), &err))
                                                     : CHECK(krun_fs_device_new(str(tag), str(path), &err)));
      if (!readOnly) [writable addObject:path];
    }

    add(vsock);

    KrunVmmBuilder builder = krun_vmm_builder_new();
    if (krun_vmm_builder_vcpus(&builder, (uint8_t)number(config, @"cpus"), &err) != KRUN_RESULT_SUCCESS)
      fail("cpus", err);
    if (krun_vmm_builder_ram_mib(&builder, number(config, @"memory_mib"), &err) != KRUN_RESULT_SUCCESS)
      fail("memory", err);
    // Sentinel's firmware (edk2, patches/edk2-*.patch): virtio-mmio and virtio-pci, a device tree and
    // ACPI, the boot framebuffer.
    NSString *firmware = [base stringByAppendingPathComponent:@"share/firmware/SENTINEL_EFI.fd"];
    krun_vmm_builder_payload(&builder, CHECK(krun_payload_load_firmware(str(firmware), (KrunStr){.data = "", .len = 0}, &err)));
    krun_vmm_builder_devices(&builder, devices);
    if (pci) krun_vmm_builder_devices(&builder, pci);
    // The firmware and GRUB talk to the legacy serial port; without one the firmware never boots.
    if (krun_vmm_builder_add_serial_console(&builder, quietInput(), STDOUT_FILENO, &err) != KRUN_RESULT_SUCCESS)
      fail("serial console", err);
    KrunVmm vmm = CHECK(krun_vmm_builder_build(&builder, &err));

    enterSandbox(writable);
    static const char running[] = "{\"event\":\"running\"}\n";
    if (write(3, running, sizeof running - 1) != sizeof running - 1) return 1;
    krun_vmm_run(vmm);  // returns only if the VMM stops without exiting the process
  }
  return 0;
}
