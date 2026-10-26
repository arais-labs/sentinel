// macOS Platform: one sentinel-workspace process per VM (DESIGN.md §15). Hypervisor.framework runs
// one VM per process, so each workspace's machine is a child: sentineld writes its config on the
// control socket, keeps its console, reaches the guest agent through the unix socket libkrun maps to
// vsock port 1, and powers it off by ending the process. Everything runs on the core Loop.
#include <IOKit/pwr_mgt/IOPMLib.h>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <map>
#include <spawn.h>
#include <stdexcept>
#include <sys/clonefile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include "../../core/loop.hpp"
#include "../../core/platform.hpp"
#include "../../core/util/json.hpp"

extern char** environ;

namespace sentineld {
namespace {

constexpr uint32_t kAgentPort = 1;
constexpr size_t kConsoleBytes = 1 << 20;
constexpr size_t kConsoleLogBytes = 16 << 20;  // per run
constexpr uint64_t kKillAfterMs = 5000;        // SIGTERM is a hard power off; this only bounds a stuck one
constexpr int kExitReset = 3;                  // sentinel-workspace's exit status when the guest rebooted

// Newest console bytes for failure messages, and the whole run written to its log file as it
// arrives (a guest that wedges still leaves its kernel's last words on disk). The guest controls
// this output, so both are bounded.
struct Console {
  std::string bytes;
  FILE* log = nullptr;
  size_t logged = 0;
  void append(const char* data, size_t n) {
    bytes.append(data, n);
    if (bytes.size() > kConsoleBytes) bytes.erase(0, bytes.size() - kConsoleBytes);
    if (!log) return;
    if (logged + n <= kConsoleLogBytes) {
      std::fwrite(data, 1, n, log);
      std::fflush(log);
    } else if (logged <= kConsoleLogBytes) {
      std::fputs("\n[sentineld: console log limit reached; later output is not kept]\n", log);
      std::fflush(log);
    }
    logged += n;
  }
  void close() {
    if (log) std::fclose(log);
    log = nullptr;
  }
};

struct Vm {
  std::string id, log, sockets;
  uint64_t generation = 0;
  pid_t pid = 0;
  int control = -1, console = -1;
  std::string controlBuffer;
  Console out;
  bool running = false, exited = false;
  Platform::Done started;                    // until the VM reports running
  std::vector<Platform::Done> stopWaiters;   // powerOff requests
  uint64_t killTimer = 0;
};

// Per-user and short: a unix socket path holds at most 104 bytes. Unique to each launch: a VM's id
// (an install's is its recipe's hash) is no name another sentineld, or this VM's next run, won't use.
std::string socketDir() {
  char temp[PATH_MAX];
  if (!confstr(_CS_DARWIN_USER_TEMP_DIR, temp, sizeof temp)) throw std::runtime_error("no temporary directory");
  std::string dir = std::string(temp) + "sentineld-XXXXXXXX";
  if (!::mkdtemp(dir.data()))
    throw std::runtime_error(std::string("cannot create the VM's socket directory: ") + std::strerror(errno));
  return dir;
}

// The last line the VM process logged: why it could not start.
std::string lastLogLine(const std::string& path) {
  FILE* f = std::fopen(path.c_str(), "r");
  if (!f) return "";
  std::string last, line;
  char buf[1024];
  while (std::fgets(buf, sizeof buf, f)) {
    line = buf;
    while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) line.pop_back();
    if (!line.empty()) last = line;
  }
  std::fclose(f);
  return last;
}

class WorkspacePlatform final : public Platform {
 public:
  WorkspacePlatform(Loop& loop, std::string workspace) : loop_(loop), exe_(std::move(workspace) + "/bin/sentinel-workspace") {}

