// sentineld --state DIR [--cache DIR] [--lifeline] [--mke2fs PATH] [--share DIR] [--owner-fd N]
//           [--distributions DIR]...  (folders of distribution folders; default: <root>/distributions)
//           [--keep-images]  (keep installed bases no workspace uses: the end-to-end harness)
// sentineld ctl (--state DIR | --socket PATH) OP [FIELDS]  (one request, from a shell: ctl.hpp)
// sentineld view (--state DIR | --socket PATH) WORKSPACE  (the desktop in a window: view/view.hpp)
// sentineld vm  (one VM, a child sentineld spawns per workspace: macos/vm/main.m)
// sentineld help [distributions]  (core/help/*.txt, built into the binary)
// sentineld --update-session ROOT (remote Macs: the update coordinator, update.hpp)
// Owned mode (--lifeline): stops when stdin reaches EOF, i.e. when Sentinel quits or crashes.
// Standalone mode: stops on SIGTERM or SIGINT. Both power off every VM before exiting.
#include <algorithm>
#include <cerrno>
#include <climits>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/resource.h>
#include <unistd.h>
#include <vector>

#include "ctl.hpp"
#include "help/help.hpp"
#include "loop.hpp"
#include "platform.hpp"
#include "server.hpp"
#include "update.hpp"
#include "util/sha256.hpp"
#include "view/view.hpp"
#include "workspaces.hpp"

