// The viewer endpoint (DESIGN.md §6): WebTransport over HTTP/3 over QUIC on a loopback UDP port,
// with a short-lived self-signed certificate that clients pin by hash.
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <sys/socket.h>

#include "../core/loop.hpp"

typedef struct ssl_ctx_st SSL_CTX;

namespace sentineld::media {

// The packet format shared by the provider's media fd and the viewer streams: a 16-byte header
// (type, flags, payload length u32be, timestamp u64be) and the payload.
enum Packet : uint8_t {
  kConfig = 1, kFrame = 2, kInput = 3, kKeyframe = 4, kAudio = 5, kCursor = 6, kRefresh = 7, kWatching = 8, kHello = 16
};
constexpr size_t kHeader = 16;
constexpr uint8_t kKey = 1;  // frame flag

struct Conn;

class Server {
 public:
  struct Hooks {
    std::function<std::string(std::string_view ticket)> admit;  // workspace id, or empty to refuse
    std::function<void(const std::string& workspace, std::span<const uint8_t> packet)> control;
  };
  Server(Loop& loop, Hooks hooks);
  ~Server();

  uint16_t port() const { return port_; }
  const std::string& certificateHash() const { return hash_; }  // SHA-256 of the DER, hex
  void rotateCertificate();

  // A frame from a workspace's provider; key frames carry `head` in front (the decoder config and
  // the guest cursor), which is all a viewer needs to start.
  void publish(const std::string& workspace, std::span<const uint8_t> head, std::span<const uint8_t> frame,
               bool key);
  // A guest cursor change or a sound packet: sent to every viewer at once on its control stream,
  // in order, never dropped.
  void relay(const std::string& workspace, std::span<const uint8_t> packet);
  void drop(const std::string& workspace);  // the provider went away: end its viewers

 private:
  friend struct Conn;
  void receive();
  void accept(std::span<const uint8_t> packet, const sockaddr_storage& from, uint32_t fromLen);
  void close(Conn& c);
  // The fastest refresh rate among a workspace's viewers, to its provider when it changes.
  void refresh(const std::string& workspace);
  // Whether a workspace has viewers, to its provider when that changes: it encodes only for them.
  void watching(const std::string& workspace);
  void schedule(Conn& c);

  Loop& loop_;
  Hooks hooks_;
  int fd_ = -1;
  uint16_t port_ = 0;
  SSL_CTX* tls_ = nullptr;
  std::string hash_;
  uint8_t secret_[32];  // stateless reset tokens
  uint64_t nextId_ = 1;
  std::map<uint64_t, std::unique_ptr<Conn>> conns_;
  std::map<std::string, Conn*, std::less<>> byCid_;
  std::map<std::string, uint16_t> refresh_;  // by workspace: the rate last sent to its provider
  std::set<std::string> watched_;            // workspaces whose provider was told it has viewers
};

}  // namespace sentineld::media