  void start(const VmConfig& c, Done done) override {
    if (vms_.count(c.id)) {
      if (c.mediaFd >= 0) ::close(c.mediaFd);
      return done("already running");
    }
    auto vm = std::make_shared<Vm>();
    vm->id = c.id;
    vm->log = c.log;
    vm->generation = ++generation_;
    vm->started = std::move(done);
    try {
      vm->sockets = socketDir();
    } catch (const std::exception& e) {
      if (c.mediaFd >= 0) ::close(c.mediaFd);
      return vm->started(e.what());
    }

    int console[2], control[2];
    if (::pipe(console) != 0) {
      if (c.mediaFd >= 0) ::close(c.mediaFd);
      return vm->started("cannot create the console pipe");
    }
    if (::socketpair(AF_UNIX, SOCK_STREAM, 0, control) != 0) {
      ::close(console[0]), ::close(console[1]);
      if (c.mediaFd >= 0) ::close(c.mediaFd);
      return vm->started("cannot create the control socket");
    }
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_adddup2(&actions, console[1], 1);
    posix_spawn_file_actions_addopen(&actions, 2, c.log.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&actions, control[1], 3);
    if (c.mediaFd >= 0) posix_spawn_file_actions_adddup2(&actions, c.mediaFd, 4);
    // Only fds 0-4 reach it: it runs an untrusted guest and must hold nothing else of sentineld's.
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_CLOEXEC_DEFAULT);
    const char* argv[] = {exe_.c_str(), nullptr};
    const int rc = ::posix_spawn(&vm->pid, exe_.c_str(), &actions, &attr, const_cast<char* const*>(argv), environ);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    ::close(console[1]);
    ::close(control[1]);
    if (c.mediaFd >= 0) ::close(c.mediaFd);
    if (rc != 0) {
      ::close(console[0]), ::close(control[0]);
      return vm->started("cannot start " + exe_ + ": " + std::strerror(rc));
    }
    vm->console = console[0];
    vm->control = control[0];
    for (int fd : {vm->console, vm->control}) {
      fcntl(fd, F_SETFD, FD_CLOEXEC);
      fcntl(fd, F_SETFL, O_NONBLOCK);
    }
    vm->out.log = std::fopen(c.consoleLog.c_str(), "wb");
    vms_[c.id] = vm;
    keepAwake();

    json::Array shares;
    if (!c.shareDir.empty()) shares.push_back(json::Object{{"tag", "sentinel"}, {"path", c.shareDir}, {"read_only", true}});
    if (!c.distributionDir.empty())
      shares.push_back(json::Object{{"tag", "distribution"}, {"path", c.distributionDir}, {"read_only", true}});
    if (!c.projectDir.empty()) shares.push_back(json::Object{{"tag", "project"}, {"path", c.projectDir}, {"read_only", false}});
    if (!c.packageCacheDir.empty())
      shares.push_back(json::Object{{"tag", "sentinel-cache"}, {"path", c.packageCacheDir}, {"read_only", false}});
    char mac[18];
    std::snprintf(mac, sizeof mac, "%02x:%02x:%02x:%02x:%02x:%02x", c.mac[0], c.mac[1], c.mac[2], c.mac[3], c.mac[4], c.mac[5]);
    json::Object config{
        {"boot_disk", c.bootDisk},
        {"sync", c.sync == DiskSync::Full ? "full" : "fsync"},
        {"cpus", uint64_t(c.cpus)},
        {"memory_mib", uint64_t(c.memoryBytes >> 20)},
        {"shares", std::move(shares)},
        {"agent_socket", vm->sockets + "/agent.sock"},
    };
    if (c.network) config["mac"] = std::string(mac);  // no mac: no network device
    if (!c.rootDisk.empty()) config["root_disk"] = c.rootDisk;
    if (!c.shareDisk.empty()) config["share_disk"] = c.shareDisk;
    if (!c.transport.empty()) config["transport"] = c.transport;
    if (!c.gpu.empty()) config["gpu"] = c.gpu;
    if (c.display)
      config["display"] = json::Object{{"width", uint64_t(c.display->width)},
                                       {"height", uint64_t(c.display->height)},
                                       {"hz", uint64_t(c.display->hz)},
                                       {"service_socket", vm->sockets + "/display.sock"}};
    const std::string line = json::dump(config) + "\n";
    // Small enough for the socket buffer: written at once.
    if (::write(vm->control, line.data(), line.size()) != ssize_t(line.size())) ::kill(vm->pid, SIGKILL);

