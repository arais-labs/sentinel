// Single-threaded kqueue event loop. Other threads hand work to it with post().
#pragma once
#include <functional>
#include <map>
#include <mutex>
#include <vector>

namespace sentineld {

class Loop {
 public:
  using Fn = std::function<void()>;
  Loop();
  ~Loop();
  Loop(const Loop&) = delete;
  Loop& operator=(const Loop&) = delete;

  void onReadable(int fd, Fn fn);  // level-triggered until removed
  void onWritable(int fd, Fn fn);
  void offWritable(int fd);
  void remove(int fd);             // drops both directions
  void post(Fn fn);                // thread-safe; runs on the loop thread
  uint64_t after(uint64_t ms, Fn fn);  // one-shot timer; returns an id for cancel()
  void cancel(uint64_t timer);
  void run();                      // returns after stop()
  void stop() { running_ = false; }

 private:
  void set(int fd, short filter, bool add);
  int kq_;
  bool running_ = false;
  std::map<int, Fn> readers_, writers_;
  std::map<uint64_t, Fn> timers_;
  uint64_t nextTimer_ = 2;  // 1 is the wake event
  std::mutex mutex_;
  std::vector<Fn> posted_;
};

}  // namespace sentineld
