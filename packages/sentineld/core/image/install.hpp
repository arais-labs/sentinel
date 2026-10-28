// How sentineld makes a distribution's base disk (DESIGN.md §7): the distribution folder's
// distribution.json "install" names pinned sources, the disk it writes (its GPT partitions) and the
// steps that fill it on the host. The VM that boots the disk once to finish it is the caller's
// (Workspaces).
#pragma once
#include <atomic>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

#include "../util/json.hpp"

namespace sentineld::image {

// Names the base disk an install produces: the recipe and every file of the folder's install/
// directory, which the steps may copy. Unrelated edits to the distribution reuse the installed disk.
std::string installKey(const json::Value& install, const std::string& folder);

// Downloads the sources into `sources` (kept by digest), creates the disk `work`/target.raw and runs
// the steps that fill it. Steps see {work}, {install} (the folder's install/ directory), each source
// by name, {disk} and, for each partition N, {N.guid} {N.start} {N.sectors} (in sectors) and
// {N.offset} {N.size} (in bytes); {disk.guid} is the disk's. The GUIDs follow from the recipe, so a
// recipe can name them (its boot configuration does). Steps run only the tools in `tools`, sandboxed
// to read their inputs and write `work`. Throws on failure or when `cancel` is set.
void prepareInstall(const json::Value& install, const std::string& folder, const std::string& sources,
                    const std::string& work, const std::string& tools, const std::atomic<bool>& cancel);

// An archive's members, to write a recipe that uses it (the source op). With `paths`: those
// members, each {bytes, text} or {bytes, binary: true} (with one path, `offset` and `length` read
// a byte range of it, for a member larger than a reply); otherwise the members whose path matches
// the glob `match`, each {path, bytes, type}. A reply over `limit` characters throws TooLarge,
// whose message says how much matched and where, so the asker narrows the request.
struct TooLarge : std::runtime_error {
  using std::runtime_error::runtime_error;
};
json::Object browseArchive(const std::string& file, const std::string& match, const std::vector<std::string>& paths,
                           size_t limit, uint64_t offset = 0, uint64_t length = 0);

}  // namespace sentineld::image
