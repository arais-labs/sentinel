// sentineld-agent: the guest agent (DESIGN.md §9–11). vsock port 1, one operation per connection.
// Frames: [u8 type][u32 BE len][bytes]; the first frame is the JSON request.
#include <algorithm>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <grp.h>
#include <filesystem>
#include <netinet/in.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <linux/vm_sockets.h>
#include <poll.h>
#include <pwd.h>
#include <stdlib.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/klog.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <xkbcommon/xkbcommon.h>

#include "../../core/util/json.hpp"
#include "../../core/util/paths.hpp"

namespace {

using namespace sentineld;

enum Frame : uint8_t { Stdin = 0, Output = 1, Resize = 2, Exit = 3, Request = 4, Started = 5, Stderr = 6 };
constexpr uint32_t kMaxFrame = 1 << 20;
constexpr uint32_t kMaxRequest = kMaxFrame;  // Stage 1 allows 1 MiB requests (long scripts)

const char* const kEnv[] = {"PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                            "HOME=/root", "TMPDIR=/tmp", "TERM=xterm-256color", nullptr};

bool readAll(int fd, void* buf, size_t n) {
  auto* p = static_cast<char*>(buf);
  while (n) {
    ssize_t r = ::read(fd, p, n);
    if (r < 0 && errno == EINTR) continue;
    if (r <= 0) return false;
    p += r; n -= size_t(r);
  }
  return true;
}

bool writeAll(int fd, const void* buf, size_t n) {
  auto* p = static_cast<const char*>(buf);
  while (n) {
    ssize_t w = ::write(fd, p, n);
    if (w < 0 && errno == EINTR) continue;
    if (w <= 0) return false;
    p += w; n -= size_t(w);
  }
  return true;
}

bool send(int fd, uint8_t type, const void* data, size_t n) {
  uint8_t h[5] = {type, uint8_t(n >> 24), uint8_t(n >> 16), uint8_t(n >> 8), uint8_t(n)};
  return writeAll(fd, h, 5) && writeAll(fd, data, n);
}

bool send(int fd, uint8_t type, const std::string& s) { return send(fd, type, s.data(), s.size()); }

// Reads one frame; false on EOF, an oversized frame or a read error (the caller closes).
bool receive(int fd, uint8_t& type, std::string& payload, uint32_t limit = kMaxFrame) {
  uint8_t h[5];
  if (!readAll(fd, h, 5)) return false;
  uint32_t n = uint32_t(h[1]) << 24 | uint32_t(h[2]) << 16 | uint32_t(h[3]) << 8 | h[4];
  if (n > limit) return false;
  type = h[0];
  payload.resize(n);
  return readAll(fd, payload.data(), n);
}

void exitFrame(int fd, json::Object o) { send(fd, Exit, json::dump(o)); }

// --- processes --------------------------------------------------------------------------

// Starts `args` in its own process group (so a stalled run can be stopped whole), with `env`.
pid_t spawn(const std::vector<std::string>& args, const std::vector<std::string>& env, bool quiet = false) {
  pid_t pid = fork();
  if (pid != 0) return pid;
  setpgid(0, 0);
  if (quiet) {
    int null = ::open("/dev/null", O_WRONLY);
    dup2(null, 1);
    dup2(null, 2);
  }
  std::vector<char*> argv, envp;
  for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
  for (auto& e : env) envp.push_back(const_cast<char*>(e.c_str()));
  argv.push_back(nullptr);
  envp.push_back(nullptr);
  signal(SIGPIPE, SIG_DFL);
  execvpe(argv[0], argv.data(), envp.data());
  _exit(127);
}

int exitCode(int status) { return WIFSIGNALED(status) ? 128 + WTERMSIG(status) : WEXITSTATUS(status); }

int runAndWait(const std::vector<std::string>& args, const std::vector<std::string>& env, bool quiet = false) {
  pid_t pid = spawn(args, env, quiet);
  int status = 0;
  if (pid < 0 || waitpid(pid, &status, 0) < 0) return 127;
  return exitCode(status);
}

// --- readiness --------------------------------------------------------------------------

// A file's contents; empty when it cannot be read.
std::string readText(const std::string& path) {
  std::string text;
  if (FILE* f = std::fopen(path.c_str(), "r")) {
    char buf[4096];
    for (size_t n; (n = std::fread(buf, 1, sizeof buf, f)) > 0;) text.append(buf, n);
    std::fclose(f);
  }
  return text;
}

// This guest's distribution folder (DESIGN.md §17): virtiofs tag "distribution", mounted here (the
// share holds the empty folder) the first time the agent needs it.
constexpr const char* kDistribution = "/run/sentinel/distribution";

// What this distribution's folder says (DESIGN.md §8): how it reports boot done, powers off and
// installs packages. Null when the guest has no distribution folder.
const json::Value& distribution() {
  static const json::Value d = [] {
    if (::mount("distribution", kDistribution, "virtiofs", MS_RDONLY, nullptr) != 0 && errno != EBUSY)
      std::fprintf(stderr, "sentineld-agent: cannot mount the distribution folder: %s\n", std::strerror(errno));
    std::string text = readText(std::string(kDistribution) + "/distribution.json");
    if (text.empty()) {
      std::fprintf(stderr, "sentineld-agent: no distribution.json in %s\n", kDistribution);
      return json::Value();
    }
    return json::parse(text);
  }();
  return d;
}

std::vector<std::string> strings(const json::Value& list) {
  std::vector<std::string> out;
  if (!list.isNull())
    for (auto& v : list.arr()) out.push_back(v.str());
  return out;
}

const std::vector<std::string> kEnvironment(std::begin(kEnv), std::end(kEnv) - 1);

bool initReady() {
  const json::Value& ready = distribution()["ready"];
  return !ready.isNull() && runAndWait(strings(ready), kEnvironment, true) == 0;
}

bool defaultRoute() {
  FILE* f = std::fopen("/proc/net/route", "r");
  if (!f) return false;
  char line[256];
  bool found = false;
  while (!found && std::fgets(line, sizeof line, f)) {
    char iface[32], dest[16];
    found = std::sscanf(line, "%31s %15s", iface, dest) == 2 && std::strcmp(dest, "00000000") == 0;
  }
  std::fclose(f);
  return found;
}

void status(int fd) {
  send(fd, Output, json::dump(json::Object{{"ready", initReady() && defaultRoute()}}));
  exitFrame(fd, {{"code", 0}});
}

// --- run --------------------------------------------------------------------------------

// A pty pair whose descriptors never leak into other connections' children (each connection is a
// thread, so a concurrent fork would otherwise inherit them and keep the session open).
bool openPty(int& master, int& slave, const winsize& ws) {
  master = posix_openpt(O_RDWR | O_NOCTTY | O_CLOEXEC);
  if (master < 0 || grantpt(master) != 0 || unlockpt(master) != 0) return false;
  slave = ::open(ptsname(master), O_RDWR | O_NOCTTY | O_CLOEXEC);
  return slave >= 0 && ioctl(master, TIOCSWINSZ, &ws) == 0;
}

void run(int fd, const json::Value& req) {
  std::vector<std::string> args;
  for (auto& a : req["argv"].arr()) args.push_back(a.str());
  if (args.empty()) return exitFrame(fd, {{"error", "argv is empty"}});
  const bool tty = !req["tty"].isNull() && req["tty"].boolean();
  const bool split = !tty && !req["stderr"].isNull() && req["stderr"].str() == "split";
  std::string cwd = req["cwd"].isNull() ? "/root" : req["cwd"].str();
  winsize ws{};
  ws.ws_col = req["cols"].isNull() ? 80 : uint16_t(req["cols"].num());
  ws.ws_row = req["rows"].isNull() ? 24 : uint16_t(req["rows"].num());

  int in[2] = {-1, -1}, out[2] = {-1, -1}, err[2] = {-1, -1}, master = -1, slave = -1;
  if (tty ? !openPty(master, slave, ws)
          : (pipe2(in, O_CLOEXEC) != 0 || pipe2(out, O_CLOEXEC) != 0 || (split && pipe2(err, O_CLOEXEC) != 0)))
    return exitFrame(fd, {{"error", std::string("cannot create pipes: ") + strerror(errno)}});

  std::vector<char*> argv;
  for (auto& a : args) argv.push_back(a.data());
  argv.push_back(nullptr);
  pid_t pid = fork();
  if (pid == 0) {
    setsid();
    if (tty) {
      ioctl(slave, TIOCSCTTY, 0);
      dup2(slave, 0); dup2(slave, 1); dup2(slave, 2);
    } else {
      dup2(in[0], 0); dup2(out[1], 1); dup2(split ? err[1] : out[1], 2);
    }
    signal(SIGPIPE, SIG_DFL);
    if (chdir(cwd.c_str()) != 0) _exit(126);
    execvpe(argv[0], argv.data(), const_cast<char* const*>(kEnv));
    _exit(127);
  }
  int childIn = tty ? master : in[1], childOut = tty ? master : out[0], childErr = split ? err[0] : -1;
  if (tty) ::close(slave);
  else { ::close(in[0]); ::close(out[1]); if (split) ::close(err[1]); }
  if (pid < 0) return exitFrame(fd, {{"error", "fork failed"}});
  int pidfd = int(syscall(SYS_pidfd_open, pid, 0));
  fcntl(childIn, F_SETFL, O_NONBLOCK);  // stdin is buffered so output keeps flowing (no deadlock)
  send(fd, Started, "", 0);

  std::string stdinBuf;
  bool stdinEof = false, exited = false, alive = true;
  int wstatus = 0;
  char buf[64 * 1024];
  while (childOut >= 0 || childErr >= 0 || !exited) {
    // Read sentineld's frames only while the stdin buffer is small (backpressure to the client).
    pollfd p[5] = {{alive && stdinBuf.size() < kMaxFrame ? fd : -1, POLLIN, 0},
                   {childOut, POLLIN, 0},
                   {childErr, POLLIN, 0},
                   {exited ? -1 : pidfd, POLLIN, 0},
                   {stdinBuf.empty() ? -1 : childIn, POLLOUT, 0}};
    // Once the process has exited, background holders of its output get a short grace period.
    int n = poll(p, 5, exited ? 100 : -1);
    if (n < 0) { if (errno == EINTR) continue; break; }
    if (n == 0) break;  // exited and the grace period passed
    if (p[0].revents) {  // frames from sentineld; EOF or a bad frame kills the process
      uint8_t type;
      std::string data;
      if (!receive(fd, type, data)) { ::kill(-pid, SIGKILL); alive = false; continue; }
      if (type == Stdin && data.empty()) {
        if (tty) stdinBuf += '\x04';  // a terminal's end of input is ^D
        else stdinEof = true;
      } else if (type == Stdin) {
        stdinBuf += data;
      } else if (type == Resize && tty && data.size() == 4) {
        ws.ws_col = uint16_t(uint8_t(data[0]) << 8 | uint8_t(data[1]));
        ws.ws_row = uint16_t(uint8_t(data[2]) << 8 | uint8_t(data[3]));
        ioctl(master, TIOCSWINSZ, &ws);
      } else if (type != Resize) {
        ::kill(-pid, SIGKILL);
        alive = false;
      }
    }
    if (p[4].revents && childIn >= 0) {
      ssize_t w = ::write(childIn, stdinBuf.data(), stdinBuf.size());
      if (w > 0) stdinBuf.erase(0, size_t(w));
      else if (w < 0 && errno != EAGAIN && errno != EINTR) stdinBuf.clear();  // the process closed stdin
    }
    if (stdinEof && stdinBuf.empty() && !tty && childIn >= 0) { ::close(childIn); childIn = -1; }
    for (int i : {1, 2}) {
      if (!p[i].revents) continue;
      int& src = i == 1 ? childOut : childErr;
      ssize_t r = ::read(src, buf, sizeof buf);
      if (r > 0) { if (alive) send(fd, i == 1 ? Output : Stderr, buf, size_t(r)); }
      else if (r == 0 || (errno != EINTR && errno != EAGAIN)) { if (src != master) ::close(src); src = -1; }  // a pty reports EIO at exit
    }
    if (p[3].revents) {
      waitpid(pid, &wstatus, 0);
      exited = true;
    }
  }
  ::kill(-pid, SIGKILL);  // anything the process left behind in its group
  if (!exited) waitpid(pid, &wstatus, 0);
  for (int f : {master, childIn == master ? -1 : childIn, childOut == master ? -1 : childOut, childErr, pidfd})
    if (f >= 0) ::close(f);
  if (alive) exitFrame(fd, {{"code", WIFSIGNALED(wstatus) ? 128 + WTERMSIG(wstatus) : WEXITSTATUS(wstatus)}});
}

// --- shutdown ---------------------------------------------------------------------------

void shutdown(int fd) {
  exitFrame(fd, {{"code", 0}});
  ::close(fd);
  runAndWait(strings(distribution()["poweroff"]), kEnvironment, true);
}

// --- mount ------------------------------------------------------------------------------

// Mounts the virtiofs `project` share at the project's host path.
void mount(int fd, const json::Value& req) {
  const std::string path = req["path"].str();
  if (!validProjectPath(path)) return exitFrame(fd, {{"error", "invalid project path"}});
  for (size_t i = 1; i <= path.size(); ++i)
    if (i == path.size() || path[i] == '/') ::mkdir(path.substr(0, i).c_str(), 0755);
  if (::mount("project", path.c_str(), "virtiofs", MS_NOSUID | MS_NODEV, nullptr) != 0 && errno != EBUSY)
    return exitFrame(fd, {{"error", std::string("mount failed: ") + strerror(errno)}});
  exitFrame(fd, {{"code", 0}});
}

// --- forward ----------------------------------------------------------------------------

// Bridges the connection to a guest TCP port: `started`, then raw bytes both ways until both
// sides have closed. Takes ownership of fd.
void forward(int fd, const json::Value& req) {
  const double port = req["port"].num();
  if (port < 1 || port > 65535 || port != double(int(port))) { exitFrame(fd, {{"error", "invalid port"}}); ::close(fd); return; }
  int tcp = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(uint16_t(port));
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (tcp < 0 || connect(tcp, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
    exitFrame(fd, {{"error", std::string("connect failed: ") + strerror(errno)}});
    if (tcp >= 0) ::close(tcp);
    ::close(fd);
    return;
  }
  send(fd, Started, "", 0);
  // One poll loop over non-blocking sockets, one 256 KiB buffer per direction, so neither
  // direction waits on the other; a side's EOF is passed on as a half-close.
  for (int s : {fd, tcp}) fcntl(s, F_SETFL, fcntl(s, F_GETFL) | O_NONBLOCK);
  struct Dir { int from, to; std::vector<char> buf = std::vector<char>(256 * 1024); size_t off = 0, len = 0; bool eof = false; };
  Dir d[2] = {{fd, tcp}, {tcp, fd}};
  while (!(d[0].eof && !d[0].len && d[1].eof && !d[1].len)) {
    pollfd p[4];
    for (int i = 0; i < 2; ++i) {
      p[2 * i] = {d[i].eof || d[i].len ? -1 : d[i].from, POLLIN, 0};
      p[2 * i + 1] = {d[i].len ? d[i].to : -1, POLLOUT, 0};
    }
    if (poll(p, 4, -1) < 0) { if (errno == EINTR) continue; break; }
    bool failed = false;
    for (int i = 0; i < 2 && !failed; ++i) {
      Dir& x = d[i];
      if (p[2 * i].revents) {
        ssize_t n = ::read(x.from, x.buf.data(), x.buf.size());
        if (n > 0) x.off = 0, x.len = size_t(n);
        else if (n == 0 || (errno != EINTR && errno != EAGAIN)) { x.eof = true; ::shutdown(x.to, SHUT_WR); }
      }
      if (p[2 * i + 1].revents && x.len) {
        ssize_t n = ::write(x.to, x.buf.data() + x.off, x.len);
        if (n > 0) x.off += size_t(n), x.len -= size_t(n);
        else if (errno != EINTR && errno != EAGAIN) failed = true;
        if (!x.len && x.eof) ::shutdown(x.to, SHUT_WR);
      }
    }
    if (failed) break;
  }
  ::close(tcp);
  ::close(fd);
}

// --- install ----------------------------------------------------------------------------
// `sentineld-agent install PACKAGE...`, run in the guest by sentineld's provisioning: the
// distribution's package manager through the host's shared package cache (DESIGN.md §8), a no-op
// for packages already installed. Package output goes to stdout and stderr.

constexpr const char* kCache = "/run/sentinel-cache";

// Downloads making progress: bytes landing in the cache or on the root file system.
uint64_t progress(const std::string& watch) {
  uint64_t bytes = 0;
  std::error_code ec;
  for (auto it = std::filesystem::recursive_directory_iterator(watch, std::filesystem::directory_options::skip_permission_denied, ec);
       !ec && it != std::filesystem::recursive_directory_iterator(); it.increment(ec))
    if (it->is_regular_file(ec)) bytes += it->file_size(ec);
  struct statvfs fs{};
  if (statvfs("/", &fs) == 0) bytes += uint64_t(fs.f_blocks - fs.f_bfree) * fs.f_frsize;
  return bytes;
}

// A download stall watchdog: a package manager without a network timeout (apk) hangs forever on a
// mirror connection that goes silent. With no progress for 90 s the command is stopped and run again
// (finished downloads stay in the cache); any failure is retried too. At most 4 attempts. Only for
// steps that are safe to stop: never while packages are being installed.
int watched(const std::vector<std::string>& args, const std::vector<std::string>& env, const std::string& watch) {
  int code = 1;
  for (int attempt = 1; attempt <= 4; ++attempt) {
    pid_t pid = spawn(args, env);
    if (pid < 0) return 127;
    uint64_t last = progress(watch);
    int idle = 0, status = 0;
    bool stalled = false;
    while (waitpid(pid, &status, WNOHANG) == 0) {
      sleep(5);
      uint64_t now = progress(watch);
      idle = now == last ? idle + 5 : 0;
      last = now;
      if (idle >= 90) {
        std::fprintf(stderr, "sentineld-agent install: no download progress for 90 s, retrying\n");
        ::kill(-pid, SIGKILL);
        waitpid(pid, &status, 0);
        stalled = true;
        break;
      }
    }
    code = stalled ? 1 : exitCode(status);
    if (code == 0) return 0;
  }
  return code;
}

// The distribution's `packages` commands (DESIGN.md §8). An argument "{cache}" stands for its
// `cache` arguments (`no_cache` without the cache), "{cache}" inside one for the cache folder.
int install(const std::vector<std::string>& packages) {
  if (packages.empty()) return 0;
  const json::Value& pm = distribution()["packages"];
  if (pm.isNull()) return 2;
  std::vector<std::string> env = kEnvironment, with = strings(pm["no_cache"]);
  for (auto& e : strings(pm["env"])) env.push_back(e);
  std::string cache;
  ::mkdir(kCache, 0755);
  if (::mount("sentinel-cache", kCache, "virtiofs", 0, nullptr) == 0 || errno == EBUSY) {
    cache = kCache;
    with = strings(pm["cache"]);
    for (auto& dir : strings(pm["cache_dirs"])) std::filesystem::create_directories(cache + "/" + dir);
  }
  auto command = [&](const json::Value& c, bool withPackages = true) {
    std::vector<std::string> out;
    auto add = [&](std::string arg) {
      for (size_t at; (at = arg.find("{cache}")) != std::string::npos;) arg.replace(at, 7, cache);
      out.push_back(std::move(arg));
    };
    for (auto& arg : strings(c))
      if (arg == "{cache}") for (auto& w : with) add(w);
      else add(arg);
    if (withPackages) out.insert(out.end(), packages.begin(), packages.end());
    return out;
  };
  // A package file (a path) is always installed; named packages only when one is missing.
  bool files = std::any_of(packages.begin(), packages.end(), [](auto& p) { return p.find('/') != std::string::npos; });
  if (!files && runAndWait(command(pm["installed"]), env, true) == 0) return 0;
  const std::string watch = cache.empty() ? "/var/cache" : cache;
  if (!pm["prepare"].isNull())
    for (auto& c : pm["prepare"].arr()) runAndWait(command(c, false), env);
  // The package index is refreshed once per boot (/run starts empty each boot).
  if (!pm["refresh"].isNull() && access("/run/sentinel-packages-refreshed", F_OK) != 0) {
    if (int code = watched(command(pm["refresh"], false), env, watch); code != 0) return code;
    ::close(::open("/run/sentinel-packages-refreshed", O_WRONLY | O_CREAT | O_CLOEXEC, 0644));
  }
  // `fetch` downloads under the stall watchdog, so it must be safe to stop; `install`, when there
  // is one, then installs from the cache with nothing to interrupt it.
  if (int code = watched(command(pm["fetch"]), env, watch); code != 0 || pm["install"].isNull()) return code;
  return runAndWait(command(pm["install"]), env);
}

// --- keyboard ---------------------------------------------------------------------------
// The viewer's keyboard layouts (DESIGN.md §8): the ones the guest can compile, applied to the
// running desktop the distribution's own way (its folder's desktops/<desktop>/keyboard, run as
// the desktop user). The reply carries the key chords computer use types with on the first.

bool xkbValue(const std::string& s) {
  return s.size() <= 256 &&
         std::all_of(s.begin(), s.end(), [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || std::strchr("_:+,.-", c); });
}

std::vector<std::string> commaList(const std::string& s) {
  std::vector<std::string> out(1);
  for (char c : s)
    if (c == ',') out.emplace_back();
    else out.back() += c;
  return out;
}

std::string utf8(uint32_t c) {
  std::string s;
  if (c < 0x80) s += char(c);
  else if (c < 0x800) s += {char(0xC0 | c >> 6), char(0x80 | (c & 0x3F))};
  else if (c < 0x10000) s += {char(0xE0 | c >> 12), char(0x80 | (c >> 6 & 0x3F)), char(0x80 | (c & 0x3F))};
  else s += {char(0xF0 | c >> 18), char(0x80 | (c >> 12 & 0x3F)), char(0x80 | (c >> 6 & 0x3F)), char(0x80 | (c & 0x3F))};
  return s;
}

xkb_keymap* compile(xkb_context* context, const std::string& model, const std::string& layout, const std::string& variant,
                    const std::string& options) {
  const xkb_rule_names names{"evdev", model.c_str(), layout.c_str(), variant.c_str(), options.c_str()};
  return xkb_keymap_new_from_names(context, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
}

// Character or keysym name -> the evdev key codes that type it, unmodified keys preferred; the
// modifiers are ordinary keys (Shift 42, AltGr 100).
json::Object chords(xkb_keymap* map) {
  xkb_state* state = xkb_state_new(map);
  const xkb_mod_index_t shift = xkb_keymap_mod_get_index(map, XKB_MOD_NAME_SHIFT), level3 = xkb_keymap_mod_get_index(map, "Mod5");
  const std::vector<std::pair<xkb_mod_index_t, int>> sets[] = {{}, {{shift, 42}}, {{level3, 100}}, {{shift, 42}, {level3, 100}}};
  json::Object found;
  for (auto& modifiers : sets) {
    xkb_mod_mask_t mask = 0;
    bool usable = true;
    for (auto& [index, _] : modifiers) {
      if (index >= 32) usable = false;
      else mask |= 1u << index;
    }
    if (!usable) continue;
    xkb_state_update_mask(state, mask, 0, 0, 0, 0, 0);
    for (int code = 1; code < 256; ++code) {
      const xkb_keysym_t symbol = xkb_state_key_get_one_sym(state, xkb_keycode_t(code + 8));
      if (symbol == XKB_KEY_NoSymbol) continue;
      json::Array chord;
      for (auto& [_, key] : modifiers) chord.emplace_back(double(key));
      chord.emplace_back(double(code));
      char name[128];
      if (xkb_keysym_get_name(symbol, name, sizeof name) > 0) found.emplace(name, chord);
      const uint32_t character = xkb_keysym_to_utf32(symbol);
      if (character >= 32 && character != 127) found.emplace(utf8(character), chord);
    }
  }
  xkb_state_unref(state);
  return found;
}

// The running desktop's id: /etc/sentinel/desktop, written by sentineld when it starts the desktop.
std::string desktopId() {
  const std::string text = "\n" + readText("/etc/sentinel/desktop");
  std::string id;
  if (size_t at = text.find("\nSENTINEL_DESKTOP="); at != std::string::npos)
    for (size_t i = at + 18; i < text.size() && text[i] != '\n'; ++i)
      if (text[i] != '"') id += text[i];
  return id.find('/') == std::string::npos ? id : std::string();
}

// Runs `args` as the desktop session's user, with its environment (session.sh's session.json) and
// `extra`, `input` on its stdin; its stdout and stderr into `out` and `err` (each up to 1 MiB).
// Stopped after `seconds`. What it leaves running in the background (a clipboard owner) stays.
int asDesktopUser(const std::vector<std::string>& args, const std::vector<std::string>& extra, const std::string& input,
                  std::string& out, std::string& err, int seconds) {
  const std::string text = readText("/run/sentinel-desktop/session.json");
  if (text.empty()) { err = "the desktop session is not running"; return 1; }
  const json::Value session = json::parse(text);
  const json::Value& user = session["user"];
  const uid_t uid = uid_t(user["uid"].num());
  const gid_t gid = gid_t(user["gid"].num());
  std::vector<gid_t> groups;
  for (auto& g : user["groups"].arr()) groups.push_back(gid_t(g.num()));
  const std::string home = user["home"].str();
  const passwd* account = getpwuid(uid);
  const std::string name = account ? account->pw_name : "sentinel";
  std::vector<std::string> env{"PATH=/usr/local/bin:/usr/bin:/bin", "HOME=" + home, "USER=" + name, "LOGNAME=" + name};
  if (auto* vars = session["environment"].obj())
    for (auto& [key, value] : *vars) env.push_back(key + "=" + value.str());
  env.insert(env.end(), extra.begin(), extra.end());
  int in[2], o[2], e[2];
  if (pipe2(in, O_CLOEXEC) != 0) return 127;
  if (pipe2(o, O_CLOEXEC) != 0) { ::close(in[0]); ::close(in[1]); return 127; }
  if (pipe2(e, O_CLOEXEC) != 0) { for (int f : {in[0], in[1], o[0], o[1]}) ::close(f); return 127; }
  pid_t pid = fork();
  if (pid == 0) {
    setpgid(0, 0);
    dup2(in[0], 0);
    dup2(o[1], 1);
    dup2(e[1], 2);
    if (setgroups(groups.size(), groups.data()) != 0 || setgid(gid) != 0 || setuid(uid) != 0 || chdir(home.c_str()) != 0) _exit(126);
    std::vector<char*> argv, envp;
    for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
    for (auto& v : env) envp.push_back(const_cast<char*>(v.c_str()));
    argv.push_back(nullptr);
    envp.push_back(nullptr);
    signal(SIGPIPE, SIG_DFL);
    execvpe(argv[0], argv.data(), envp.data());
    _exit(127);
  }
  for (int f : {in[0], o[1], e[1]}) ::close(f);
  if (pid < 0) { for (int f : {in[1], o[0], e[0]}) ::close(f); return 127; }
  int writeFd = in[1];
  size_t written = 0;
  if (input.empty()) { ::close(writeFd); writeFd = -1; }
  else fcntl(writeFd, F_SETFL, O_NONBLOCK);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
  bool timedOut = false;
  int readers[2] = {o[0], e[0]};
  std::string* sinks[2] = {&out, &err};
  while (readers[0] >= 0 || readers[1] >= 0) {
    const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count();
    if (left <= 0) { timedOut = true; break; }
    pollfd fds[3] = {{readers[0], POLLIN, 0}, {readers[1], POLLIN, 0}, {writeFd, POLLOUT, 0}};
    if (::poll(fds, 3, int(left)) < 0) {
      if (errno == EINTR) continue;
      break;
    }
    if (writeFd >= 0 && fds[2].revents) {
      ssize_t n = ::write(writeFd, input.data() + written, input.size() - written);
      if (n > 0) written += size_t(n);
      if ((n < 0 && errno != EAGAIN && errno != EINTR) || written == input.size()) { ::close(writeFd); writeFd = -1; }
    }
    for (int i = 0; i < 2; ++i) {
      if (readers[i] < 0 || !fds[i].revents) continue;
      char buf[4096];
      ssize_t n = ::read(readers[i], buf, sizeof buf);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) { ::close(readers[i]); readers[i] = -1; continue; }
      if (sinks[i]->size() < (1u << 20)) sinks[i]->append(buf, size_t(n));
    }
  }
  for (int f : {writeFd, readers[0], readers[1]})
    if (f >= 0) ::close(f);
  if (timedOut) ::kill(-pid, SIGKILL);
  int status = 0;
  waitpid(pid, &status, 0);
  if (timedOut) err += "\n(timed out after " + std::to_string(seconds) + " s)";
  return timedOut ? 124 : exitCode(status);
}

// A script of the running desktop in the distribution's folder (desktops/<desktop>/<name>), or
// why there is none.
std::string desktopScript(const std::string& name, std::string& why) {
  const std::string desktop = desktopId();
  if (desktop.empty()) { why = "the workspace has no desktop"; return {}; }
  distribution();  // its folder mounted
  const std::string script = std::string(kDistribution) + "/desktops/" + desktop + "/" + name;
  if (access(script.c_str(), R_OK) == 0) return script;
  why = "the " + desktop + " desktop has no " + name + " support in this distribution";
  return {};
}

std::string tail(const std::string& s, size_t n = 400) { return s.substr(s.size() - std::min(s.size(), n)); }

void keyboard(int fd, const json::Value& req) {
  auto field = [&](const char* name) { return req[name].isNull() ? std::string() : req[name].str(); };
  std::string model = field("model"), layout = field("layout"), variant = field("variant"), options = field("options");
  if (!xkbValue(model) || !xkbValue(layout) || !xkbValue(variant) || !xkbValue(options))
    return exitFrame(fd, {{"error", "invalid XKB keyboard configuration"}});
  if (model.empty()) model = "pc105";
  std::string why;
  const std::string script = desktopScript("keyboard", why);
  if (script.empty()) return exitFrame(fd, {{"error", why}});

  xkb_context* context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
  if (!context) return exitFrame(fd, {{"error", "could not initialize the keyboard configuration"}});
  // Only the layouts this guest's XKB data has.
  std::vector<std::string> layouts = commaList(layout), variants = commaList(variant);
  variants.resize(layouts.size());
  std::vector<std::pair<std::string, std::string>> kept;
  for (size_t i = 0; i < layouts.size(); ++i)
    if (xkb_keymap* map = layouts[i].empty() ? nullptr : compile(context, model, layouts[i], variants[i], "")) {
      kept.emplace_back(layouts[i], variants[i]);
      xkb_keymap_unref(map);
    }
  if (kept.empty()) {
    xkb_context_unref(context);
    return exitFrame(fd, {{"error", "none of the keyboard layouts is installed in this workspace"}});
  }
  if (kept.size() == 1) {  // a single layout needs no switching shortcut
    std::string rest;
    for (auto& o : commaList(options))
      if (!o.empty() && o.rfind("grp:", 0) != 0) rest += (rest.empty() ? "" : ",") + o;
    options = rest;
  }
  layout.clear();
  variant.clear();
  for (size_t i = 0; i < kept.size(); ++i) {
    layout += (i ? "," : "") + kept[i].first;
    variant += (i ? "," : "") + kept[i].second;
  }
  json::Object keys;
  if (xkb_keymap* first = compile(context, model, kept[0].first, kept[0].second, options)) {
    keys = chords(first);
    xkb_keymap_unref(first);
  }
  xkb_context_unref(context);

  // Applied every time: some of it (X11's live keymap) does not outlive the session.
  std::string out, err;
  if (int code = asDesktopUser({"sh", script},
                               {"XKB_RULES=evdev", "XKB_MODEL=" + model, "XKB_LAYOUT=" + layout, "XKB_VARIANT=" + variant,
                                "XKB_OPTIONS=" + options},
                               "", out, err, 30);
      code != 0)
    return exitFrame(fd, {{"error", "applying the keyboard layouts failed (exit " + std::to_string(code) + "): " + tail(out + err)}});
  send(fd, Output, json::dump(json::Object{{"layout", layout}, {"variant", variant}, {"keys", std::move(keys)}}));
  exitFrame(fd, {{"code", 0}});
}

// --- clipboard --------------------------------------------------------------------------
// One clipboard transfer of the running desktop (DESIGN.md §8), through the distribution's
// desktops/<desktop>/clipboard, as the desktop user: `write` puts stdin on the clipboard, `read`
// prints it. Text only, up to 256 KiB, without NUL.

constexpr size_t kClipboardMax = 256 * 1024;

void clipboard(int fd, const json::Value& req) {
  const std::string action = req["action"].isNull() ? "" : req["action"].str();
  if (action != "read" && action != "write") return exitFrame(fd, {{"error", "clipboard action must be read or write"}});
  std::string text;
  if (action == "write") {
    text = req["text"].isNull() ? "" : req["text"].str();
    if (text.size() > kClipboardMax || text.find('\0') != std::string::npos)
      return exitFrame(fd, {{"error", "clipboard text must be at most 256 KiB without NUL"}});
  }
  std::string why;
  const std::string script = desktopScript("clipboard", why);
  if (script.empty()) return exitFrame(fd, {{"error", why}});
  std::string out, err;
  if (int code = asDesktopUser({"sh", script, action}, {"LC_ALL=C.UTF-8"}, text, out, err, 10); code != 0)
    return exitFrame(fd, {{"error", "the clipboard " + action + " failed (exit " + std::to_string(code) + "): " + tail(err)}});
  if (out.size() > kClipboardMax) return exitFrame(fd, {{"error", "clipboard text exceeds 256 KiB"}});
  send(fd, Output, json::dump(action == "read" ? json::Object{{"text", out}} : json::Object{}));
  exitFrame(fd, {{"code", 0}});
}

// --- type -------------------------------------------------------------------------------
// Types text into the running desktop for computer use (DESIGN.md §22), through the distribution's
// desktops/<desktop>/type (text on stdin) as the desktop user: desktops whose distribution data
// says `paste` take it from the clipboard instead, and have none.

void typeText(int fd, const json::Value& req) {
  const std::string text = req["text"].isNull() ? "" : req["text"].str();
  if (text.size() > 4000 || text.find('\0') != std::string::npos)
    return exitFrame(fd, {{"error", "text must be at most 4000 bytes without NUL"}});
  std::string why;
  const std::string script = desktopScript("type", why);
  if (script.empty()) return exitFrame(fd, {{"error", why}});
  std::string out, err;
  if (int code = asDesktopUser({"sh", script}, {"LC_ALL=C.UTF-8"}, text, out, err, 20); code != 0)
    return exitFrame(fd, {{"error", "typing failed (exit " + std::to_string(code) + "): " + tail(out + err)}});
  exitFrame(fd, {{"code", 0}});
}

// --- wallpaper --------------------------------------------------------------------------
// One of the share's wallpapers (wallpapers/<design>.png, DESIGN.md §8) on the running desktop:
// copied into the desktop user's home, then the distribution's desktops/<desktop>/wallpaper.

void wallpaper(int fd, const json::Value& req) {
  const std::string design = req["design"].isNull() ? "" : req["design"].str();
  if (design.empty() || design.size() > 64 ||
      !std::all_of(design.begin(), design.end(), [](char c) { return std::islower(static_cast<unsigned char>(c)) || std::isdigit(static_cast<unsigned char>(c)) || c == '-'; }))
    return exitFrame(fd, {{"error", "invalid wallpaper design"}});
  const std::string image = readText("/run/sentinel/wallpapers/" + design + ".png");
  if (image.empty()) return exitFrame(fd, {{"error", "no wallpaper " + design}});
  std::string why;
  const std::string script = desktopScript("wallpaper", why);
  if (script.empty()) return exitFrame(fd, {{"error", why}});
  // Named by its size too: a changed image gets a new path, so no desktop shows a cached old one.
  const std::string file = design + "-" + std::to_string(image.size()) + ".png";
  std::string out, err;
  if (int code = asDesktopUser({"sh", "-c", "dir=\"$HOME/.local/share/backgrounds/sentinel\" && mkdir -p \"$dir\" && cat > \"$dir/$1\"", "sh", file},
                               {}, image, out, err, 30);
      code != 0)
    return exitFrame(fd, {{"error", "the wallpaper could not be saved: " + tail(err)}});
  const std::string home = [] {
    try { return json::parse(readText("/run/sentinel-desktop/session.json"))["user"]["home"].str(); } catch (...) { return std::string(); }
  }();
  out.clear();
  err.clear();
  if (int code = asDesktopUser({"sh", script, home + "/.local/share/backgrounds/sentinel/" + file}, {}, "", out, err, 30); code != 0)
    return exitFrame(fd, {{"error", "the wallpaper could not be applied (exit " + std::to_string(code) + "): " + tail(out + err)}});
  exitFrame(fd, {{"code", 0}});
}

void connection(int fd) {
  uint8_t type;
  std::string payload;
  if (!receive(fd, type, payload, kMaxRequest) || type != Request) { ::close(fd); return; }
  try {
    json::Value req = json::parse(payload);
    const std::string& op = req["op"].str();
    if (op == "status") status(fd);
    else if (op == "run") run(fd, req);
    else if (op == "shutdown") return shutdown(fd);
    else if (op == "mount") mount(fd, req);
    else if (op == "forward") return forward(fd, req);
    else if (op == "keyboard") keyboard(fd, req);
    else if (op == "clipboard") clipboard(fd, req);
    else if (op == "wallpaper") wallpaper(fd, req);
    else if (op == "type") typeText(fd, req);
    else exitFrame(fd, {{"error", "unknown op " + op}});
  } catch (const std::exception& e) {
    exitFrame(fd, {{"error", e.what()}});
  }
  ::close(fd);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc > 1 && std::strcmp(argv[1], "install") == 0) return install({argv + 2, argv + argc});
  signal(SIGPIPE, SIG_IGN);
  // Kernel warnings and errors (an ext4 checksum failure, an oops) reach the console, which sentineld
  // keeps on the host per boot: distributions lower the console level at boot, and the kernel's own
  // account of a failure would otherwise exist only inside a guest that may no longer run.
  klogctl(8 /* SYSLOG_ACTION_CONSOLE_LEVEL */, nullptr, 5);
  int s = socket(AF_VSOCK, SOCK_STREAM | SOCK_CLOEXEC, 0);
  sockaddr_vm addr{};
  addr.svm_family = AF_VSOCK;
  addr.svm_cid = VMADDR_CID_ANY;
  addr.svm_port = 1;
  if (s < 0 || bind(s, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(s, 64) != 0) {
    std::perror("sentineld-agent: vsock");
    return 1;
  }
  while (true) {
    int fd = accept4(s, nullptr, nullptr, SOCK_CLOEXEC);
    if (fd < 0) { if (errno == EINTR) continue; std::perror("accept"); return 1; }
    std::thread(connection, fd).detach();
  }
}
