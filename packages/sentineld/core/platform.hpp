// The OS-specific VM layer (DESIGN.md §15). All callbacks run on the core's Loop.
#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>

namespace sentineld {

class Loop;

enum class DiskSync { Full, Fsync };

struct Display {
  unsigned width = 0, height = 0, hz = 0;
};

struct VmConfig {
  std::string id;
  std::string bootDisk;    // GPT disk with the EFI system partition (GRUB finds the root by label)
  std::string rootDisk;    // a Linux system's ext4 root as a whole disk, no partition table; empty: none
  std::string consoleLog;  // this run's whole guest console (up to 16 MiB), written as it arrives
  std::string log;         // the VM process's own messages, appended
  std::string shareDir;    // served read-only to the guest as virtiofs tag "sentinel" (the agent)
  std::string distributionDir;  // the workspace's distribution folder, read-only as tag "distribution"
  std::string shareDisk;   // or the share as a read-only FAT32 disk, for guests without virtiofs
  std::string projectDir;  // served read-write as virtiofs tag "project"; empty for none
  std::string packageCacheDir; // the distribution's package cache, read-write as tag "sentinel-cache"; empty for none
  unsigned cpus = 2;
  uint64_t memoryBytes = 2ull << 30;
  bool network = true;      // a virtio NIC on the host's network (libslirp)
  std::array<uint8_t, 6> mac{};
  DiskSync sync = DiskSync::Fsync;
  std::optional<Display> display;  // a desktop: GPU, display and input devices
  // The machine the distribution needs (its distribution.json "machine"): the virtio transport
  // ("pci"; empty: virtio-mmio), and the virtio GPU ("3d": Venus, virgl and Neptune, for Windows'
  // viogpu3d; "2d": framebuffers and a cursor only, on the machine's transport, for guests whose
  // driver drives nothing more; "none": the boot framebuffer alone; empty: Venus).
  std::string transport;
  std::string gpu;
  int mediaFd = -1;                // with a display: the viewer channel (DESIGN.md §6), the callee's
};

class Platform {
 public:
  using Done = std::function<void(std::string error)>;  // "" on success
  virtual ~Platform() = default;

  virtual void start(const VmConfig& config, Done done) = 0;
  // Hard stop; graceful stop is the agent's. A VM still starting is stopped once it has started.
  virtual void powerOff(const std::string& id, Done done) = 0;
  // Opens a connection to a guest vsock port. On success fd >= 0 and the caller owns it.
  virtual void connect(const std::string& id, uint32_t port, std::function<void(int fd, std::string error)> done) = 0;
  // The Unix socket of a running VM's display service (computer use, DESIGN.md §22); "" for none.
  virtual std::string displaySocket(const std::string& id) = 0;
  // The newest console bytes of a running VM (bounded; guest-controlled, so sanitize before use).
  virtual std::string consoleTail(const std::string& id, size_t bytes) = 0;
  // Powers off every VM, starting ones included.
  virtual void drain(std::function<void()> done) = 0;
  // Fires exactly once per run, after the VM has stopped for any reason (error "" = clean stop;
  // `reboot`: the guest asked to restart, and the caller starts the machine again if it wants it).
  std::function<void(const std::string& id, const std::string& error, bool reboot)> onExit;

  // Disk operations on stopped VMs; synchronous because they are O(1) on APFS.
  virtual void cloneDisk(const std::string& from, const std::string& to) = 0;
  virtual void growDisk(const std::string& path, uint64_t bytes) = 0;
  // The bytes a file holds that no clone shares: what deleting it frees.
  virtual uint64_t ownBytes(const std::string& path) = 0;
};

// `exe`: this sentineld, whose `vm` mode runs each VM (lib/ and share/firmware/ beside its bin/).
std::unique_ptr<Platform> makePlatform(Loop& loop, std::string exe);

}  // namespace sentineld
