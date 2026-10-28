#include "agent.hpp"

#include <cerrno>
#include <functional>
#include <sys/socket.h>
#include <fcntl.h>
#include <memory>
#include <unistd.h>

#include "loop.hpp"
#include "platform.hpp"
#include "util/text.hpp"

namespace sentineld::agent {
namespace {

constexpr size_t kMaxExitJson = 4096;

// Re-encodes a guest exit as {"code":0-255} or {"error":<printable, ≤1 KiB>} (DESIGN.md §13).
std::string exitJson(std::string_view raw) {
  json::Value v;
  try { v = json::parse(raw); } catch (...) {}
  if (auto* c = std::get_if<double>(&v["code"].v); c && *c >= 0 && *c <= 255 && *c == double(int(*c)))
    return json::dump(json::Object{{"code", int(*c)}});
  std::string error;
  if (auto* e = std::get_if<std::string>(&v["error"].v))
    error = printable(*e, 1024);
  return json::dump(json::Object{{"error", error.empty() ? std::string("process failed") : error}});
}
constexpr size_t kRelayHighWater = 4 << 20;

uint32_t length(const std::string& b, size_t at) {
  return uint32_t(uint8_t(b[at + 1])) << 24 | uint32_t(uint8_t(b[at + 2])) << 16 |
         uint32_t(uint8_t(b[at + 3])) << 8 | uint8_t(b[at + 4]);
}

void nonblocking(int fd) { fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK); }

// Writes `data` to a non-blocking socket from the loop, then calls next(); fail() on error.
// The caller's timer bounds how long a guest that stops reading can hold it.
void writeAsync(Loop& loop, int fd, std::string data, std::function<void()> next,
                std::function<void(std::string)> fail) {
  auto pending = std::make_shared<std::string>(std::move(data));
  auto step = std::make_shared<std::function<void()>>();
  *step = [&loop, fd, pending, next, fail, step] {
    while (!pending->empty()) {
      ssize_t w = ::write(fd, pending->data(), pending->size());
      if (w < 0 && errno == EINTR) continue;
      if (w < 0 && errno == EAGAIN) {
        loop.onWritable(fd, [step] { (*step)(); });
        return;
      }
      if (w <= 0) { loop.offWritable(fd); *step = nullptr; return fail("agent unreachable: write failed"); }
      pending->erase(0, size_t(w));
    }
    loop.offWritable(fd);
    auto go = next;
    *step = nullptr;  // breaks the self-reference
    go();
  };
  (*step)();
}

struct Call : std::enable_shared_from_this<Call> {
  Loop& loop;
  int fd = -1;
  size_t cap;
  std::string in;
  uint64_t timer = 0;
  Result result;
  std::function<void(Result)> done;

  Call(Loop& l, size_t c, std::function<void(Result)> d) : loop(l), cap(c), done(std::move(d)) {}

  void finish(std::string error = "") {
    if (!done) return;
    if (!error.empty() && result.error.empty()) result.error = std::move(error);
    loop.cancel(timer);
    if (fd >= 0) { loop.remove(fd); ::close(fd); fd = -1; }
    auto cb = std::move(done);
    done = nullptr;
    cb(std::move(result));
  }

  void keep(std::string& into, const char* data, size_t n) {
    size_t room = into.size() < cap ? cap - into.size() : 0;
    if (n > room) result.truncated = true;
    into.append(data, n < room ? n : room);
  }

  void readable() {
    char buf[64 * 1024];
    ssize_t n = ::read(fd, buf, sizeof buf);
    if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
    if (n <= 0) return finish("agent closed the connection");
    in.append(buf, size_t(n));
    size_t at = 0;
    while (in.size() - at >= 5) {
      uint8_t type = uint8_t(in[at]);
      uint32_t len = length(in, at);
      if (len > kMaxFrame) return finish("agent sent an oversized frame");
      if (in.size() - at - 5 < len) break;
      const char* data = in.data() + at + 5;
      at += 5 + len;
      switch (type) {
        case Started: if (len) return finish("agent sent a malformed started"); break;
        case Output: keep(result.out, data, len); break;
        case Stderr: keep(result.err, data, len); break;
        case Exit: {
          if (len > kMaxExitJson) return finish("agent sent an oversized exit");
          json::Value v = json::parse(std::string_view(data, len));
          if (auto* c = std::get_if<double>(&v["code"].v); c && *c >= 0 && *c <= 255) result.code = int(*c);
          else if (auto* e = std::get_if<std::string>(&v["error"].v)) return finish("agent: " + printable(*e, 1024));
          else return finish("agent sent a malformed exit");
          return finish();
        }
        default: return finish("agent sent an unknown frame");
      }
    }
    in.erase(0, at);
  }
};

}  // namespace

