// sentineld-media: the one process per Mac that faces viewers (DESIGN.md §6). sentineld spawns it
// with a control socket as fd 3 and hands it each GPU provider's media fd; it relays encoded video
// to admitted viewers and their input back. It holds no workspace state and touches no files.
#include <sandbox.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <csignal>
#include <cstdio>
#include <cstring>
#include <deque>

#include "../core/util/json.hpp"
#include "server.hpp"

using namespace sentineld;
using namespace sentineld::media;

namespace {

constexpr int kControl = 3;
constexpr uint32_t kMaxPayload = 4u << 20;
constexpr uint64_t kTicketMs = 30000, kKeyframeMs = 250, kRotateMs = 6ull * 24 * 3600 * 1000;

uint64_t nowMs() { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / 1000000; }

struct Feed {
  int fd;
  std::string rx;
  std::vector<uint8_t> config, cursor;  // the latest decoder config and guest cursor packets
  std::vector<uint8_t> head;            // both, in front of every key frame
  uint64_t lastKeyframe = 0;
};

struct Ticket {
  std::string workspace;
  uint64_t expires;
};

class Media {
 public:
  Media()
      : server_(loop_, {[this](std::string_view t) { return admit(t); },
                        [this](const std::string& ws, std::span<const uint8_t> p) { control(ws, p); }}) {
    loop_.onReadable(kControl, [this] { readControl(); });
    announce();
  }
  void run() { loop_.run(); }

 private:
  void announce() {
    std::string line = json::dump(json::Object{{"port", int(server_.port())}, {"hash", server_.certificateHash()}});
    line += '\n';
    if (::write(kControl, line.data(), line.size()) != ssize_t(line.size())) _exit(1);
    loop_.after(kRotateMs, [this] {
      server_.rotateCertificate();
      announce();
    });
  }

  // One ticket admits one viewer to one workspace, once, within kTicketMs.
  std::string admit(std::string_view ticket) {
    auto it = tickets_.find(std::string(ticket));
    if (it == tickets_.end()) return {};
    auto t = std::move(it->second);
    tickets_.erase(it);
    return t.expires > nowMs() && feeds_.count(t.workspace) ? t.workspace : std::string();
  }

  // Viewer input and key frame requests, to the workspace's provider. Never blocks: a provider that
  // does not keep up loses input rather than stalling every viewer.
  void control(const std::string& workspace, std::span<const uint8_t> packet) {
    auto it = feeds_.find(workspace);
    if (it == feeds_.end()) return;
    if (packet[0] == kKeyframe) {
      if (nowMs() - it->second.lastKeyframe < kKeyframeMs) return;
      it->second.lastKeyframe = nowMs();
    }
    (void)::send(it->second.fd, packet.data(), packet.size(), MSG_DONTWAIT);
  }

  void readControl() {
    char buf[4096];
    char cmsg[CMSG_SPACE(sizeof(int) * 8)];
    iovec iov{buf, sizeof buf};
    msghdr msg{.msg_iov = &iov, .msg_iovlen = 1, .msg_control = cmsg, .msg_controllen = sizeof cmsg};
    ssize_t n = ::recvmsg(kControl, &msg, 0);
    if (n <= 0) _exit(0);  // sentineld is gone
    for (auto* c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c))
      if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS)
        for (size_t i = 0; i < (c->cmsg_len - CMSG_LEN(0)) / sizeof(int); ++i)
          fds_.push_back(reinterpret_cast<int*>(CMSG_DATA(c))[i]);
    controlRx_.append(buf, size_t(n));
    for (size_t nl; (nl = controlRx_.find('\n')) != std::string::npos;) {
      auto req = json::parse(std::string_view(controlRx_).substr(0, nl));
      controlRx_.erase(0, nl + 1);
      const auto& op = req["op"].str();
      const auto& workspace = req["workspace"].str();
      if (op == "attach" && !fds_.empty()) {
        attach(workspace, fds_.front());
        fds_.pop_front();
      } else if (op == "ticket") {
        tickets_[req["ticket"].str()] = {workspace, nowMs() + kTicketMs};
        std::erase_if(tickets_, [](auto& t) { return t.second.expires <= nowMs(); });
      }
    }
  }

  void attach(const std::string& workspace, int fd) {
    detach(workspace);
    int size = 8 << 20;
    setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof size);
    feeds_[workspace] = Feed{.fd = fd, .rx = {}, .config = {}, .cursor = {}, .head = {}};
    loop_.onReadable(fd, [this, workspace] { readFeed(workspace); });
  }

  void detach(const std::string& workspace) {
    auto it = feeds_.find(workspace);
    if (it == feeds_.end()) return;
    loop_.remove(it->second.fd);
    ::close(it->second.fd);
    feeds_.erase(it);
    server_.drop(workspace);
  }

  void readFeed(const std::string& workspace) {
    Feed& f = feeds_.at(workspace);
    char buf[256 * 1024];
    ssize_t n = ::recv(f.fd, buf, sizeof buf, MSG_DONTWAIT);
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) return detach(workspace);
    if (n < 0) return;
    f.rx.append(buf, size_t(n));
    size_t at = 0;
    while (f.rx.size() - at >= kHeader) {
      auto* h = reinterpret_cast<const uint8_t*>(f.rx.data() + at);
      uint32_t len = uint32_t(h[4]) << 24 | uint32_t(h[5]) << 16 | uint32_t(h[6]) << 8 | h[7];
      if (len > kMaxPayload) return detach(workspace);
      if (f.rx.size() - at < kHeader + len) break;
      std::span<const uint8_t> packet(h, kHeader + len);
      if (h[0] == kConfig || h[0] == kCursor) {
        (h[0] == kConfig ? f.config : f.cursor).assign(packet.begin(), packet.end());
        f.head = f.config;
        f.head.insert(f.head.end(), f.cursor.begin(), f.cursor.end());
        if (h[0] == kCursor) server_.relay(workspace, packet);
      } else if (h[0] == kFrame) {
        server_.publish(workspace, f.head, packet, h[1] & kKey);
      } else if (h[0] == kAudio) {
        server_.relay(workspace, packet);
      }
      at += kHeader + len;
    }
    f.rx.erase(0, at);
  }

  Loop loop_;
  Server server_;
  std::map<std::string, Feed> feeds_;
  std::map<std::string, Ticket> tickets_;
  std::string controlRx_;
  std::deque<int> fds_;
};

// No files, no processes: only the sockets it was given and the UDP port it opened.
void enterSandbox() {
  static const char profile[] =
      "(version 1)(deny default)"
      "(allow network-inbound (local udp \"localhost:*\"))"
      "(allow network-outbound (remote udp \"localhost:*\"))"
      "(allow file-read-metadata)(allow sysctl-read)(allow mach-lookup (global-name \"com.apple.system.logger\"))";
  char* error = nullptr;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
  int failed = sandbox_init(profile, 0, &error);
#pragma clang diagnostic pop
  if (failed) {
    std::fprintf(stderr, "sentineld-media: sandbox: %s\n", error);
    _exit(1);
  }
}

}  // namespace

int main() {
  std::signal(SIGPIPE, SIG_IGN);
  try {
    Media media;
    enterSandbox();
    media.run();
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld-media: %s\n", e.what());
    return 1;
  }
}
