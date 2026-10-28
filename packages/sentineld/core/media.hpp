// The viewer endpoint process, sentineld-media (DESIGN.md §6). sentineld starts one, hands it each
// GPU provider's media channel, and tells it which viewer tickets to admit.
#pragma once
#include <string>
#include <sys/types.h>

#include "loop.hpp"
#include "util/json.hpp"

namespace sentineld {

class Media {
 public:
  Media(Loop& loop, std::string binary);
  ~Media();
  // A new media channel for a workspace's provider: returns the provider's end (or -1); the other
  // end goes to sentineld-media.
  int attach(const std::string& workspace);
  // {port, cert_sha256, ticket, expires} for one viewer of the workspace; empty if not running yet.
  json::Object display(const std::string& workspace);

 private:
  void spawn();
  void read();
  bool send(const std::string& line, int fd = -1);

  Loop& loop_;
  std::string binary_;
  int fd_ = -1;
  pid_t pid_ = 0;
  std::string rx_, hash_;
  int port_ = 0;
};

}  // namespace sentineld