std::string frame(uint8_t type, const std::string& data) {
  const uint32_t n = uint32_t(data.size());
  std::string f{char(type), char(n >> 24), char(n >> 16), char(n >> 8), char(n)};
  return f + data;
}

void call(Loop& loop, Platform& platform, const std::string& vm, const json::Object& request, bool closeStdin,
          size_t cap, uint64_t timeoutMs, std::function<void(Result)> done) {
  auto c = std::make_shared<Call>(loop, cap, std::move(done));
  c->timer = loop.after(timeoutMs, [c] { c->result.timedOut = true; c->finish(); });
  std::string hello = frame(Request, json::dump(request));
  if (closeStdin) hello += frame(Stdin, "");
  platform.connect(vm, kPort, [c, hello](int fd, std::string error) {
    if (!c->done) { if (fd >= 0) ::close(fd); return; }  // timed out while connecting
    if (fd < 0) return c->finish("agent unreachable: " + error);
    c->fd = fd;
    nonblocking(fd);
    writeAsync(c->loop, fd, hello, [c] {
      if (!c->done) return;
      c->loop.onReadable(c->fd, [c] {
        try { c->readable(); } catch (const std::exception& e) { c->finish(std::string("agent: ") + e.what()); }
      });
    }, [c](std::string e) { c->finish(e); });
  });
}

namespace {

// One direction of a relay: frames from `from` are validated and queued for `to`.
struct Relay : std::enable_shared_from_this<Relay> {
  Loop& loop;
  int client, agentFd;
  std::string fromClient, fromAgent;  // partial frames being assembled
  std::string toClient, toAgent;      // validated bytes waiting to be written
  bool exited = false, closed = false;

  Relay(Loop& l, int c, int a) : loop(l), client(c), agentFd(a) {}

  void close() {
    if (closed) return;
    closed = true;
    loop.remove(client);
    loop.remove(agentFd);
    ::close(client);
    ::close(agentFd);  // the agent kills the process when its connection closes
  }

  // Moves complete frames from `buf` to `out`, allowing only `allowed` types.
  bool frames(std::string& buf, std::string& out, bool fromGuest) {
    size_t at = 0;
    while (buf.size() - at >= 5) {
      uint8_t type = uint8_t(buf[at]);
      uint32_t len = length(buf, at);
      bool ok = fromGuest ? (type == Output || type == Exit) : (type == Stdin || type == Resize);
      if (!ok || len > kMaxFrame || (type == Exit && len > kMaxExitJson)) return false;
      if (buf.size() - at - 5 < len) break;
      if (fromGuest && type == Exit) {
        exited = true;
        out += frame(Exit, exitJson(std::string_view(buf.data() + at + 5, len)));
      } else {
        out.append(buf, at, 5 + len);
      }
      at += 5 + len;
    }
    buf.erase(0, at);
    return true;
  }

  void pump(int fd, std::string& in, std::string& out, bool fromGuest) {
    char buf[64 * 1024];
    ssize_t n = ::read(fd, buf, sizeof buf);
    if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
    if (n <= 0) return close();
    in.append(buf, size_t(n));
    if (!frames(in, out, fromGuest)) return close();
    flush();
  }

  void flush() {
    for (auto [fd, out] : {std::pair{client, &toClient}, std::pair{agentFd, &toAgent}}) {
      while (!out->empty()) {
        ssize_t w = ::write(fd, out->data(), out->size());
        if (w < 0 && errno == EINTR) continue;
        if (w < 0 && errno == EAGAIN) break;
        if (w <= 0) return close();
        out->erase(0, size_t(w));
      }
    }
    if (exited && toClient.empty()) return close();
    arm();
  }

  // Reads only while the opposite queue is below the high-water mark (backpressure); writes only
  // while something is queued.
  void arm() {
    auto self = shared_from_this();
    auto watch = [&](int fd, bool read, bool write, std::string& in, std::string& out, bool guest) {
      loop.remove(fd);
      if (read) loop.onReadable(fd, [self, fd, &in, &out, guest] { self->pump(fd, in, out, guest); });
      if (write) loop.onWritable(fd, [self] { self->flush(); });
    };
    watch(client, toAgent.size() < kRelayHighWater, !toClient.empty(), fromClient, toAgent, false);
    watch(agentFd, toClient.size() < kRelayHighWater && !exited, !toAgent.empty(), fromAgent, toClient, true);
  }
};

}  // namespace

void relay(Loop& loop, int client, int agentFd, std::string pending, std::string first) {
  nonblocking(client);
  nonblocking(agentFd);
  auto r = std::make_shared<Relay>(loop, client, agentFd);
  r->toClient = std::move(first);
  r->fromAgent = std::move(pending);
  if (!r->frames(r->fromAgent, r->toClient, true)) return r->close();
  r->flush();
}

