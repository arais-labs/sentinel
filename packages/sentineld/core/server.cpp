#include "server.hpp"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <vector>

#include "loop.hpp"
#include "agent.hpp"
#include "workspaces.hpp"

namespace sentineld {

constexpr int kApi = 1;
constexpr size_t kMaxRequest = 1 << 20;
constexpr size_t kMaxQueued = 16 << 20;

struct Server::Conn {
  int fd;
  std::string in, out;
  enum { New, Ready, Rejected } handshake = New;
  bool watching = false, closing = false, busy = false;  // busy: a process request owns it
};

Server::Server(Loop& loop, Workspaces& workspaces, const std::string& socketPath, std::function<void()> shutdown)
    : loop_(loop), workspaces_(workspaces), path_(socketPath), shutdown_(std::move(shutdown)) {
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  if (path_.size() >= sizeof addr.sun_path) throw std::runtime_error("socket path too long: " + path_);
  std::memcpy(addr.sun_path, path_.c_str(), path_.size() + 1);
  ::unlink(path_.c_str());
  listen_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
  // Access control is the file mode: owner only (the state directory is 0700 as well).
  mode_t old = ::umask(077);
  int bound = ::bind(listen_, reinterpret_cast<sockaddr*>(&addr), sizeof addr);
  ::umask(old);
  if (listen_ < 0 || bound != 0 || ::listen(listen_, 64) != 0) throw std::runtime_error("cannot listen on " + path_);
  fcntl(listen_, F_SETFL, O_NONBLOCK);
  fcntl(listen_, F_SETFD, FD_CLOEXEC);
  loop_.onReadable(listen_, [this] { accept(); });

  workspaces_.onChange = [this](const std::string& id, json::Value record) {
    json::Object event{{"event", "workspace"}, {"id", id}, {"record", record}};
    std::vector<std::shared_ptr<Conn>> watchers;  // send may close and erase a connection
    for (auto& [_, c] : conns_)
      if (c->watching) watchers.push_back(c);
    for (auto& c : watchers) send(c, event);
  };
}

Server::~Server() {
  for (auto& [fd, _] : conns_) { loop_.remove(fd); ::close(fd); }
  loop_.remove(listen_);
  ::close(listen_);
  ::unlink(path_.c_str());
}

void Server::accept() {
  while (true) {
    int fd = ::accept(listen_, nullptr, nullptr);
    if (fd < 0) return;  // EAGAIN: drained
    fcntl(fd, F_SETFL, O_NONBLOCK);
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
    auto c = std::make_shared<Conn>();
    c->fd = fd;
    conns_[fd] = c;
    loop_.onReadable(fd, [this, c] { readable(c); });
  }
}

void Server::readable(const std::shared_ptr<Conn>& c) {
  char buf[64 * 1024];
  ssize_t n = ::read(c->fd, buf, sizeof buf);
  if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) return close(c);
  if (n < 0) return;
  c->in.append(buf, size_t(n));
  size_t start = 0;
  for (size_t nl; !c->busy && (nl = c->in.find('\n', start)) != std::string::npos; start = nl + 1) {
    request(c, c->in.substr(start, nl - start));
    if (c->closing) return;
  }
  c->in.erase(0, start);
  if (c->in.size() > kMaxRequest) {
    send(c, error("invalid", "request line too long"));
    c->closing = true;
    flush(c);
  }
}

void Server::request(const std::shared_ptr<Conn>& c, const std::string& line) {
  json::Value req;
  try {
    req = json::parse(line);
  } catch (const std::exception& e) {
    send(c, error("invalid", e.what()));
    return;
  }
  json::Value id = req["id"];
  auto* op = std::get_if<std::string>(&req["op"].v);
  auto reply = [this, w = std::weak_ptr<Conn>(c), id](json::Object m) {
    if (auto conn = w.lock(); conn && !conn->closing) {
      m["id"] = id;
      send(conn, m);
    }
  };
  if (!op || id.isNull()) return reply(error("invalid", "request needs id and op"));

  // Every connection opens with status{api}; requests after a failed handshake are refused.
  if (c->handshake != Conn::Ready) {
    if (c->handshake == Conn::New && *op == "status") {
      auto* api = std::get_if<double>(&req["api"].v);
      if (api && *api == kApi) {
        c->handshake = Conn::Ready;
        return reply({{"api", kApi}, {"worker_id", workspaces_.workerId()}});
      }
      c->handshake = Conn::Rejected;
      reply(error("unsupported", "api version", {{"required", kApi}}));
    } else {
      reply(error("unsupported", "status{api} must come first", {{"required", kApi}}));
    }
    c->closing = true;
    return flush(c);
  }
  if (*op == "status") return reply({{"api", kApi}, {"worker_id", workspaces_.workerId()}});
  // Acknowledged at once; the daemon then powers off its VMs and exits, closing every connection.
  // That EOF is the caller's signal that the state directory is free.
  if (*op == "shutdown") {
    reply({});
    return shutdown_();
  }
  if (*op == "list" && !req["watch"].isNull() && req["watch"].boolean()) c->watching = true;
  if (*op == "process") {
    // The connection becomes the process stream: `started`, then binary frames (DESIGN.md §4).
    c->busy = true;
    return workspaces_.process(req, [this, w = std::weak_ptr<Conn>(c), id](int fd, std::string pending, json::Object err) {
      auto conn = w.lock();
      if (!conn || conn->closing) { if (fd >= 0) ::close(fd); return; }
      if (fd < 0) {
        err["id"] = id;
        send(conn, err);
        conn->closing = true;
        return flush(conn);
      }
      std::string first = json::dump(json::Object{{"id", id}, {"started", true}}) + "\n";
      if (!conn->in.empty()) { ::close(fd); return close(conn); }  // frames before `started`
      agent::relay(loop_, detach(conn), fd, std::move(pending), conn->out + first);
    });
  }
  workspaces_.handle(*op, req, reply);
}

void Server::send(const std::shared_ptr<Conn>& c, const json::Object& message) {
  if (c->closing) return;
  if (c->out.size() > kMaxQueued) return close(c);  // a client that stopped reading
  bool idle = c->out.empty();
  c->out += json::dump(message);
  c->out += '\n';
  if (idle) flush(c);
}

void Server::flush(const std::shared_ptr<Conn>& c) {
  while (!c->out.empty()) {
    ssize_t n = ::write(c->fd, c->out.data(), c->out.size());
    if (n < 0 && errno == EINTR) continue;
    if (n < 0 && errno == EAGAIN) {
      loop_.onWritable(c->fd, [this, c] { flush(c); });
      return;
    }
    if (n <= 0) return close(c);
    c->out.erase(0, size_t(n));
  }
  if (c->closing) return close(c);
  loop_.offWritable(c->fd);
}

void Server::close(const std::shared_ptr<Conn>& c) {
  const int fd = c->fd;
  if (!conns_.count(fd)) return;
  c->closing = true;
  loop_.remove(fd);
  ::close(fd);
  conns_.erase(fd);
}

// Hands an open connection to another owner (the process relay) without closing it.
int Server::detach(const std::shared_ptr<Conn>& c) {
  const int fd = c->fd;
  c->closing = true;
  loop_.remove(fd);
  conns_.erase(fd);
  return fd;
}

}  // namespace sentineld
