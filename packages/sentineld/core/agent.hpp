// Talking to the guest agent (DESIGN.md §10–13): one vsock connection per operation, binary frames
// [u8 type][u32 BE len][bytes]. Everything from the guest is untrusted and bounded.
#pragma once
#include <functional>
#include <optional>
#include <string>

#include "util/json.hpp"

namespace sentineld {

class Loop;
class Platform;

namespace agent {

enum Frame : uint8_t { Stdin = 0, Output = 1, Resize = 2, Exit = 3, Request = 4, Started = 5, Stderr = 6 };
constexpr uint32_t kPort = 1;
constexpr uint32_t kMaxFrame = 1 << 20;

std::string frame(uint8_t type, const std::string& data);

struct Result {
  std::optional<int> code;  // exit code, 128+signo when killed by a signal
  std::string out, err;     // kept up to the cap; the rest is drained and dropped
  bool truncated = false, timedOut = false;
  std::string error;        // "" on success; otherwise the agent was unreachable or misbehaved
};

// Sends one request and collects its frames until exit, the timeout or a protocol error.
void call(Loop& loop, Platform& platform, const std::string& vm, const json::Object& request, bool closeStdin,
          size_t cap, uint64_t timeoutMs, std::function<void(Result)> done);

// Opens an interactive `run`: sends the request and waits for `started`. On success the caller owns
// fd; `pending` holds any agent bytes that arrived after `started`.
void open(Loop& loop, Platform& platform, const std::string& vm, const json::Object& request, uint64_t timeoutMs,
          std::function<void(int fd, std::string pending, std::string error)> done);

// Relays an interactive process between a client and an agent connection (both already past
// `started`) until exit or either side closes. `first` is written to the client before any frame.
// Takes ownership of both descriptors.
void relay(Loop& loop, int client, int agentFd, std::string pending, std::string first);

// Copies raw bytes both ways between two sockets (a forwarded TCP connection) until both sides
// have closed, passing each EOF on as a half-close. Takes ownership of both descriptors.
void bridge(Loop& loop, int a, int b, std::string pendingForA);

}  // namespace agent
}  // namespace sentineld
