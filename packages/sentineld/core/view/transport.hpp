// The viewer's WebTransport session to sentineld-media (DESIGN.md §6): HTTP/3 over QUIC on the loopback
// port, the certificate pinned by hash, the ticket in a hello. It runs on its own thread and hands
// every §6 packet to `onPacket`.
#pragma once
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace viewer {

class Transport {
 public:
  // type, flags, timestamp (µs) and payload of one §6 packet; called on the transport's thread.
  std::function<void(uint8_t type, uint8_t flags, uint64_t timestamp, const uint8_t* data, size_t size)> onPacket;
  std::function<void()> onReady;                        // the hello is sent
  std::function<void(const std::string& why)> onClosed;  // the session ended

  Transport();
  ~Transport();
  // Starts the session's thread.
  void start(int port, const std::string& certSha256, const std::string& ticket);
  // A §6 packet to the host (input, key frame request); any thread.
  void send(uint8_t type, const std::vector<uint8_t>& payload);
  void stop();

  struct State;  // the session (transport.cpp)

 private:
  std::unique_ptr<State> s_;
};

}  // namespace viewer