namespace {

int signalPipe[2];

void onSignal(int) {
  char c = 0;
  (void)!::write(signalPipe[1], &c, 1);
}

// Installed layout: <root>/bin/sentineld, <root>/tools/{mke2fs,resize2fs}, <root>/share (the guest share),
// <root>/distributions (the distributions it ships).
std::string root(const char* argv0) {
  char path[PATH_MAX];
  if (!::realpath(argv0, path)) return ".";
  std::string dir(path);
  dir.erase(dir.rfind('/'));
  return dir + "/..";
}

// Base disks, install sources and downloaded packages are cache: the per-user cache directory,
// which the user or a cleaner may wipe at any time. One subdirectory per state directory keeps
// instances apart.
std::string defaultCache(const std::string& state) {
  const char* home = std::getenv("HOME");
#ifdef __APPLE__
  std::string base = std::string(home ? home : ".") + "/Library/Caches/Sentinel";
#else
  const char* xdg = std::getenv("XDG_CACHE_HOME");
  std::string base = xdg && *xdg == '/' ? std::string(xdg) + "/sentinel" : std::string(home ? home : ".") + "/.cache/sentinel";
#endif
  char path[PATH_MAX];
  sentineld::Sha256 h;
  const std::string real = ::realpath(state.c_str(), path) ? path : state;
  h.update(real.data(), real.size());
  return base + "/" + h.digest().substr(7, 16);
}

// One sentineld per directory: a second one would prune or overwrite the first one's files.
// An update coordinator hands over the lock it already holds (--owner-fd), with no unowned gap.
bool own(const std::string& dir, int inherited = -1) {
  std::error_code ec;
  std::filesystem::create_directories(dir, ec);
  ::chmod(dir.c_str(), 0700);
  int lock = inherited >= 0 ? inherited : ::open((dir + "/owner.lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
  if (lock < 0) {
    std::fprintf(stderr, "sentineld: cannot use %s: %s\n", dir.c_str(), std::strerror(errno));
    return false;
  }
  if (inherited >= 0) fcntl(lock, F_SETFD, FD_CLOEXEC);
  if (::flock(lock, LOCK_EX | LOCK_NB) == 0) return true;
  std::fprintf(stderr, "sentineld: %s is already in use by another sentineld\n", dir.c_str());
  return false;
}

int help(const char* topic) {
  if (const char* text = sentineld::helpText(topic ? topic : "")) return std::fputs(text, stdout), 0;
  std::fprintf(stderr, "sentineld: no help on %s (topics: distributions; sentineld ctl --help for operations)\n", topic);
  return 2;
}

}  // namespace

extern "C" int sentineld_vm(void);  // macos/vm/main.m

int main(int argc, char** argv) {
  // First, before this process opens anything: the VM reads its descriptors 3 and 4 as sentineld left them.
  if (argc == 2 && !std::strcmp(argv[1], "vm")) return sentineld_vm();
  const std::string base = root(argv[0]);
  std::string state, cache, mke2fs = base + "/tools/mke2fs", share = base + "/share";
  std::vector<std::string> distributions;
  bool lifeline = false, keepImages = false;
  int ownerFd = -1;
  if (argc == 3 && std::string(argv[1]) == "--update-session") return sentineld::updateSession(argv[2]);
  if (argc > 1 && std::string(argv[1]) == "ctl") return sentineld::ctl(argc, argv);
  if (argc > 1 && std::string(argv[1]) == "view") return sentineld::view(argc, argv);
  if (argc > 1 && (std::string(argv[1]) == "help" || std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h"))
    return argc <= 3 ? help(argc == 3 ? argv[2] : nullptr) : 2;
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    if (a == "--state" && i + 1 < argc) state = argv[++i];
    else if (a == "--cache" && i + 1 < argc) cache = argv[++i];
    else if (a == "--mke2fs" && i + 1 < argc) mke2fs = argv[++i];
    else if (a == "--share" && i + 1 < argc) share = argv[++i];
    else if (a == "--workspace" && i + 1 < argc) ++i;  // passed by releases before `sentineld vm` updating to this one
    else if (a == "--distributions" && i + 1 < argc) distributions.push_back(argv[++i]);
    else if (a == "--owner-fd" && i + 1 < argc) ownerFd = std::atoi(argv[++i]);
    else if (a == "--lifeline") lifeline = true;
    else if (a == "--keep-images") keepImages = true;
    else { std::fprintf(stderr, "sentineld: unknown argument %s (sentineld --help)\n", a.c_str()); return 2; }
  }
  if (state.empty()) { std::fprintf(stderr, "sentineld: --state is required (sentineld --help)\n"); return 2; }
  if (distributions.empty()) distributions.push_back(base + "/distributions");
  // Every forward connection, download and VM device holds descriptors; macOS starts at 256.
  if (rlimit r; getrlimit(RLIMIT_NOFILE, &r) == 0) {
    r.rlim_cur = std::min<rlim_t>(r.rlim_max, OPEN_MAX);
    setrlimit(RLIMIT_NOFILE, &r);
  }

  try {
    if (!own(state, ownerFd)) return 1;
    if (cache.empty()) cache = defaultCache(state);
    std::error_code ec;
    std::filesystem::create_directories(std::filesystem::path(cache).parent_path(), ec);
    if (!own(cache)) return 1;

    sentineld::Loop loop;
    auto platform = sentineld::makePlatform(loop, base + "/bin/sentineld");
    sentineld::Media media(loop, base + "/bin/sentineld-media");
    sentineld::Workspaces workspaces(loop, *platform, state, cache, mke2fs, share, distributions, &media, keepImages);
    bool draining = false;
    auto drain = [&] {
      if (draining) return;
      draining = true;
      workspaces.drain([&] { loop.stop(); });
    };
    sentineld::Server server(loop, workspaces, state + "/sentineld.sock", drain);
    if (::pipe(signalPipe) != 0) throw std::runtime_error("pipe failed");
    for (int fd : signalPipe) {
      fcntl(fd, F_SETFD, FD_CLOEXEC);
      fcntl(fd, F_SETFL, O_NONBLOCK);
    }
    std::signal(SIGTERM, onSignal);
    std::signal(SIGINT, onSignal);
    std::signal(SIGPIPE, SIG_IGN);
    loop.onReadable(signalPipe[0], [&] { char c; (void)!::read(signalPipe[0], &c, 1); drain(); });
    if (lifeline)
      loop.onReadable(STDIN_FILENO, [&] {
        char buf[256];
        if (::read(STDIN_FILENO, buf, sizeof buf) <= 0) { loop.remove(STDIN_FILENO); drain(); }
      });

    std::fprintf(stderr, "sentineld: listening on %s/sentineld.sock\n", state.c_str());
    loop.run();
    return 0;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld: %s\n", e.what());
    return 1;
  }
}
