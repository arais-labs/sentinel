#include "json.hpp"

#include <cstdio>
#include <cstdlib>
#include <type_traits>

namespace sentineld::json {
namespace {

struct Parser {
  std::string_view s;
  size_t i = 0;

  [[noreturn]] void fail(const char* what) const {
    throw std::runtime_error(std::string("json: ") + what + " at offset " + std::to_string(i));
  }
  void ws() { while (i < s.size() && (s[i] == ' ' || s[i] == '\n' || s[i] == '\r' || s[i] == '\t')) ++i; }
  char peek() { ws(); if (i >= s.size()) fail("unexpected end"); return s[i]; }
  void expect(char c) { if (peek() != c) fail("unexpected character"); ++i; }

  unsigned hex4() {
    if (i + 4 > s.size()) fail("bad unicode escape");
    unsigned v = 0;
    for (int k = 0; k < 4; ++k) {
      char c = s[i++];
      v <<= 4;
      if (c >= '0' && c <= '9') v |= unsigned(c - '0');
      else if (c >= 'a' && c <= 'f') v |= unsigned(c - 'a' + 10);
      else if (c >= 'A' && c <= 'F') v |= unsigned(c - 'A' + 10);
      else fail("bad unicode escape");
    }
    return v;
  }

  static void utf8(std::string& out, unsigned cp) {
    if (cp < 0x80) { out += char(cp); return; }
    if (cp < 0x800) { out += char(0xC0 | cp >> 6); out += char(0x80 | (cp & 0x3F)); return; }
    if (cp < 0x10000) { out += char(0xE0 | cp >> 12); out += char(0x80 | (cp >> 6 & 0x3F)); out += char(0x80 | (cp & 0x3F)); return; }
    out += char(0xF0 | cp >> 18); out += char(0x80 | (cp >> 12 & 0x3F));
    out += char(0x80 | (cp >> 6 & 0x3F)); out += char(0x80 | (cp & 0x3F));
  }

  std::string string() {
    expect('"');
    std::string out;
    while (true) {
      if (i >= s.size()) fail("unterminated string");
      char c = s[i++];
      if (c == '"') return out;
      if (c != '\\') { out += c; continue; }
      if (i >= s.size()) fail("bad escape");
      switch (char e = s[i++]) {
        case '"': case '\\': case '/': out += e; break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'u': {
          unsigned cp = hex4();
          if (cp >= 0xD800 && cp < 0xDC00) {  // surrogate pair
            if (s.substr(i, 2) != "\\u") fail("unpaired surrogate");
            i += 2;
            unsigned lo = hex4();
            if (lo < 0xDC00 || lo >= 0xE000) fail("unpaired surrogate");
            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
          } else if (cp >= 0xDC00 && cp < 0xE000) {
            fail("unpaired surrogate");
          }
          utf8(out, cp);
          break;
        }
        default: fail("bad escape");
      }
    }
  }

  int depth = 0;

  Value value() {
    if (++depth > 64) fail("nesting too deep");
    Value v = scalarOrContainer();
    --depth;
    return v;
  }

  Value scalarOrContainer() {
    switch (peek()) {
      case '{': {
        ++i;
        Object o;
        if (peek() == '}') { ++i; return {std::move(o)}; }
        while (true) {
          std::string k = string();
          expect(':');
          o.emplace(std::move(k), value());
          if (peek() == ',') { ++i; continue; }
          expect('}');
          return {std::move(o)};
        }
      }
      case '[': {
        ++i;
        Array a;
        if (peek() == ']') { ++i; return {std::move(a)}; }
        while (true) {
          a.push_back(value());
          if (peek() == ',') { ++i; continue; }
          expect(']');
          return {std::move(a)};
        }
      }
      case '"': return {string()};
      case 't': if (s.substr(i, 4) != "true") fail("bad literal"); i += 4; return {true};
      case 'f': if (s.substr(i, 5) != "false") fail("bad literal"); i += 5; return {false};
      case 'n': if (s.substr(i, 4) != "null") fail("bad literal"); i += 4; return {nullptr};
      default: {
        size_t start = i;
        while (i < s.size() && std::string_view("+-0123456789.eE").find(s[i]) != std::string_view::npos) ++i;
        if (start == i) fail("unexpected character");
        return {std::strtod(std::string(s.substr(start, i - start)).c_str(), nullptr)};
      }
    }
  }
};

}  // namespace

Value parse(std::string_view text) {
  Parser p{text};
  Value v = p.value();
  p.ws();
  if (p.i != text.size()) p.fail("trailing data");
  return v;
}

}  // namespace sentineld::json

namespace sentineld::json {
namespace {

void quote(std::string& out, const std::string& s) {
  out += '"';
  for (unsigned char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char b[8];
          std::snprintf(b, sizeof b, "\\u%04x", c);
          out += b;
        } else {
          out += char(c);
        }
    }
  }
  out += '"';
}

void write(std::string& out, const Value& v) {
  std::visit([&](auto&& x) {
    using T = std::decay_t<decltype(x)>;
    if constexpr (std::is_same_v<T, std::nullptr_t>) out += "null";
    else if constexpr (std::is_same_v<T, bool>) out += x ? "true" : "false";
    else if constexpr (std::is_same_v<T, double>) {
      char b[32];
      if (x == double(int64_t(x))) std::snprintf(b, sizeof b, "%lld", (long long)x);
      else std::snprintf(b, sizeof b, "%.17g", x);
      out += b;
    } else if constexpr (std::is_same_v<T, std::string>) quote(out, x);
    else if constexpr (std::is_same_v<T, Array>) {
      out += '[';
      for (size_t i = 0; i < x.size(); ++i) { if (i) out += ','; write(out, x[i]); }
      out += ']';
    } else {
      out += '{';
      bool first = true;
      for (auto& [k, val] : x) { if (!first) out += ','; first = false; quote(out, k); out += ':'; write(out, val); }
      out += '}';
    }
  }, v.v);
}

}  // namespace

std::string dump(const Value& v) {
  std::string out;
  write(out, v);
  return out;
}

}  // namespace sentineld::json
