// Pinned file downloads: the sources of an install recipe (DESIGN.md §7).
#pragma once
#include <atomic>
#include <cstdint>
#include <string>

namespace sentineld::image {

// Downloads `url` (HTTP or HTTPS) to `path`, which appears only once the file matches `digest`
// ("sha256:<hex>"; "" takes any file). Returns the file's digest; `got`, when given, counts the bytes
// received. Throws on failure or when `cancel` is set.
std::string download(const std::string& url, const std::string& digest, const std::string& path, const std::atomic<bool>& cancel,
                     std::atomic<uint64_t>* got = nullptr);

}  // namespace sentineld::image
