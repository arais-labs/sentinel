#include "disk.hpp"

#include <algorithm>
#include <cerrno>
#include <climits>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#include <zlib.h>

namespace sentineld::image {
namespace {

// A child process, killed and reaped if still running when the guard goes away.
struct Child {
  pid_t pid = -1;
  ~Child() { if (pid > 0) { ::kill(pid, SIGKILL); reap(); } }
  int reap() {
    int status = 0;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    pid = -1;
    return status;
  }
};

// Our tools run sandboxed, and the sandbox matches real paths.
std::string absolute(const std::string& path) {
  char buf[PATH_MAX];
  if (!::realpath(path.c_str(), buf)) throw std::runtime_error("cannot resolve " + path);
  return buf;
}

// --- GPT ----------------------------------------------------------------------------------

void guid(const char* text, uint8_t out[16]) {  // first three fields little-endian
  unsigned a, b, c, d[8];
  std::sscanf(text, "%8x-%4x-%4x-%2x%2x-%2x%2x%2x%2x%2x%2x", &a, &b, &c, &d[0], &d[1], &d[2], &d[3],
              &d[4], &d[5], &d[6], &d[7]);
  for (int i = 0; i < 4; ++i) out[i] = uint8_t(a >> (8 * i));
  for (int i = 0; i < 2; ++i) out[4 + i] = uint8_t(b >> (8 * i)), out[6 + i] = uint8_t(c >> (8 * i));
  for (int i = 0; i < 8; ++i) out[8 + i] = uint8_t(d[i]);
}

void randomGuid(uint8_t out[16]) {
  arc4random_buf(out, 16);
  out[7] = (out[7] & 0x0F) | 0x40;  // version 4
  out[8] = (out[8] & 0x3F) | 0x80;  // RFC 4122 variant
}

template <class T> void put(uint8_t* p, T v) { std::memcpy(p, &v, sizeof v); }  // little-endian host

void pwriteAll(int fd, const void* data, size_t n, uint64_t off) {
  auto* p = static_cast<const char*>(data);
  while (n) {
    ssize_t w = pwrite(fd, p, n, off_t(off));
    if (w < 0 && errno == EINTR) continue;
    if (w <= 0) throw std::runtime_error("disk write failed");
    p += w; n -= size_t(w); off += uint64_t(w);
  }
}

constexpr uint32_t kEntries = 128, kEntrySize = 128;
constexpr uint64_t kEntrySectors = kEntries * kEntrySize / 512;

// Writes the protective MBR, both entry arrays and both headers for a disk of `sectors`.
void writeTables(int fd, uint64_t sectors, const std::vector<uint8_t>& entries, const uint8_t diskGuid[16]) {
  const uint32_t entriesCrc = uint32_t(crc32(0, entries.data(), uInt(entries.size())));
  const uint64_t last = sectors - 1;
  auto header = [&](uint64_t self, uint64_t other, uint64_t entriesLba) {
    uint8_t h[512] = {};
    std::memcpy(h, "EFI PART", 8);
    put<uint32_t>(h + 8, 0x00010000);
    put<uint32_t>(h + 12, 92);
    put<uint64_t>(h + 24, self);
    put<uint64_t>(h + 32, other);
    put<uint64_t>(h + 40, 2 + kEntrySectors);         // first usable LBA
    put<uint64_t>(h + 48, last - kEntrySectors - 1);  // last usable LBA
    std::memcpy(h + 56, diskGuid, 16);
    put<uint64_t>(h + 72, entriesLba);
    put<uint32_t>(h + 80, kEntries);
    put<uint32_t>(h + 84, kEntrySize);
    put<uint32_t>(h + 88, entriesCrc);
    put<uint32_t>(h + 16, uint32_t(crc32(0, h, 92)));
    pwriteAll(fd, h, 512, self * 512);
  };
  uint8_t mbr[512] = {};  // protective MBR
  mbr[446 + 4] = 0xEE;
  put<uint32_t>(mbr + 446 + 8, 1);
  put<uint32_t>(mbr + 446 + 12, uint32_t(std::min<uint64_t>(sectors - 1, 0xFFFFFFFF)));
  mbr[510] = 0x55; mbr[511] = 0xAA;
  pwriteAll(fd, mbr, 512, 0);
  pwriteAll(fd, entries.data(), entries.size(), 2 * 512);
  pwriteAll(fd, entries.data(), entries.size(), (last - kEntrySectors) * 512);
  header(1, last, 2);
  header(last, 1, last - kEntrySectors);
}

}  // namespace

void writeGpt(int fd, uint64_t sectors, const std::vector<GptPartition>& partitions, const std::string& diskGuid) {
  if (partitions.size() > kEntries) throw std::runtime_error("too many partitions");
  std::vector<uint8_t> entries(kEntries * kEntrySize);
  for (size_t i = 0; i < partitions.size(); ++i) {
    const GptPartition& p = partitions[i];
    uint8_t* e = entries.data() + i * kEntrySize;
    guid(p.type, e);
    if (p.guid.empty()) randomGuid(e + 16);
    else guid(p.guid.c_str(), e + 16);
    put<uint64_t>(e + 32, p.start);
    put<uint64_t>(e + 40, p.start + p.sectors - 1);
    for (size_t k = 0; k < p.name.size() && k < 36; ++k) put<uint16_t>(e + 56 + 2 * k, uint16_t(p.name[k]));
  }
  uint8_t disk[16];
  if (diskGuid.empty()) randomGuid(disk);
  else guid(diskGuid.c_str(), disk);
  writeTables(fd, sectors, entries, disk);
}

std::string growRoot(const std::string& root, const std::string& resize2fs) {
  // -f below skips resize2fs's own check (it wants a fresh e2fsck after every mount, which a booted
  // root never has), so it is done here: a file system not cleanly unmounted, marked with errors or
  // with a journal to replay is left as is, since growing it corrupts it. It grows on a later start.
  {
    uint8_t sb[1024];
    const int fd = ::open(root.c_str(), O_RDONLY | O_CLOEXEC);
    const bool read = fd >= 0 && ::pread(fd, sb, sizeof sb, 1024) == ssize_t(sizeof sb);
    if (fd >= 0) ::close(fd);
    if (!read) return "cannot read the superblock of " + root;
    uint16_t magic, state;
    uint32_t incompat;
    std::memcpy(&magic, sb + 0x38, 2);
    std::memcpy(&state, sb + 0x3A, 2);
    std::memcpy(&incompat, sb + 0x60, 4);
    if (magic != 0xEF53) return "not an ext4 file system: " + root;
    if ((state & 0x3) != 0x1 || (incompat & 0x4)) return "";  // not clean (0x1), errors (0x2), needs recovery (0x4)
  }
  const std::string tool = absolute(resize2fs), disk = absolute(root);  // the sandbox matches real paths
  const std::string profile =
      "(version 1)(deny default)(allow sysctl-read)(allow file-read-metadata)"
      "(allow process-exec (literal \"" + tool + "\"))"
      "(allow file-read* (subpath \"/usr/lib\") (subpath \"/System\") (subpath \"/private/var/db/dyld\")"
      " (literal \"/\") (literal \"" + tool + "\") (literal \"/dev/null\") (literal \"/dev/urandom\"))"
      "(allow file-read* file-write* (literal \"" + disk + "\"))";
  // No size: the file system grows to the file's, which is the whole file (no partition table).
  std::vector<std::string> argv = {"/usr/bin/sandbox-exec", "-p", profile, tool, "-f", disk};
  std::vector<char*> args;
  for (auto& a : argv) args.push_back(a.data());
  args.push_back(nullptr);
  char* env[] = {const_cast<char*>("E2FSPROGS_CONFIG=/nonexistent"), const_cast<char*>("PATH=/usr/bin:/bin"), nullptr};
  int out[2];
  if (::pipe(out) != 0) return "pipe failed";
  posix_spawn_file_actions_t fa;
  posix_spawn_file_actions_init(&fa);
  posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
  posix_spawn_file_actions_adddup2(&fa, out[1], 1);
  posix_spawn_file_actions_adddup2(&fa, out[1], 2);
  Child child;
  const int rc = posix_spawn(&child.pid, args[0], &fa, nullptr, args.data(), env);
  posix_spawn_file_actions_destroy(&fa);
  ::close(out[1]);
  std::string output;
  if (rc == 0) {
    char buf[4096];
    ssize_t n;
    while ((n = ::read(out[0], buf, sizeof buf)) > 0 || (n < 0 && errno == EINTR))
      if (n > 0 && output.size() < 4096) output.append(buf, size_t(n));
  }
  ::close(out[0]);
  if (rc != 0) { child.pid = -1; return "cannot start resize2fs"; }
  const int status = child.reap();
  if (WIFEXITED(status) && WEXITSTATUS(status) == 0) return "";
  return "resize2fs failed: " + output.substr(0, 1024);
}

}  // namespace sentineld::image
