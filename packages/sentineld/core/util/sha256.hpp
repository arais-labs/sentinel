// SHA-256 behind a small interface; the implementation is per platform.
#pragma once
#include <cstddef>
#include <string>
#include <string_view>

namespace sentineld {

class Sha256 {
 public:
  Sha256();
  void update(const void* data, size_t n);
  std::string digest();  // "sha256:<hex>"
 private:
  alignas(8) unsigned char state_[112];
};

// "sha256:" followed by 64 lowercase hex digits: safe to use as a file name.
inline bool isDigest(std::string_view d) {
  if (d.size() != 71 || d.substr(0, 7) != "sha256:") return false;
  for (char c : d.substr(7))
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}

}  // namespace sentineld
