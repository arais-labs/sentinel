#include "computer.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cmath>
#include <memory>
#include <map>
#include <regex>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace sentineld::computer {

namespace {

// US key codes (Linux evdev) for named keys, and for printable keys when the desktop's layout gave
// no chords.
const std::map<std::string, int>& usKeys() {
  static const std::map<std::string, int> keys = [] {
    std::map<std::string, int> k{
        {"Escape", 1},    {"BackSpace", 14},  {"Tab", 15},          {"Return", 28},     {"Control_L", 29},
        {"Shift_L", 42},  {"Shift_R", 54},    {"Alt_L", 56},        {"space", 57},      {"Caps_Lock", 58},
        {"Num_Lock", 69}, {"Scroll_Lock", 70}, {"KP_Enter", 96},    {"Control_R", 97},  {"Print", 99},
        {"Alt_R", 100},   {"Home", 102},      {"Up", 103},          {"Prior", 104},     {"Page_Up", 104},
        {"Left", 105},    {"Right", 106},     {"End", 107},         {"Down", 108},      {"Next", 109},
        {"Page_Down", 109}, {"Insert", 110},  {"Delete", 111},      {"Pause", 119},     {"Super_L", 125},
        {"Super_R", 126}, {"Menu", 127},      {"minus", 12},        {"equal", 13},      {"bracketleft", 26},
        {"bracketright", 27}, {"semicolon", 39}, {"apostrophe", 40}, {"grave", 41},     {"backslash", 43},
        {"comma", 51},    {"period", 52},     {"slash", 53}};
    for (auto [row, first] : {std::pair{"1234567890", 2}, {"qwertyuiop", 16}, {"asdfghjkl", 30}, {"zxcvbnm", 44}})
      for (int i = 0; row[i]; ++i) k[std::string(1, row[i])] = first + i;
    const int f[] = {59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88};
    for (int i = 0; i < 12; ++i) k["F" + std::to_string(i + 1)] = f[i];
    return k;
  }();
  return keys;
}

// Keys a layout places itself: a missing chord for one of them is an error, not a US fallback.
bool layoutKey(const std::string& key) {
  static const char* const names[] = {"minus", "equal", "bracketleft", "bracketright", "semicolon", "apostrophe",
                                      "grave", "backslash", "comma", "period", "slash"};
  if (key.size() == 1) return true;
  for (const char* n : names)
    if (key == n) return true;
  return false;
}

std::string base64(const std::string& in) {
  static const char* k = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  size_t i = 0;
  for (; i + 2 < in.size(); i += 3) {
    uint32_t n = uint32_t(uint8_t(in[i])) << 16 | uint32_t(uint8_t(in[i + 1])) << 8 | uint8_t(in[i + 2]);
    out += {k[n >> 18], k[n >> 12 & 63], k[n >> 6 & 63], k[n & 63]};
  }
  if (i + 1 == in.size()) {
    uint32_t n = uint32_t(uint8_t(in[i])) << 16;
    out += {k[n >> 18], k[n >> 12 & 63], '=', '='};
  } else if (i + 2 == in.size()) {
    uint32_t n = uint32_t(uint8_t(in[i])) << 16 | uint32_t(uint8_t(in[i + 1])) << 8;
    out += {k[n >> 18], k[n >> 12 & 63], k[n >> 6 & 63], '='};
  }
  return out;
}

int integer(const json::Value& v, const char* name, int low, int high) {
  if (!std::holds_alternative<double>(v.v) || v.num() != double(int64_t(v.num())) || v.num() < low || v.num() > high)
    throw std::invalid_argument(std::string(name) + " must be an integer between " + std::to_string(low) + " and " + std::to_string(high));
  return int(v.num());
}

const json::Array& actionsOf(const json::Value& request, int width, int height) {
  static const json::Array none;
  if (!request.obj()) throw std::invalid_argument("Request must be an object");
  if (request["actions"].isNull()) return none;
  if (!std::holds_alternative<json::Array>(request["actions"].v) || request["actions"].arr().size() > 16)
    throw std::invalid_argument("Use at most 16 actions per batch");
  const json::Array& actions = request["actions"].arr();
  const json::Value& viewport = request["viewport"];
  if (!actions.empty() && (!viewport.obj() || viewport["width"].isNull() || viewport["height"].isNull() ||
                           viewport["width"].num() != width || viewport["height"].num() != height))
    throw std::invalid_argument("Desktop dimensions changed or viewport missing; take a fresh screenshot");
  static const std::regex keyName("[A-Za-z0-9_]{1,24}");
  for (auto& a : actions) {
    const std::string kind = a.obj() && std::holds_alternative<std::string>(a["type"].v) ? a["type"].str() : "";
    if (kind != "move" && kind != "click" && kind != "double_click" && kind != "drag" && kind != "scroll" &&
        kind != "keypress" && kind != "type" && kind != "wait")
      throw std::invalid_argument("Unsupported computer action");
    if (kind == "move" || kind == "click" || kind == "double_click" || kind == "scroll") {
      integer(a["x"], "x", 0, width - 1);
      integer(a["y"], "y", 0, height - 1);
    }
    if (kind == "click" || kind == "double_click" || kind == "drag") {
      const std::string button = a["button"].isNull() ? "left" : std::holds_alternative<std::string>(a["button"].v) ? a["button"].str() : "";
      if (button != "left" && button != "middle" && button != "right") throw std::invalid_argument("Invalid mouse button");
    }
    if (kind == "drag") {
      if (!std::holds_alternative<json::Array>(a["path"].v) || a["path"].arr().size() < 2 || a["path"].arr().size() > 100)
        throw std::invalid_argument("Drag path requires 2–100 points");
      for (auto& p : a["path"].arr()) {
        if (!p.obj()) throw std::invalid_argument("Drag point must be an object");
        integer(p["x"], "x", 0, width - 1);
        integer(p["y"], "y", 0, height - 1);
      }
    }
    if (kind == "scroll") {
      integer(a["ticks"], "ticks", 1, 20);
      const std::string d = std::holds_alternative<std::string>(a["direction"].v) ? a["direction"].str() : "";
      if (d != "up" && d != "down" && d != "left" && d != "right") throw std::invalid_argument("Invalid scroll direction");
    }
    if (kind == "keypress") {
      if (!std::holds_alternative<json::Array>(a["keys"].v) || a["keys"].arr().empty() || a["keys"].arr().size() > 5)
        throw std::invalid_argument("keypress requires 1–5 key names");
      for (auto& k : a["keys"].arr())
        if (!std::holds_alternative<std::string>(k.v) || !std::regex_match(k.str(), keyName))
          throw std::invalid_argument("Use X11 key names, e.g. Control_L, Return, Left");
    }
    if (kind == "type" && (!std::holds_alternative<std::string>(a["text"].v) || a["text"].str().size() > 4000 ||
                           a["text"].str().find('\0') != std::string::npos))
      throw std::invalid_argument("Text must be at most 4000 UTF-8 bytes without NUL");
    if (kind == "wait") integer(a["milliseconds"], "milliseconds", 0, 2000);
  }
  return actions;
}

class Display {
 public:
  explicit Display(const std::string& path) {
    fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (fd_ < 0 || path.empty() || path.size() >= sizeof addr.sun_path) throw std::runtime_error("Virtual display is unavailable");
    path.copy(addr.sun_path, path.size());
    timeval tv{10, 0};
    setsockopt(fd_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd_, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int one = 1;
    setsockopt(fd_, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
    if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
      ::close(fd_);
      throw std::runtime_error("Virtual display is unavailable");
    }
  }
  // Closing releases the batch's keys, buttons and lease.
  ~Display() { ::close(fd_); }
  Display(const Display&) = delete;
  Display& operator=(const Display&) = delete;

  json::Value request(const std::string& action, json::Object fields = {}) {
    fields["action"] = action;
    const std::string line = json::dump(fields) + "\n";
    for (size_t at = 0; at < line.size();) {
      ssize_t n = ::send(fd_, line.data() + at, line.size() - at, 0);
      if (n <= 0) throw std::runtime_error("Virtual display disconnected");
      at += size_t(n);
    }
    std::string reply;
    for (;;) {
      if (size_t nl = buffer_.find('\n'); nl != std::string::npos) {
        reply = buffer_.substr(0, nl);
        buffer_.erase(0, nl + 1);
        break;
      }
      if (buffer_.size() > 2000000) throw std::runtime_error("Virtual display response exceeded limit");
      char buf[65536];
      ssize_t n = ::recv(fd_, buf, sizeof buf, 0);
      if (n <= 0) throw std::runtime_error("Virtual display disconnected or response exceeded limit");
      buffer_.append(buf, size_t(n));
    }
    json::Value v = json::parse(reply);
    if (!v.obj()) throw std::runtime_error("Invalid virtual display response");
    if (v["ok"].isNull() || !v["ok"].boolean())
      throw std::runtime_error(std::holds_alternative<std::string>(v["error"].v) ? v["error"].str() : "Virtual display is busy or unavailable");
    return v;
  }

  std::pair<int, int> geometry() {
    json::Value status = request("status");
    return {int(status["viewport"]["width"].num()), int(status["viewport"]["height"].num())};
  }

  // A §6 input payload: [u16 device][u16 count] then (u16 type, u16 code, i32 value), SYN last.
  void input(int device, std::vector<std::array<int, 3>> events) {
    events.push_back({0, 0, 0});
    std::string body;
    auto u16 = [&](int v) { body += {char(v >> 8 & 0xFF), char(v & 0xFF)}; };
    u16(device);
    u16(int(events.size()));
    for (auto& [type, code, value] : events) {
      u16(type);
      u16(code);
      const uint32_t u = uint32_t(int32_t(value));
      body += {char(u >> 24), char(u >> 16 & 0xFF), char(u >> 8 & 0xFF), char(u & 0xFF)};
    }
    request("input", {{"data", base64(body)}});
  }

 private:
  int fd_ = -1;
  std::string buffer_;
};

class Runner {
 public:
  Runner(const Batch& b, Display& d) : batch_(b), display_(d) {}

