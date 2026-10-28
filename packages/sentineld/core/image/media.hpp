// Install media (DESIGN.md §8): a GPT disk whose FAT32 and exFAT partitions sentineld formats and
// fills from folders on the host (FatFs, no mounting), and reads back after the install VM ran.
#pragma once
#include <optional>
#include <string>
#include <vector>

namespace sentineld::image {

struct MediaPartition {
  std::string fs;     // "fat32" (the firmware boots from it) or "exfat" (no 4 GiB file limit)
  std::string label;  // the volume label
  std::string from;   // the folder whose tree the partition holds
  std::vector<std::pair<std::string, std::string>> also;  // more folders' trees: {where in it, folder}
};

// Writes `out` atomically: a GPT disk with one basic-data partition per entry, in order, each sized
// to its content with room to spare.
void writeMedia(const std::vector<MediaPartition>& partitions, const std::string& out);

// A disk an install writes on the host (DESIGN.md §7): partitions in order from 1 MiB, each `mib` long
// or, the last, the rest of the disk; type "esp", "msr" or "basic"; and their unique GUIDs.
struct DiskPartition {
  std::string name, type;
  uint64_t mib = 0;
  std::string guid;
  uint64_t start = 0, sectors = 0;  // set by createDisk, in 512-byte sectors
};

// Creates the sparse disk `path` of `bytes` with its GPT, and places the partitions.
void createDisk(const std::string& path, uint64_t bytes, const std::string& diskGuid, std::vector<DiskPartition>& partitions);

// Formats the partition of `disk` as FAT32 and fills it with the tree of `from`.
void formatFat32(const std::string& disk, const DiskPartition& partition, const std::string& label, const std::string& from);

// The content of `path` ("dir/file") on the media disk's first partition, at most `limit` bytes;
// nullopt when the file does not exist.
std::optional<std::string> readMedia(const std::string& disk, const std::string& path, size_t limit = 4096);

}  // namespace sentineld::image