void open(Loop& loop, Platform& platform, const std::string& vm, const json::Object& request, uint64_t timeoutMs,
          std::function<void(int, std::string, std::string)> done) {
  struct Opening {
    int fd = -1;
    uint64_t timer = 0;
    std::string in;
    std::function<void(int, std::string, std::string)> done;
  };
  auto o = std::make_shared<Opening>();
  o->done = std::move(done);
  auto fail = [&loop, o](std::string error) {
    if (!o->done) return;
    loop.cancel(o->timer);
    if (o->fd >= 0) { loop.remove(o->fd); ::close(o->fd); }
    auto cb = std::move(o->done);
    o->done = nullptr;
    cb(-1, "", std::move(error));
  };
  o->timer = loop.after(timeoutMs, [fail] { fail("agent did not start the process in time"); });
  std::string hello = frame(Request, json::dump(request));
  platform.connect(vm, kPort, [&loop, o, fail, hello](int fd, std::string error) {
    if (!o->done) { if (fd >= 0) ::close(fd); return; }
    if (fd < 0) return fail("agent unreachable: " + error);
    o->fd = fd;
    nonblocking(fd);
    writeAsync(loop, fd, hello, [&loop, o, fail] {
    if (!o->done) return;
    loop.onReadable(o->fd, [&loop, o, fail] {
     try {
      char buf[4096];
      ssize_t n = ::read(o->fd, buf, sizeof buf);
      if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
      if (n <= 0) return fail("agent closed the connection");
      o->in.append(buf, size_t(n));
      if (o->in.size() < 5) return;
      uint8_t type = uint8_t(o->in[0]);
      uint32_t len = length(o->in, 0);
      if (type == Exit && len <= kMaxExitJson && o->in.size() >= 5 + len) {
        json::Value v = json::parse(std::string_view(o->in.data() + 5, len));
        auto* e = std::get_if<std::string>(&v["error"].v);
        return fail("agent: " + (e ? printable(*e, 1024) : std::string("process did not start")));
      }
      if (type != Started || len != 0) return fail("agent sent an unexpected frame");
      loop.cancel(o->timer);
      loop.remove(o->fd);
      auto cb = std::move(o->done);
      o->done = nullptr;
      cb(o->fd, o->in.substr(5), "");
     } catch (const std::exception& e) {
      fail(std::string("agent: ") + e.what());
     }
    });
    }, fail);
  });
}

namespace {

struct Bridge : std::enable_shared_from_this<Bridge> {
  Loop& loop;
  int fd[2];
  std::string queued[2];  // bytes waiting to be written to fd[i]
  bool eof[2] = {false, false};  // fd[i] has reached EOF
  bool closed = false;

  Bridge(Loop& l, int a, int b) : loop(l), fd{a, b} {}

  void close() {
    if (closed) return;
    closed = true;
    for (int f : fd) { loop.remove(f); ::close(f); }
  }

  void pump(int i) {  // read fd[i], queue for fd[1-i]
    char buf[256 * 1024];
    ssize_t n = ::read(fd[i], buf, sizeof buf);
    if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
    if (n < 0) return close();
    if (n == 0) eof[i] = true;
    else queued[1 - i].append(buf, size_t(n));
    flush();
  }

  void flush() {
    for (int i = 0; i < 2; ++i) {
      while (!queued[i].empty()) {
        ssize_t w = ::write(fd[i], queued[i].data(), queued[i].size());
        if (w < 0 && errno == EINTR) continue;
        if (w < 0 && errno == EAGAIN) break;
        if (w <= 0) return close();
        queued[i].erase(0, size_t(w));
      }
      if (eof[1 - i] && queued[i].empty()) ::shutdown(fd[i], SHUT_WR);  // pass the EOF on
    }
    if (eof[0] && eof[1] && queued[0].empty() && queued[1].empty()) return close();
    arm();
  }

  void arm() {
    auto self = shared_from_this();
    for (int i = 0; i < 2; ++i) {
      loop.remove(fd[i]);
      if (!eof[i] && queued[1 - i].size() < kRelayHighWater) loop.onReadable(fd[i], [self, i] { self->pump(i); });
      if (!queued[i].empty()) loop.onWritable(fd[i], [self] { self->flush(); });
    }
  }
};

}  // namespace

void bridge(Loop& loop, int a, int b, std::string pendingForA) {
  nonblocking(a);
  nonblocking(b);
  auto br = std::make_shared<Bridge>(loop, a, b);
  br->queued[0] = std::move(pendingForA);
  br->flush();
}

}  // namespace sentineld::agent