  std::vector<int> keycodes(const json::Array& keys) const {
    std::vector<int> codes;
    for (auto& k : keys) {
      std::string key = k.str();
      if (key.size() == 1) key[0] = char(std::tolower(static_cast<unsigned char>(key[0])));
      std::vector<int> chord;
      // Named controls stay explicit; printable keys follow the desktop's layout.
      if (auto it = batch_.keys.find(key); it != batch_.keys.end() && std::holds_alternative<json::Array>(it->second.v)) {
        for (auto& c : it->second.arr()) chord.push_back(int(c.num()));
      } else if (!batch_.keys.empty() && layoutKey(key)) {
        throw std::invalid_argument("Unknown key name; use Control_L, Return, Left, a–z, or F1–F12");
      } else if (auto us = usKeys().find(key); us != usKeys().end()) {
        chord.push_back(us->second);
      } else {
        throw std::invalid_argument("Unknown key name; use Control_L, Return, Left, a–z, or F1–F12");
      }
      for (int c : chord)
        if (std::find(codes.begin(), codes.end(), c) == codes.end()) codes.push_back(c);
    }
    return codes;
  }

  void pause(int ms) const {
    for (auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms); std::chrono::steady_clock::now() < end;) {
      cancelled();
      std::this_thread::sleep_for(std::chrono::milliseconds(std::min(ms, 50)));
    }
  }

