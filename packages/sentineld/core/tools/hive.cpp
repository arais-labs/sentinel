// hive FILE EDITS.json [NAME=VALUE...]: applies a distribution's registry edits to an offline Windows
// registry hive (DESIGN.md §7) — a SYSTEM hive, or a BCD store, which is one too. EDITS is an array of
// {"key": "A\\B", "delete": true}, {"copy": "A\\B", "to": "A\\C"} (the key and everything below it)
// and {"key": "A\\B", "values": {"Name": VALUE}}; a VALUE is {"sz"|"expand": "text"}, {"multi": [...]},
// {"dword"|"qword": N} or {"binary": ["hex", {"guid": "{...}"}, ...]} (its parts concatenated, GUIDs
// in their in-memory order), with an optional "type" for registry types beyond these. {NAME} in any
// string is the value given on the command line. Run by install steps, sandboxed like every tool.
#include <hivex.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "../util/json.hpp"

using namespace sentineld;

namespace {

hive_h* hive;
std::map<std::string, std::string> vars;

std::string expand(const std::string& text) {
  std::string out;
  for (size_t i = 0; i < text.size();) {
    const size_t end = text[i] == '{' ? text.find('}', i) : std::string::npos;
    auto v = end == std::string::npos ? vars.end() : vars.find(text.substr(i + 1, end - i - 1));
    if (v == vars.end()) {
      out += text[i++];
    } else {
      out += v->second;
      i = end + 1;
    }
  }
  return out;
}

std::vector<std::string> path(const std::string& key) {
  std::vector<std::string> parts;
  std::stringstream in(expand(key));
  for (std::string part; std::getline(in, part, '\\');)
    if (!part.empty()) parts.push_back(part);
  return parts;
}

hive_node_h node(const std::string& key, bool make) {
  hive_node_h n = hivex_root(hive);
  for (auto& part : path(key)) {
    hive_node_h c = hivex_node_get_child(hive, n, part.c_str());
    if (!c && make) c = hivex_node_add_child(hive, n, part.c_str());
    if (!c) return 0;
    n = c;
  }
  return n;
}

std::string utf16(const std::string& s) {  // the hive's strings: UTF-16LE, ASCII is all a recipe needs
  std::string out;
  for (unsigned char c : s) {
    if (c >= 0x80) throw std::runtime_error("non-ASCII registry string: " + s);
    out += char(c);
    out += '\0';
  }
  return out;
}

std::string guid(const std::string& text) {  // "aabbccdd-eeff-0011-2233-445566778899", braces or not, as stored
  std::string g = expand(text);
  if (g.size() == 38 && g.front() == '{' && g.back() == '}') g = g.substr(1, 36);
  unsigned a, b, c, d[8];
  if (g.size() != 36 || std::sscanf(g.c_str(), "%8x-%4x-%4x-%2x%2x-%2x%2x%2x%2x%2x%2x", &a, &b, &c, &d[0], &d[1], &d[2], &d[3],
                                    &d[4], &d[5], &d[6], &d[7]) != 11)
    throw std::runtime_error("not a GUID: " + g);
  std::string out(16, '\0');
  for (int i = 0; i < 4; ++i) out[i] = char(a >> (8 * i));
  for (int i = 0; i < 2; ++i) out[4 + i] = char(b >> (8 * i)), out[6 + i] = char(c >> (8 * i));
  for (int i = 0; i < 8; ++i) out[8 + i] = char(d[i]);
  return out;
}

std::string hex(const std::string& text) {
  const std::string h = expand(text);
  if (h.size() % 2) throw std::runtime_error("odd hex: " + h);
  std::string out;
  for (size_t i = 0; i < h.size(); i += 2) out += char(std::stoi(h.substr(i, 2), nullptr, 16));
  return out;
}

std::string number(uint64_t v, size_t bytes) {
  std::string out(bytes, '\0');
  for (size_t i = 0; i < bytes; ++i) out[i] = char(v >> (8 * i));
  return out;
}

// One value: its registry type and bytes.
std::pair<hive_type, std::string> value(const json::Value& v) {
  auto has = [&](const char* k) { return !v[k].isNull(); };
  hive_type type;
  std::string data;
  if (has("sz") || has("expand")) {
    type = has("sz") ? hive_t_REG_SZ : hive_t_REG_EXPAND_SZ;
    data = utf16(expand((has("sz") ? v["sz"] : v["expand"]).str())) + std::string(2, '\0');
  } else if (has("multi")) {
    type = hive_t_REG_MULTI_SZ;
    for (auto& s : v["multi"].arr()) data += utf16(expand(s.str())) + std::string(2, '\0');
    data += std::string(2, '\0');
  } else if (has("dword")) {
    type = hive_t_REG_DWORD, data = number(uint64_t(v["dword"].num()), 4);
  } else if (has("qword")) {
    type = hive_t_REG_QWORD, data = number(uint64_t(v["qword"].num()), 8);
  } else if (has("binary")) {
    type = hive_t_REG_BINARY;
    for (auto& part : v["binary"].arr()) data += part.obj() ? guid(part["guid"].str()) : hex(part.str());
  } else {
    throw std::runtime_error("unknown value " + json::dump(v));
  }
  if (has("type")) type = hive_type(uint32_t(v["type"].num()));
  return {type, data};
}

void copy(hive_node_h from, hive_node_h to) {
  hive_value_h* vs = hivex_node_values(hive, from);
  std::vector<hive_set_value> values;
  for (size_t i = 0; vs[i]; ++i) {
    hive_type t;
    size_t len;
    char* data = hivex_value_value(hive, vs[i], &t, &len);
    values.push_back({hivex_value_key(hive, vs[i]), t, len, data});
  }
  free(vs);
  if (hivex_node_set_values(hive, to, values.size(), values.data(), 0) != 0) throw std::runtime_error("cannot copy values");
  for (auto& v : values) free(v.key), free(v.value);
  hive_node_h* cs = hivex_node_children(hive, from);
  for (size_t i = 0; cs[i]; ++i) {
    char* name = hivex_node_name(hive, cs[i]);
    hive_node_h c = hivex_node_get_child(hive, to, name);
    if (!c) c = hivex_node_add_child(hive, to, name);
    free(name);
    copy(cs[i], c);
  }
  free(cs);
}

void apply(const json::Value& edit) {
  if (!edit["copy"].isNull()) {
    hive_node_h from = node(edit["copy"].str(), false);
    if (!from) throw std::runtime_error("no key " + expand(edit["copy"].str()));
    return copy(from, node(edit["to"].str(), true));
  }
  const std::string key = edit["key"].str();
  if (!edit["delete"].isNull()) {
    if (hive_node_h n = node(key, false); n && hivex_node_delete_child(hive, n) != 0) throw std::runtime_error("cannot delete " + key + ": " + std::strerror(errno));
    return;
  }
  hive_node_h n = node(key, true);
  if (!n) throw std::runtime_error("cannot create " + expand(key) + ": " + std::strerror(errno));
  // Set one by one: hivex_node_set_values replaces all of a key's values.
  for (auto& [name, v] : *edit["values"].obj()) {
    auto [type, data] = value(v);
    const std::string k = expand(name);
    hive_set_value set{const_cast<char*>(k.c_str()), type, data.size(), data.data()};
    if (hivex_node_set_value(hive, n, &set, 0) != 0) throw std::runtime_error("cannot set " + key + "\\" + k + ": " + std::strerror(errno));
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3) {
    std::fprintf(stderr, "usage: %s HIVE EDITS.json [NAME=VALUE...]\n", argv[0]);
    return 2;
  }
  try {
    for (int i = 3; i < argc; ++i) {
      const char* eq = std::strchr(argv[i], '=');
      if (!eq) throw std::runtime_error(std::string("expected NAME=VALUE: ") + argv[i]);
      vars[std::string(argv[i], size_t(eq - argv[i]))] = eq + 1;
    }
    std::ifstream in(argv[2]);
    std::stringstream text;
    text << in.rdbuf();
    const json::Value edits = json::parse(text.str());
    if (!(hive = hivex_open(argv[1], HIVEX_OPEN_WRITE))) throw std::runtime_error(std::string("cannot open ") + argv[1]);
    for (auto& edit : edits.arr()) apply(edit);
    if (hivex_commit(hive, nullptr, 0) != 0) throw std::runtime_error("cannot write the hive");
    hivex_close(hive);
  } catch (const std::exception& e) {
    std::fprintf(stderr, "hive: %s\n", e.what());
    return 1;
  }
}