    loop_.onReadable(vm->console, [this, vm] {
      char buf[16384];
      ssize_t n;
      while ((n = ::read(vm->console, buf, sizeof buf)) > 0) vm->out.append(buf, size_t(n));
      if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) {
        loop_.remove(vm->console);
        ::close(vm->console);
        vm->console = -1;
      }
    });
    loop_.onReadable(vm->control, [this, vm] {
      char buf[4096];
      ssize_t n;
      while ((n = ::read(vm->control, buf, sizeof buf)) > 0) vm->controlBuffer.append(buf, size_t(n));
      for (size_t nl; (nl = vm->controlBuffer.find('\n')) != std::string::npos;) {
        const std::string event = vm->controlBuffer.substr(0, nl);
        vm->controlBuffer.erase(0, nl + 1);
        if (event.find("\"running\"") != std::string::npos && !vm->running) {
          vm->running = true;
          if (auto done = std::exchange(vm->started, nullptr)) done("");
          if (!vm->stopWaiters.empty()) stop(vm);  // powerOff arrived while starting
        }
      }
      // The control socket closes when the process ends: collect it.
      if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) {
        loop_.remove(vm->control);
        ::close(vm->control);
        vm->control = -1;
        reap(vm);
      }
    });
  }

  void powerOff(const std::string& id, Done done) override {
    auto it = vms_.find(id);
    if (it == vms_.end()) return done("");  // not running
    auto vm = it->second;
    vm->stopWaiters.push_back(std::move(done));
    if (vm->stopWaiters.size() == 1) stop(vm);
  }

  void connect(const std::string& id, uint32_t port, std::function<void(int, std::string)> done) override {
    auto it = vms_.find(id);
    if (it == vms_.end() || !it->second->running) return done(-1, "not running");
    if (port != kAgentPort) return done(-1, "no such vsock port");
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    const std::string path = it->second->sockets + "/agent.sock";
    std::snprintf(addr.sun_path, sizeof addr.sun_path, "%s", path.c_str());
    const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return done(-1, std::strerror(errno));
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    if (::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
      const std::string e = std::strerror(errno);
      ::close(fd);
      return done(-1, e);
    }
    done(fd, "");
  }

  std::string displaySocket(const std::string& id) override {
    auto it = vms_.find(id);
    return it == vms_.end() || !it->second->running ? "" : it->second->sockets + "/display.sock";
  }

  std::string consoleTail(const std::string& id, size_t bytes) override {
    auto it = vms_.find(id);
    if (it == vms_.end()) return "";
    const std::string& c = it->second->out.bytes;
    return c.substr(c.size() > bytes ? c.size() - bytes : 0);
  }

  void drain(std::function<void()> done) override {
    auto pending = std::make_shared<size_t>(1);
    auto finished = [pending, done](std::string) { if (--*pending == 0) done(); };
    std::vector<std::string> ids;
    for (auto& [id, _] : vms_) ids.push_back(id);
    for (auto& id : ids) { ++*pending; powerOff(id, finished); }
    finished("");
  }

  void cloneDisk(const std::string& from, const std::string& to) override {
    ::unlink(to.c_str());
    if (::clonefile(from.c_str(), to.c_str(), 0) != 0)
      throw std::runtime_error("cannot clone disk (the state directory must be on APFS): " + std::string(strerror(errno)));
  }

  void growDisk(const std::string& path, uint64_t bytes) override {
    struct stat st;
    if (::stat(path.c_str(), &st) != 0) throw std::runtime_error("cannot stat " + path);
    if (uint64_t(st.st_size) < bytes && ::truncate(path.c_str(), off_t(bytes)) != 0)
      throw std::runtime_error("cannot grow " + path);
  }

 private:
  // Ends the process: a hard power off (the agent does graceful shutdowns). By value: callers pass
  // the shared pointer they hold.
  void stop(std::shared_ptr<Vm> vm) {
    if (vm->exited) return settle(vm, "");
    ::kill(vm->pid, SIGTERM);
    vm->killTimer = loop_.after(kKillAfterMs, [vm] {
      if (!vm->exited) ::kill(vm->pid, SIGKILL);
    });
  }

  // Collects the exited process without blocking the loop (retried until it has exited).
  void reap(std::shared_ptr<Vm> vm) {
    int status = 0;
    const pid_t r = ::waitpid(vm->pid, &status, WNOHANG);
    if (r == 0) {
      loop_.after(20, [this, vm] { reap(vm); });
      return;
    }
    std::string error;
    const bool reboot = r > 0 && WIFEXITED(status) && WEXITSTATUS(status) == kExitReset && vm->stopWaiters.empty();
    if (r < 0) error = "lost track of the virtual machine";
    else if (WIFSIGNALED(status) && WTERMSIG(status) != SIGTERM) error = "the virtual machine crashed (signal " + std::to_string(WTERMSIG(status)) + ")";
    else if (WIFEXITED(status) && WEXITSTATUS(status) != 0 && !reboot)
      error = "the virtual machine failed (exit " + std::to_string(WEXITSTATUS(status)) + ")";
    if (!error.empty())
      if (std::string why = lastLogLine(vm->log); !why.empty()) error += ": " + why;
    // A power off we asked for is a clean stop.
    if (!vm->stopWaiters.empty()) error.clear();
    finish(vm, error, reboot);
  }

  void settle(const std::shared_ptr<Vm>& vm, const std::string& error) {
    auto waiters = std::move(vm->stopWaiters);
    vm->stopWaiters.clear();
    for (auto& w : waiters) w(error);
  }

  // Runs once per VM run: closes its descriptors and console log, and reports the exit.
  void finish(std::shared_ptr<Vm> vm, const std::string& error, bool reboot = false) {
    if (vm->exited) return;
    vm->exited = true;
    if (vm->killTimer) loop_.cancel(vm->killTimer);
    if (vm->console >= 0) {
      // The rest of the console, then its end.
      char buf[16384];
      ssize_t n;
      while ((n = ::read(vm->console, buf, sizeof buf)) > 0) vm->out.append(buf, size_t(n));
      loop_.remove(vm->console);
      ::close(vm->console);
      vm->console = -1;
    }
    vm->out.close();
    ::unlink((vm->sockets + "/agent.sock").c_str());
    ::unlink((vm->sockets + "/display.sock").c_str());
    ::rmdir(vm->sockets.c_str());
    auto it = vms_.find(vm->id);
    if (it != vms_.end() && it->second->generation == vm->generation) vms_.erase(it);
    keepAwake();
    if (auto done = std::exchange(vm->started, nullptr)) done(error.empty() ? "the virtual machine stopped while starting" : error);
    settle(vm, "");
    if (onExit) onExit(vm->id, error, reboot);
  }

  Loop& loop_;
  std::string exe_;
  std::map<std::string, std::shared_ptr<Vm>> vms_;
  IOPMAssertionID awake_ = kIOPMNullAssertionID;

  // While a VM runs the Mac does not idle-sleep (a sleeping host freezes every guest, an install's
  // too); the display still sleeps.
  void keepAwake() {
    if (!vms_.empty() && awake_ == kIOPMNullAssertionID)
      IOPMAssertionCreateWithName(kIOPMAssertPreventUserIdleSystemSleep, kIOPMAssertionLevelOn,
                                  CFSTR("Sentinel workspaces are running"), &awake_);
    else if (vms_.empty() && awake_ != kIOPMNullAssertionID)
      IOPMAssertionRelease(std::exchange(awake_, kIOPMNullAssertionID));
  }
  uint64_t generation_ = 0;
};

}  // namespace

std::unique_ptr<Platform> makePlatform(Loop& loop, std::string workspace) {
  return std::make_unique<WorkspacePlatform>(loop, std::move(workspace));
}

}  // namespace sentineld