  void cancelled() const {
    if (batch_.cancel && batch_.cancel->load()) throw std::runtime_error("Computer action batch cancelled");
  }

  void move(const json::Value& point) {
    auto [width, height] = display_.geometry();
    display_.input(1, {{3, 0, int(std::lround(point["x"].num() * 65535 / std::max(width - 1, 1)))},
                       {3, 1, int(std::lround(point["y"].num() * 65535 / std::max(height - 1, 1)))}});
  }

  void press(const std::vector<int>& codes) {
    std::vector<std::array<int, 3>> events;
    for (int c : codes) events.push_back({1, c, 1});
    display_.input(2, events);
  }

  void execute(const json::Value& a) {
    const std::string kind = a["type"].str();
    const std::string name = a["button"].isNull() ? "left" : a["button"].str();
    const int button = name == "right" ? 273 : name == "middle" ? 274 : 272;
    struct Release {
      Display& d;
      ~Release() { try { d.request("release"); } catch (...) {} }
    } release{display_};
    if (kind == "move" || kind == "click" || kind == "double_click" || kind == "scroll") move(a);
    if (kind == "click" || kind == "double_click") {
      for (int i = 0; i < (kind == "double_click" ? 2 : 1); ++i) {
        display_.input(1, {{1, button, 1}});
        display_.request("release");
        pause(60);
      }
    } else if (kind == "drag") {
      const json::Array& path = a["path"].arr();
      move(path[0]);
      display_.input(1, {{1, button, 1}});
      for (size_t i = 1; i < path.size(); ++i) {
        move(path[i]);
        pause(10);
      }
    } else if (kind == "scroll") {
      const std::string d = a["direction"].str();
      const int code = d == "up" || d == "down" ? 8 : 6, sign = d == "up" || d == "right" ? 1 : -1;
      display_.input(0, {{2, code, sign * int(a["ticks"].num())}});
    } else if (kind == "keypress") {
      press(keycodes(a["keys"].arr()));
    } else if (kind == "type" && !a["text"].str().empty()) {
      if (batch_.paste.empty()) {
        if (std::string why = batch_.guest({{"op", "type"}, {"text", a["text"]}}); !why.empty()) throw std::runtime_error(why);
      } else {
        // Paste plain text: a terminal must not take Ctrl+V as its "quote next character".
        if (std::string why = batch_.guest({{"op", "clipboard"}, {"action", "write"}, {"text", a["text"]}}); !why.empty())
          throw std::runtime_error(why);
        json::Array keys;
        for (auto& k : batch_.paste) keys.push_back(k);
        press(keycodes(keys));
      }
    } else if (kind == "wait") {
      pause(int(a["milliseconds"].num()));
    }
  }

