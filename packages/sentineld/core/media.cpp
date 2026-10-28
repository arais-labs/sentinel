#include "media.hpp"

#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>

extern char** environ;

namespace sentineld {

Media::Media(Loop& loop, std::string binary) : loop_(loop), binary_(std::move(binary)) { spawn(); }

Media::~Media() {
  if (pid_ > 0) ::kill(pid_, SIGTERM);
  if (fd_ >= 0) ::close(fd_);
}

// The control socket is its fd 3 and the only descriptor it inherits besides stdio; its stderr is
// ours. It exits when the socket closes, so it never outlives sentineld.
void Media::spawn() {
  int sv[2];
  if (::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) return;
  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
  posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0);
  posix_spawn_file_actions_adddup2(&actions, 2, 2);
  posix_spawn_file_actions_adddup2(&actions, sv[1], 3);
  posix_spawnattr_t attr;
  posix_spawnattr_init(&attr);
  posix_spawnattr_setflags(&attr, POSIX_SPAWN_CLOEXEC_DEFAULT);
  const char* argv[] = {binary_.c_str(), nullptr};
  int rc = posix_spawn(&pid_, binary_.c_str(), &actions, &attr, const_cast<char* const*>(argv), environ);
  posix_spawn_file_actions_destroy(&actions);
  posix_spawnattr_destroy(&attr);
  ::close(sv[1]);
  if (rc != 0) {
    ::close(sv[0]);
    pid_ = 0;
    std::fprintf(stderr, "sentineld: cannot start %s: %s\n", binary_.c_str(), std::strerror(rc));
    return;
  }
  fd_ = sv[0];
  fcntl(fd_, F_SETFD, FD_CLOEXEC);
  loop_.onReadable(fd_, [this] { read(); });
}

// Announcements: {"port", "hash"} at start and on every certificate rotation.
void Media::read() {
  char buf[1024];
  ssize_t n = ::read(fd_, buf, sizeof buf);
  if (n < 0 && errno == EINTR) return;
  if (n <= 0) {
    loop_.remove(fd_);
    ::close(fd_);
    fd_ = -1;
    int status = 0;
    ::waitpid(pid_, &status, 0);  // its socket closes as it exits
    std::fprintf(stderr, "sentineld: sentineld-media exited (status %d); restarting\n", status);
    port_ = 0;
    hash_.clear();
    loop_.after(1000, [this] { spawn(); });  // providers started before then have no viewers
    return;
  }
  rx_.append(buf, size_t(n));
  for (size_t nl; (nl = rx_.find('\n')) != std::string::npos;) {
    auto line = json::parse(std::string_view(rx_).substr(0, nl));
    rx_.erase(0, nl + 1);
    port_ = int(line["port"].num());
    hash_ = line["hash"].str();
  }
}

bool Media::send(const std::string& line, int fd) {
  if (fd_ < 0) return false;
  iovec iov{const_cast<char*>(line.data()), line.size()};
  char cmsg[CMSG_SPACE(sizeof(int))] = {};
  msghdr msg{.msg_iov = &iov, .msg_iovlen = 1};
  if (fd >= 0) {
    msg.msg_control = cmsg;
    msg.msg_controllen = sizeof cmsg;
    auto* c = CMSG_FIRSTHDR(&msg);
    c->cmsg_level = SOL_SOCKET;
    c->cmsg_type = SCM_RIGHTS;
    c->cmsg_len = CMSG_LEN(sizeof(int));
    std::memcpy(CMSG_DATA(c), &fd, sizeof fd);
  }
  return ::sendmsg(fd_, &msg, 0) == ssize_t(line.size());
}

int Media::attach(const std::string& workspace) {
  int sv[2];
  if (fd_ < 0 || ::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) return -1;
  int size = 8 << 20;
  setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &size, sizeof size);
  bool sent = send(json::dump(json::Object{{"op", "attach"}, {"workspace", workspace}}) + "\n", sv[1]);
  ::close(sv[1]);
  if (!sent) {
    ::close(sv[0]);
    return -1;
  }
  fcntl(sv[0], F_SETFD, FD_CLOEXEC);
  return sv[0];
}

json::Object Media::display(const std::string& workspace) {
  if (hash_.empty()) return {};
  uint8_t bytes[16];
  arc4random_buf(bytes, sizeof bytes);
  std::string ticket;
  for (uint8_t b : bytes) ticket += {"0123456789abcdef"[b >> 4], "0123456789abcdef"[b & 15]};
  if (!send(json::dump(json::Object{{"op", "ticket"}, {"workspace", workspace}, {"ticket", ticket}}) + "\n")) return {};
  return {{"port", port_}, {"cert_sha256", hash_}, {"ticket", ticket}, {"expires", int64_t(::time(nullptr)) + 30}};
}

}  // namespace sentineld
