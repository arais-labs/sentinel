#include "media.hpp"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

#include "disk.hpp"

extern "C" {
#include "ff.h"  // first: diskio.h uses its types
#include "diskio.h"
}

namespace fs = std::filesystem;

namespace sentineld::image {
namespace {

constexpr uint64_t kMiB = 1 << 20;
constexpr size_t kChunk = 8 << 20;

// FatFs has one volume, the partition `volume` describes; the mutex serializes its users.
struct Volume {
  int fd = -1;
  uint64_t base = 0, sectors = 0;  // in 512-byte sectors
} volume;
std::mutex volumeLock;

uint64_t alignUp(uint64_t v, uint64_t a) { return (v + a - 1) / a * a; }

struct Fd {
  int v;
  ~Fd() { if (v >= 0) ::close(v); }
};

void check(FRESULT r, const std::string& what) {
  if (r != FR_OK) throw std::runtime_error(what + " failed (FatFs error " + std::to_string(int(r)) + ")");
}

// A file or folder to copy: where it is, and its path in the volume ("/a/b").
struct Entry {
  fs::path source;
  std::string name;
};

// The files and folders under `root`, named under `at` ("" for the volume's root), folders before
// what they hold; anything else is refused.
void tree(const fs::path& root, const std::string& at, std::vector<Entry>& entries, uint64_t& bytes) {
  if (!at.empty()) {
    entries.push_back({root, at});
    bytes += 4096;
  }
  for (auto it = fs::recursive_directory_iterator(root); it != fs::recursive_directory_iterator(); ++it) {
    const auto status = it->symlink_status();
    if (fs::is_regular_file(status)) bytes += alignUp(it->file_size(), 128 << 10);  // the largest cluster used
    else if (!fs::is_directory(status)) throw std::runtime_error("media hold only files and folders: " + it->path().string());
    bytes += 4096;
    entries.push_back({it->path(), at + "/" + it->path().lexically_relative(root).generic_string()});
  }
}

std::vector<Entry> tree(const MediaPartition& p, uint64_t& bytes) {
  std::vector<Entry> entries;
  tree(p.from, "", entries, bytes);
  for (auto& [at, folder] : p.also) {
    if (at.empty() || at.find("..") != std::string::npos) throw std::runtime_error("media: bad folder name " + at);
    // The folder takes the place of what `from` has under that name (a mount point, say).
    const std::string root = "/" + at;
    std::erase_if(entries, [&](const Entry& e) { return e.name == root || e.name.compare(0, root.size() + 1, root + "/") == 0; });
    tree(folder, root, entries, bytes);
  }
  // A parent sorts before its children; a name twice would fail when it is created.
  std::sort(entries.begin(), entries.end(), [](const Entry& a, const Entry& b) { return a.name < b.name; });
  return entries;
}

// Formats the partition `volume` points at and copies `entries` (under `root`) into it.
void fill(const MediaPartition& p, const std::vector<Entry>& entries) {
  MKFS_PARM opt{};
  opt.fmt = BYTE((p.fs == "exfat" ? FM_EXFAT : FM_FAT32) | FM_SFD);  // the partition alone, no table of its own
  std::vector<char> work(kMiB);
  check(f_mkfs("", &opt, work.data(), UINT(work.size())), "formatting " + p.label);
  FATFS fatfs{};
  check(f_mount(&fatfs, "", 1), "mounting " + p.label);
  std::unique_ptr<FATFS, void (*)(FATFS*)> mounted(&fatfs, [](FATFS*) { f_unmount(""); });
  check(f_setlabel(p.label.c_str()), "labelling " + p.label);
  std::vector<char> buf(kChunk);
  for (const auto& [path, name] : entries) {
    if (fs::is_directory(fs::symlink_status(path))) {
      check(f_mkdir(name.c_str()), "creating " + name);
      continue;
    }
    Fd in{::open(path.c_str(), O_RDONLY | O_CLOEXEC)};
    if (in.v < 0) throw std::runtime_error("cannot read " + path.string());
    FIL file{};
    check(f_open(&file, name.c_str(), FA_WRITE | FA_CREATE_NEW), "creating " + name);
    for (;;) {
      ssize_t n = ::read(in.v, buf.data(), buf.size());
      if (n < 0 && errno == EINTR) continue;
      if (n < 0) {
        f_close(&file);
        throw std::runtime_error("cannot read " + path.string());
      }
      if (n == 0) break;
      UINT written = 0;
      const FRESULT r = f_write(&file, buf.data(), UINT(n), &written);
      if (r != FR_OK || written != UINT(n)) {
        f_close(&file);
        check(r == FR_OK ? FR_DENIED : r, "writing " + name);  // a short write: the volume is full
      }
    }
    check(f_close(&file), "writing " + name);
  }
}

}  // namespace

void createDisk(const std::string& path, uint64_t bytes, const std::string& diskGuid, std::vector<DiskPartition>& partitions) {
  const uint64_t sectors = bytes / 512, end = sectors - kMiB / 512;  // the tail holds the backup GPT
  std::vector<GptPartition> table;
  uint64_t next = kMiB / 512;
  for (size_t i = 0; i < partitions.size(); ++i) {
    DiskPartition& p = partitions[i];
    const char* type = p.type == "esp" ? kEspType : p.type == "msr" ? kMsrType : p.type == "basic" ? kBasicDataType : p.type == "linux" ? kLinuxType : nullptr;
    if (!type) throw std::runtime_error("unknown partition type: " + p.type);
    if (!p.mib && i + 1 != partitions.size()) throw std::runtime_error("only the last partition takes the rest of the disk");
    p.start = next;
    p.sectors = p.mib ? p.mib * kMiB / 512 : end - next;
    if (p.start + p.sectors > end) throw std::runtime_error("the partitions do not fit the disk");
    table.push_back({type, p.start, p.sectors, p.name, p.guid});
    next += p.sectors;
  }
  Fd disk{::open(path.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0644)};
  if (disk.v < 0 || ::ftruncate(disk.v, off_t(sectors * 512)) != 0) throw std::runtime_error("cannot create " + path);
  writeGpt(disk.v, sectors, table, diskGuid);
}

void formatFat32(const std::string& disk, const DiskPartition& partition, const std::string& label, const std::string& from) {
  uint64_t bytes = 0;
  const MediaPartition media{"fat32", label, from, {}};
  const auto entries = tree(media, bytes);
  if (bytes > partition.sectors * 512) throw std::runtime_error(from + " does not fit partition " + partition.name);
  Fd fd{::open(disk.c_str(), O_RDWR | O_CLOEXEC)};
  if (fd.v < 0) throw std::runtime_error("cannot open " + disk);
  std::lock_guard<std::mutex> lock(volumeLock);
  volume = {fd.v, partition.start, partition.sectors};
  try {
    fill(media, entries);
  } catch (...) {
    volume = {};
    throw;
  }
  volume = {};
  if (::fsync(fd.v) != 0) throw std::runtime_error("fsync failed");
}

void writeMedia(const std::vector<MediaPartition>& partitions, const std::string& out) {
  struct Plan {
    uint64_t start, sectors;
    std::vector<Entry> entries;
  };
  std::vector<Plan> plans;
  uint64_t next = kMiB / 512;  // partitions start 1 MiB aligned
  for (const MediaPartition& p : partitions) {
    if (p.fs != "fat32" && p.fs != "exfat") throw std::runtime_error("unknown media file system: " + p.fs);
    uint64_t bytes = 0;
    auto entries = tree(p, bytes);
    // Room for the file system's own tables; FAT32 needs enough clusters to be FAT32 at all.
    bytes = alignUp(std::max<uint64_t>(bytes + bytes / 16 + 64 * kMiB, p.fs == "fat32" ? 320 * kMiB : 64 * kMiB), kMiB);
    plans.push_back({next, bytes / 512, std::move(entries)});
    next += bytes / 512;
  }
  const uint64_t sectors = next + kMiB / 512;  // the tail holds the backup GPT

  const std::string part = out + "." + std::to_string(getpid()) + ".part";
  struct Unlink { const std::string& path; bool keep = false; ~Unlink() { if (!keep) ::unlink(path.c_str()); } } guard{part};
  Fd disk{::open(part.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0644)};
  if (disk.v < 0) throw std::runtime_error("cannot create " + part);
  if (::ftruncate(disk.v, off_t(sectors * 512)) != 0) throw std::runtime_error("cannot size " + part);
  std::vector<GptPartition> table;
  {
    std::lock_guard<std::mutex> lock(volumeLock);
    for (size_t i = 0; i < partitions.size(); ++i) {
      volume = {disk.v, plans[i].start, plans[i].sectors};
      fill(partitions[i], plans[i].entries);
      table.push_back({kBasicDataType, plans[i].start, plans[i].sectors, partitions[i].label, ""});
    }
    volume = {};
  }
  writeGpt(disk.v, sectors, table);
  if (::fsync(disk.v) != 0) throw std::runtime_error("fsync failed");
  if (::rename(part.c_str(), out.c_str()) != 0) throw std::runtime_error("cannot finalize " + out);
  guard.keep = true;
}

std::optional<std::string> readMedia(const std::string& disk, const std::string& path, size_t limit) {
  Fd fd{::open(disk.c_str(), O_RDONLY | O_CLOEXEC)};
  if (fd.v < 0) throw std::runtime_error("cannot open " + disk);
  // The first entry of the primary GPT: its first and last sector.
  uint8_t header[512], entry[128];
  if (::pread(fd.v, header, sizeof header, 512) != ssize_t(sizeof header) || std::memcmp(header, "EFI PART", 8) != 0)
    throw std::runtime_error(disk + " has no GPT");
  uint64_t entries, first, last;
  std::memcpy(&entries, header + 72, 8);
  if (::pread(fd.v, entry, sizeof entry, off_t(entries * 512)) != ssize_t(sizeof entry)) throw std::runtime_error("cannot read " + disk);
  std::memcpy(&first, entry + 32, 8);
  std::memcpy(&last, entry + 40, 8);
  if (first == 0 || last < first) throw std::runtime_error(disk + " has no partition");

  std::lock_guard<std::mutex> lock(volumeLock);
  volume = {fd.v, first, last - first + 1};
  std::unique_ptr<Volume, void (*)(Volume*)> reset(&volume, [](Volume* v) { *v = {}; });
  FATFS fatfs{};
  check(f_mount(&fatfs, "", 1), "mounting " + disk);
  std::unique_ptr<FATFS, void (*)(FATFS*)> mounted(&fatfs, [](FATFS*) { f_unmount(""); });
  FIL file{};
  const std::string name = "/" + path;
  const FRESULT r = f_open(&file, name.c_str(), FA_READ);
  if (r == FR_NO_FILE || r == FR_NO_PATH) return std::nullopt;
  check(r, "opening " + name);
  std::string text(std::min<uint64_t>(f_size(&file), limit), '\0');
  UINT got = 0;
  const FRESULT rr = f_read(&file, text.data(), UINT(text.size()), &got);
  f_close(&file);
  check(rr, "reading " + name);
  text.resize(got);
  return text;
}

}  // namespace sentineld::image