 private:
  const Batch& batch_;
  Display& display_;
};

}  // namespace

json::Object run(const Batch& batch) {
  int completed = 0;
  std::unique_ptr<Display> display;
  try {
    display = std::make_unique<Display>(batch.socket);
    display->request("acquire");
    Runner runner(batch, *display);
    const auto dimensions = display->geometry();
    const json::Array& actions = actionsOf(batch.request, dimensions.first, dimensions.second);
    for (auto& a : actions)
      if (a["type"].str() == "keypress") runner.keycodes(a["keys"].arr());
    for (auto& a : actions) {
      runner.cancelled();
      if (display->geometry() != dimensions) throw std::runtime_error("Desktop resized during batch; take a fresh screenshot");
      runner.execute(a);
      ++completed;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    json::Value shot = display->request("snapshot");
    return {{"ok", true}, {"completed_actions", completed}, {"viewport", shot["viewport"]}, {"cursor", shot["cursor"]},
            {"screenshot", shot["screenshot"]}};
  } catch (const std::exception& e) {
    json::Object result{{"ok", false}, {"completed_actions", completed}, {"error", e.what()}};
    try {
      if (display) {
        json::Value shot = display->request("snapshot");
        for (const char* key : {"viewport", "cursor", "screenshot"}) result[key] = shot[key];
      }
    } catch (...) {}
    return result;
  }
}

}  // namespace sentineld::computer
