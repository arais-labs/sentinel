// The remote update coordinator. The backend uploads a release, starts its sentineld with
// --update-session ROOT over SSH and drives the transaction (app/services/workspaces/machines/remote/update.py):
// one JSON request per line on stdin, one reply per line on stdout.
//
// ROOT/update.lock is the update lease: one updater at a time, released by the OS when SSH drops or
// the coordinator dies, and given up after 60 s without a request on a half-open connection.
// ROOT/state/owner.lock is the store: held by the running sentineld. `activate` takes it, switches
// the release and hands the held lock to the new sentineld (--owner-fd), so no other start can
// slip in between.
#include "update.hpp"

#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <spawn.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

#include "util/json.hpp"

extern char** environ;

namespace sentineld {
namespace {

constexpr int kLeaseSeconds = 60;
constexpr int kInheritedOwner = 198;

int lock(const std::string& path) {
  int fd = ::open(path.c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (fd < 0) throw std::runtime_error("cannot open " + path + ": " + std::strerror(errno));
  if (::flock(fd, LOCK_EX | LOCK_NB) != 0) {
    const int code = errno;
    ::close(fd);
    if (code == EWOULDBLOCK) throw std::runtime_error("Runtime is busy; another service or update owns the lock");
    throw std::runtime_error("cannot lock " + path + ": " + std::strerror(code));
  }
  return fd;
}

json::Value read(const std::string& path) {
  std::ifstream in(path);
  if (!in) return nullptr;
  std::stringstream text;
  text << in.rdbuf();
  return json::parse(text.str());
}

// Atomic and durable: a crash leaves the old file or the new one, never a torn one.
void write(const std::string& path, const json::Value& value) {
  const std::string next = path + ".next", text = json::dump(value);
  int fd = ::open(next.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (fd < 0) throw std::runtime_error("cannot write " + path);
  bool ok = ::write(fd, text.data(), text.size()) == ssize_t(text.size()) && ::fsync(fd) == 0;
  ::close(fd);
  if (!ok || ::rename(next.c_str(), path.c_str()) != 0) throw std::runtime_error("cannot write " + path);
  const std::string parent = path.substr(0, path.rfind('/'));
  if (int dir = ::open(parent.c_str(), O_RDONLY | O_CLOEXEC); dir >= 0) {
    ::fsync(dir);
    ::close(dir);
  }
}

bool ownerFree(const std::string& root) {
  int fd = ::open((root + "/state/owner.lock").c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (fd < 0) throw std::runtime_error("cannot inspect runtime ownership");
  const bool free = ::flock(fd, LOCK_EX | LOCK_NB) == 0;
  const int code = errno;
  ::close(fd);
  if (!free && code != EWOULDBLOCK) throw std::runtime_error("cannot inspect runtime ownership");
  return free;
}

bool inside(const std::string& path, const std::string& dir) {
  return path.compare(0, dir.size(), dir) == 0 && path.find("/../") == std::string::npos &&
         path.find("/./") == std::string::npos && path.find('\0') == std::string::npos;
}

const std::string kExecutable = "/sentineld/bin/sentineld";

// The manifest names its release: ROOT/releases/<release>/sentineld/bin/sentineld (which runs the VMs too).
std::string release(const std::string& root, const json::Value& manifest) {
  auto* protocol = std::get_if<double>(&manifest["updateProtocol"].v);
  auto* exe = std::get_if<std::string>(&manifest["executable"].v);
  if (!protocol || *protocol != 1 || !exe || !inside(*exe, root + "/releases/") ||
      exe->size() <= kExecutable.size() || exe->compare(exe->size() - kExecutable.size(), kExecutable.size(), kExecutable) != 0)
    throw std::runtime_error("Unsupported runtime update manifest");
  std::string dir = exe->substr(0, exe->size() - kExecutable.size());
  if (dir.find('/', root.size() + 10) != std::string::npos)
    throw std::runtime_error("Unsupported runtime update manifest");
  return dir;
}

pid_t launch(const std::string& root, const std::string& exe, int owner) {
  posix_spawn_file_actions_t actions;
  posix_spawnattr_t attr;
  posix_spawn_file_actions_init(&actions);
  posix_spawnattr_init(&attr);
  const std::string log = root + "/service.log";
  posix_spawn_file_actions_adddup2(&actions, owner, kInheritedOwner);
  posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
  posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, log.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
  posix_spawn_file_actions_adddup2(&actions, STDOUT_FILENO, STDERR_FILENO);
  // Its own session: the service outlives this SSH connection.
  posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSID | POSIX_SPAWN_SETSIGDEF);
  sigset_t all;
  sigfillset(&all);
  posix_spawnattr_setsigdefault(&attr, &all);
  const char* home = std::getenv("HOME");
  std::string envHome = std::string("HOME=") + (home ? home : "/");
  const std::string state = root + "/state", cache = root + "/cache", fd = std::to_string(kInheritedOwner);
  const char* argv[] = {exe.c_str(), "--state", state.c_str(), "--cache", cache.c_str(),
                        "--owner-fd", fd.c_str(), nullptr};
  const char* envp[] = {"PATH=/usr/bin:/bin:/usr/sbin:/sbin", "LANG=en_US.UTF-8", envHome.c_str(), nullptr};
  pid_t pid = 0;
  const int rc = posix_spawn(&pid, exe.c_str(), &actions, &attr, const_cast<char**>(argv), const_cast<char**>(envp));
  posix_spawn_file_actions_destroy(&actions);
  posix_spawnattr_destroy(&attr);
  if (rc != 0) throw std::runtime_error(std::string("Cannot launch replacement runtime: ") + std::strerror(rc));
  return pid;
}

// ROOT/current points at the active release: tool and share paths through it never change.
void point(const std::string& root, const std::string& dir) {
  const std::string link = root + "/current", next = link + ".next";
  ::unlink(next.c_str());
  if (::symlink(dir.c_str(), next.c_str()) != 0 || ::rename(next.c_str(), link.c_str()) != 0)
    throw std::runtime_error("cannot switch the active release");
}

void reply(const json::Object& message) {
  std::cout << json::dump(message) << '\n' << std::flush;
}

json::Object handle(const std::string& root, const json::Value& request) {
  const std::string& action = request["action"].str();
  if (action == "heartbeat") return {{"ok", true}};
  if (action == "inspect")
    return {{"owner_free", ownerFree(root)}, {"migrations", json::Array{}},
            {"manifest", read(root + "/manifest.json")}, {"journal", read(root + "/update.json")}};
  if (action == "journal") {
    if (!request["journal"].obj()) throw std::runtime_error("Missing update state");
    write(root + "/update.json", request["journal"]);
    return {{"ok", true}};
  }
  // sentineld's store has had no format change yet: nothing to migrate.
  if (action == "migration_status") return {{"target", json::Array{}}, {"inputs", json::Array{}}};
  if (action == "migration_plan" || action == "migrate") return {{"ok", true}};
  if (action == "activate") {
    const json::Value& manifest = request["manifest"];
    const std::string dir = release(root, manifest);
    int owner = lock(root + "/state/owner.lock");
    try {
      write(root + "/manifest.json", manifest);
      point(root, dir);
      const pid_t pid = launch(root, manifest["executable"].str(), owner);
      ::close(owner);
      return {{"ok", true}, {"pid", int64_t(pid)}};
    } catch (...) {
      ::close(owner);
      throw;
    }
  }
  throw std::runtime_error("Unknown runtime update action");
}

}  // namespace

int updateSession(const char* rootArg) {
  std::signal(SIGHUP, SIG_IGN);
  std::signal(SIGPIPE, SIG_IGN);
  const std::string root = rootArg;
  if (root.empty() || root[0] != '/') {
    reply({{"error", "the runtime root must be an absolute path"}});
    return 2;
  }
  int lease = -1;
  try {
    ::mkdir(root.c_str(), 0700);
    ::mkdir((root + "/state").c_str(), 0700);
    lease = lock(root + "/update.lock");
  } catch (const std::exception& e) {
    reply({{"error", e.what()}});
    return 1;
  }
  ::alarm(kLeaseSeconds);  // SIGALRM ends an abandoned session, releasing the lease
  reply({{"event", "locked"}});
  for (std::string line; std::getline(std::cin, line);) {
    ::alarm(kLeaseSeconds);
    try {
      reply(handle(root, json::parse(line)));
    } catch (const std::exception& e) {
      reply({{"error", e.what()}});
    }
  }
  ::close(lease);
  return 0;
}

}  // namespace sentineld
