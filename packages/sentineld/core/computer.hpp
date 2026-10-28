// Computer use (DESIGN.md §22): one action batch on a workspace's display, driven from the host
// through the VM process's display service. Runs on a worker thread: it blocks.
#pragma once
#include <atomic>
#include <functional>
#include <string>
#include <vector>

#include "util/json.hpp"

namespace sentineld::computer {

struct Batch {
  std::string socket;                // the display service (Platform::displaySocket)
  json::Value request;               // {actions, viewport}
  json::Object keys;                 // the desktop layout's key chords (the agent's keyboard op); empty: US
  std::vector<std::string> paste;    // the desktop types by pasting with these keys; empty: the guest types
  // Asks the guest agent (`op`, e.g. type or clipboard) and waits; returns why it failed, "" on success.
  std::function<std::string(json::Object op)> guest;
  const std::atomic<bool>* cancel = nullptr;
};

// {ok, completed_actions, error?, viewport, cursor, screenshot}: a failed batch still reports what it
// completed and the screen, so nothing is replayed blindly.
json::Object run(const Batch& batch);

}  // namespace sentineld::computer
