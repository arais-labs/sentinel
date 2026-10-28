// Control plane (DESIGN.md §4): Unix socket, one JSON object per line.
#pragma once
#include <functional>
#include <map>
#include <memory>
#include <string>

#include "util/json.hpp"

namespace sentineld {

class Loop;
class Workspaces;

class Server {
 public:
  // shutdown: the `shutdown` op (an update replacing this release) drains and stops the daemon.
  Server(Loop& loop, Workspaces& workspaces, const std::string& socketPath, std::function<void()> shutdown);
  ~Server();

 private:
  struct Conn;
  void accept();
  void readable(const std::shared_ptr<Conn>& c);
  void request(const std::shared_ptr<Conn>& c, const std::string& line);
  void send(const std::shared_ptr<Conn>& c, const json::Object& message);
  void flush(const std::shared_ptr<Conn>& c);
  void close(const std::shared_ptr<Conn>& c);
  int detach(const std::shared_ptr<Conn>& c);

  Loop& loop_;
  Workspaces& workspaces_;
  std::string path_;
  std::function<void()> shutdown_;
  int listen_ = -1;
  std::map<int, std::shared_ptr<Conn>> conns_;
};

}  // namespace sentineld
