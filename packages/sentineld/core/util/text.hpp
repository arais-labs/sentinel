// Guest-sourced text made safe for the control plane (DESIGN.md §13).
#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace sentineld {

// Valid UTF-8: invalid, overlong and surrogate sequences become U+FFFD.
inline std::string utf8(std::string_view in) {
  std::string out;
  out.reserve(in.size());
  for (size_t i = 0; i < in.size();) {
    const unsigned char c = static_cast<unsigned char>(in[i]);
    const size_t n = c < 0x80 ? 1 : c >= 0xC2 && c <= 0xDF ? 2 : (c & 0xF0) == 0xE0 ? 3 : c >= 0xF0 && c <= 0xF4 ? 4 : 0;
    bool ok = n && i + n <= in.size();
    uint32_t cp = n == 1 ? c : n == 2 ? c & 0x1F : n == 3 ? c & 0x0F : c & 0x07;
    for (size_t k = 1; ok && k < n; ++k) {
      const unsigned char b = static_cast<unsigned char>(in[i + k]);
      ok = (b >> 6) == 2;
      cp = cp << 6 | (b & 0x3F);
    }
    ok = ok && !(n == 3 && (cp < 0x800 || (cp >= 0xD800 && cp <= 0xDFFF))) && !(n == 4 && (cp < 0x10000 || cp > 0x10FFFF));
    if (ok) out.append(in.substr(i, n)), i += n;
    else out += "\xEF\xBF\xBD", ++i;
  }
  return out;
}

// Valid UTF-8 without C0 or C1 control characters (newline and tab kept), at most `cap` bytes,
// cut on a character boundary.
inline std::string printable(std::string_view in, size_t cap) {
  const std::string valid = utf8(in);
  std::string out;
  for (size_t i = 0; i < valid.size(); ++i) {
    const unsigned char c = static_cast<unsigned char>(valid[i]);
    if (c == 0xC2 && i + 1 < valid.size() && static_cast<unsigned char>(valid[i + 1]) < 0xA0) { ++i; continue; }  // U+0080-9F
    if ((c >= 0x20 && c != 0x7F) || c == '\n' || c == '\t') out += valid[i];
  }
  if (out.size() > cap) {
    size_t n = cap;
    while (n && (static_cast<unsigned char>(out[n]) & 0xC0) == 0x80) --n;
    out.resize(n);
  }
  return out;
}

}  // namespace sentineld