// FatFs's disk layer: the sectors of the partition `volume` describes.
extern "C" {

DSTATUS disk_status(BYTE) { return sentineld::image::volume.fd < 0 ? STA_NOINIT : 0; }
DSTATUS disk_initialize(BYTE drive) { return disk_status(drive); }

DRESULT disk_read(BYTE, BYTE* buff, LBA_t sector, UINT count) {
  auto& v = sentineld::image::volume;
  if (sector + count > v.sectors) return RES_PARERR;
  const size_t n = size_t(count) * 512;
  for (size_t done = 0; done < n;) {
    ssize_t r = ::pread(v.fd, buff + done, n - done, off_t((v.base + sector) * 512 + done));
    if (r < 0 && errno == EINTR) continue;
    if (r <= 0) return RES_ERROR;
    done += size_t(r);
  }
  return RES_OK;
}

DRESULT disk_write(BYTE, const BYTE* buff, LBA_t sector, UINT count) {
  auto& v = sentineld::image::volume;
  if (sector + count > v.sectors) return RES_PARERR;
  const size_t n = size_t(count) * 512;
  for (size_t done = 0; done < n;) {
    ssize_t w = ::pwrite(v.fd, buff + done, n - done, off_t((v.base + sector) * 512 + done));
    if (w < 0 && errno == EINTR) continue;
    if (w <= 0) return RES_ERROR;
    done += size_t(w);
  }
  return RES_OK;
}

DRESULT disk_ioctl(BYTE, BYTE cmd, void* buff) {
  switch (cmd) {
    case CTRL_SYNC: return RES_OK;  // writeMedia syncs the whole disk once
    case GET_SECTOR_COUNT: *static_cast<LBA_t*>(buff) = sentineld::image::volume.sectors; return RES_OK;
    case GET_SECTOR_SIZE: *static_cast<WORD*>(buff) = 512; return RES_OK;
    case GET_BLOCK_SIZE: *static_cast<DWORD*>(buff) = 1; return RES_OK;
    default: return RES_PARERR;
  }
}

}
