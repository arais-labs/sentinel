#include "loop.hpp"

#include <cerrno>
#include <cstdio>
#include <stdexcept>
#include <sys/event.h>
#include <unistd.h>

namespace sentineld {

constexpr uintptr_t kWake = 1;

// A handler failure is a bug or bad input on one connection: report it and keep the daemon up.
static void guarded(const Loop::Fn& fn) {
  try {
    fn();
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld: handler failed: %s\n", e.what());
  }
}

Loop::Loop() : kq_(kqueue()) {
  if (kq_ < 0) throw std::runtime_error("kqueue failed");
  struct kevent ev;
  EV_SET(&ev, kWake, EVFILT_USER, EV_ADD | EV_CLEAR, 0, 0, nullptr);
  if (kevent(kq_, &ev, 1, nullptr, 0, nullptr) != 0) throw std::runtime_error("kqueue wake setup failed");
}

Loop::~Loop() { ::close(kq_); }

void Loop::set(int fd, short filter, bool add) {
  struct kevent ev;
  EV_SET(&ev, uintptr_t(fd), filter, add ? EV_ADD : EV_DELETE, 0, 0, nullptr);
  kevent(kq_, &ev, 1, nullptr, 0, nullptr);
}

void Loop::onReadable(int fd, Fn fn) { readers_[fd] = std::move(fn); set(fd, EVFILT_READ, true); }

void Loop::onWritable(int fd, Fn fn) { writers_[fd] = std::move(fn); set(fd, EVFILT_WRITE, true); }

void Loop::offWritable(int fd) {
  if (writers_.erase(fd)) set(fd, EVFILT_WRITE, false);
}

void Loop::remove(int fd) {
  if (readers_.erase(fd)) set(fd, EVFILT_READ, false);
  if (writers_.erase(fd)) set(fd, EVFILT_WRITE, false);
}

void Loop::post(Fn fn) {
  {
    std::lock_guard lock(mutex_);
    posted_.push_back(std::move(fn));
  }
  struct kevent ev;
  EV_SET(&ev, kWake, EVFILT_USER, 0, NOTE_TRIGGER, 0, nullptr);
  kevent(kq_, &ev, 1, nullptr, 0, nullptr);
}

uint64_t Loop::after(uint64_t ms, Fn fn) {
  uint64_t id = nextTimer_++;
  timers_[id] = std::move(fn);
  struct kevent ev;
  EV_SET(&ev, uintptr_t(id), EVFILT_TIMER, EV_ADD | EV_ONESHOT, 0, intptr_t(ms), nullptr);
  kevent(kq_, &ev, 1, nullptr, 0, nullptr);
  return id;
}

void Loop::cancel(uint64_t timer) {
  if (!timers_.erase(timer)) return;
  struct kevent ev;
  EV_SET(&ev, uintptr_t(timer), EVFILT_TIMER, EV_DELETE, 0, 0, nullptr);
  kevent(kq_, &ev, 1, nullptr, 0, nullptr);
}

void Loop::run() {
  running_ = true;
  struct kevent events[64];
  while (running_) {
    int n = kevent(kq_, nullptr, 0, events, 64, nullptr);
    if (n < 0) {
      if (errno == EINTR) continue;
      throw std::runtime_error("kevent failed");
    }
    for (int i = 0; i < n && running_; ++i) {
      const auto& ev = events[i];
      if (ev.filter == EVFILT_USER) {
        std::vector<Fn> work;
        {
          std::lock_guard lock(mutex_);
          work.swap(posted_);
        }
        for (auto& fn : work) guarded(fn);
        continue;
      }
      if (ev.filter == EVFILT_TIMER) {
        auto t = timers_.find(uint64_t(ev.ident));
        if (t == timers_.end()) continue;  // cancelled
        Fn fn = std::move(t->second);
        timers_.erase(t);
        guarded(fn);
        continue;
      }
      // Copy the handler: it may remove itself (and its fd) while running.
      auto& table = ev.filter == EVFILT_READ ? readers_ : writers_;
      auto it = table.find(int(ev.ident));
      if (it == table.end()) continue;
      Fn fn = it->second;
      guarded(fn);
    }
  }
}

}  // namespace sentineld
