// Pinned file downloads: the sources of an install recipe (DESIGN.md §7).
#pragma once
#include <atomic>
#include <string>

namespace sentineld::image {

// Downloads `url` (HTTP or HTTPS) to `path`, which appears only once the file matches `digest`
// ("sha256:<hex>"). Throws on failure or when `cancel` is set.
void download(const std::string& url, const std::string& digest, const std::string& path, const std::atomic<bool>& cancel);

}  // namespace sentineld::image
