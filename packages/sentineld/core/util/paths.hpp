// Project paths are mounted inside the guest at the same path as on the host, so they may not
// shadow guest system directories (DESIGN.md §11). Shared by sentineld and the agent.
#pragma once
#include <string_view>

namespace sentineld {

inline bool validProjectPath(std::string_view p) {
  if (p.size() < 2 || p.size() > 4096 || p[0] != '/' || p.back() == '/') return false;
  for (size_t i = 0; i < p.size();) {  // no empty, "." or ".." components
    size_t j = p.find('/', i + 1);
    if (j == std::string_view::npos) j = p.size();
    std::string_view c = p.substr(i + 1, j - i - 1);
    if (c.empty() || c == "." || c == "..") return false;
    i = j;
  }
  static constexpr std::string_view kSystem[] = {"/proc", "/sys", "/dev", "/run", "/boot", "/etc",
                                                 "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var"};
  // Guest directories a project may live under but never replace.
  static constexpr std::string_view kParents[] = {"/tmp", "/home", "/root", "/opt", "/srv", "/mnt", "/media"};
  for (auto s : kSystem)
    if (p == s || (p.substr(0, s.size()) == s && p[s.size()] == '/')) return false;
  for (auto s : kParents)
    if (p == s) return false;
  return true;
}

}  // namespace sentineld
