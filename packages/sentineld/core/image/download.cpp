#include "download.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <curl/curl.h>
#include <fcntl.h>
#include <stdexcept>
#include <thread>
#include <unistd.h>

#include "../util/sha256.hpp"

namespace sentineld::image {
namespace {

constexpr uint64_t kMaxDownload = 64ull << 30;  // a source file

struct Easy {
  CURL* h = curl_easy_init();
  ~Easy() { curl_easy_cleanup(h); }
};

// Transfers abort within a second of `cancel` (the progress callback's return value).
int progress(void* cancel, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
  return static_cast<const std::atomic<bool>*>(cancel)->load() ? 1 : 0;
}

void setup(Easy& e, const std::string& url, const std::atomic<bool>& cancel) {
  if (!e.h) throw std::runtime_error("curl_easy_init failed");
  curl_easy_setopt(e.h, CURLOPT_URL, url.c_str());
  curl_easy_setopt(e.h, CURLOPT_PROTOCOLS_STR, "http,https");
  curl_easy_setopt(e.h, CURLOPT_REDIR_PROTOCOLS_STR, "http,https");
  curl_easy_setopt(e.h, CURLOPT_FOLLOWLOCATION, 1L);  // mirrors and CDNs redirect
  curl_easy_setopt(e.h, CURLOPT_MAXREDIRS, 5L);
  curl_easy_setopt(e.h, CURLOPT_USERAGENT, "sentineld");
  curl_easy_setopt(e.h, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(e.h, CURLOPT_CONNECTTIMEOUT, 30L);
  curl_easy_setopt(e.h, CURLOPT_LOW_SPEED_LIMIT, 1024L);  // stalled for a minute: give up
  curl_easy_setopt(e.h, CURLOPT_LOW_SPEED_TIME, 60L);
  curl_easy_setopt(e.h, CURLOPT_NOPROGRESS, 0L);
  curl_easy_setopt(e.h, CURLOPT_XFERINFOFUNCTION, progress);
  curl_easy_setopt(e.h, CURLOPT_XFERINFODATA, &cancel);
}

long status(CURL* h) {
  long code = 0;
  curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
  return code;
}

// One download: hashed as it is written to a unique part file (created on the first byte), renamed
// into place once verified.
struct Transfer {
  std::string part;
  int fd = -1;
  Sha256 hash;
  uint64_t got = 0;
  int attempts = 0;
  bool failed = false, tooLarge = false;
  Easy easy;
  void reset() {  // the next attempt starts from scratch
    if (fd >= 0) ::close(fd), fd = -1;
    ::unlink(part.c_str());
    hash = Sha256();
    got = 0;
    failed = tooLarge = false;
  }
  bool open() { return fd >= 0 || (fd = ::open(part.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644)) >= 0; }
  ~Transfer() {
    if (fd >= 0) ::close(fd);
    if (!part.empty()) ::unlink(part.c_str());
  }
};

size_t store(char* p, size_t size, size_t n, void* u) {
  auto* t = static_cast<Transfer*>(u);
  const size_t len = size * n;
  if (t->got + len > kMaxDownload) return t->tooLarge = true, 0;
  if (!t->open()) return t->failed = true, 0;
  t->hash.update(p, len);
  t->got += len;
  for (size_t off = 0; off < len;) {
    ssize_t w = ::write(t->fd, p + off, len - off);
    if (w <= 0) return t->failed = true, 0;
    off += size_t(w);
  }
  return len;
}

}  // namespace

void download(const std::string& url, const std::string& digest, const std::string& path, const std::atomic<bool>& cancel) {
  static const bool init = curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK;
  if (!init) throw std::runtime_error("curl_global_init failed");
  // The digest is what makes a source trustworthy, not the transport: some publishers (Microsoft's
  // Windows images) serve their files over plain HTTP only.
  if (url.rfind("https://", 0) != 0 && url.rfind("http://", 0) != 0) throw std::runtime_error("unsupported source URL: " + url);
  Transfer t;
  for (;;) {
    t.part = path + "." + std::to_string(getpid()) + "." + std::to_string(arc4random()) + ".part";
    setup(t.easy, url, cancel);
    curl_easy_setopt(t.easy.h, CURLOPT_WRITEFUNCTION, store);
    curl_easy_setopt(t.easy.h, CURLOPT_WRITEDATA, &t);
    const CURLcode rc = curl_easy_perform(t.easy.h);
    const long code = status(t.easy.h);
    if (cancel) throw std::runtime_error("download cancelled");
    if (rc == CURLE_OK && code == 200) break;
    const bool transient = (rc != CURLE_OK && !t.failed && !t.tooLarge) || code == 429 || code >= 500;
    if (!transient || ++t.attempts >= 3) {
      if (t.failed) throw std::runtime_error("cannot write " + t.part);
      throw std::runtime_error("download failed for " + url + ": " +
                               (rc != CURLE_OK ? std::string(curl_easy_strerror(rc)) : "HTTP " + std::to_string(code)));
    }
    std::fprintf(stderr, "sentineld: retrying %s\n", url.c_str());
    t.reset();
    std::this_thread::sleep_for(std::chrono::seconds(2 * t.attempts));
  }
  if (t.hash.digest() != digest) throw std::runtime_error("download does not match its digest: " + url);
  if (!t.open() || fcntl(t.fd, F_FULLFSYNC) != 0 || ::rename(t.part.c_str(), path.c_str()) != 0)
    throw std::runtime_error("cannot store " + path);
  t.part.clear();
}

}  // namespace sentineld::image
