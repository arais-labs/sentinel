// Disk helpers for base and workspace disks: GPT partition tables and growing an ext4 root.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace sentineld::image {

// GPT partition types.
inline constexpr const char* kEspType = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B";
inline constexpr const char* kBasicDataType = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7";
inline constexpr const char* kMsrType = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE";  // Microsoft reserved
inline constexpr const char* kLinuxType = "0FC63DAF-8483-4772-8E79-3D69D8477DE4";  // Linux file system

// One partition: its type GUID, first sector and length in 512-byte sectors, name (ASCII), and unique
// GUID (random when empty).
struct GptPartition {
  const char* type;
  uint64_t start, sectors;
  std::string name;
  std::string guid;
};

// Writes the protective MBR and both GPT copies for a disk of `sectors` 512-byte sectors; the disk's
// GUID is random when empty.
void writeGpt(int fd, uint64_t sectors, const std::vector<GptPartition>& partitions, const std::string& diskGuid = "");

// Grows a root disk's file system to fill the file (after the file has grown), with resize2fs on the
// host before the guest boots. A no-op at full size, and for a file system that was not cleanly
// unmounted (it grows on a later start). Returns an error message, or "" on success.
std::string growRoot(const std::string& root, const std::string& resize2fs);

}  // namespace sentineld::image
