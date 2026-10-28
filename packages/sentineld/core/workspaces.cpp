#include "workspaces.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <ctime>
#include <fcntl.h>
#include <filesystem>
#include <string_view>
#include <fstream>
#include <future>
#include <memory>
#include <sstream>
#include <optional>
#include <set>
#include <thread>
#include <sys/wait.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "agent.hpp"
#include "catalog.hpp"
#include "computer.hpp"
#include "image/disk.hpp"
#include "image/install.hpp"
#include "image/media.hpp"
#include "loop.hpp"
#include "util/paths.hpp"
#include "util/sha256.hpp"
#include "util/text.hpp"

namespace fs = std::filesystem;

namespace sentineld {

json::Object error(const std::string& code, const std::string& message, json::Object details) {
  json::Object e{{"code", code}, {"message", message}};
  if (!details.empty()) e["details"] = std::move(details);
  return {{"error", std::move(e)}};
}

namespace {

constexpr uint64_t kGiB = 1ull << 30;
constexpr size_t kExecCap = 4 << 20;
constexpr uint64_t kReadyMs = 10 * 60 * 1000;  // first boot may run provisioning
constexpr uint64_t kLayerMs = 45 * 60 * 1000;   // a desktop's packages, on a slow connection
constexpr uint64_t kLayerRootBytes = 8 * kGiB;  // the smallest workspace disk (validate)
constexpr uint64_t kLayerStopMs = 60 * 1000;    // a desktop system's own power off
constexpr uint64_t kShutdownMs = 15 * 1000;  // a guest's own power off, unless its distribution says ("shutdown_s")
// A desktop's display: the guest's mode, refreshed as fast as Mac screens go.
constexpr Display kDisplay{1920, 1200, 120};

bool hasDesktop(const json::Object& spec) {
  auto it = spec.find("desktop");
  return it != spec.end() && it->second.str() != "none";
}

uint64_t now() {
  return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now().time_since_epoch()).count());
}

bool isId(const std::string& s) {
  if (s.empty() || s.size() > 64) return false;
  for (char c : s)
    if (!std::isalnum(static_cast<unsigned char>(c)) && c != '-') return false;
  return true;
}

std::string readFile(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  std::stringstream s;
  s << in.rdbuf();
  return s.str();
}

// Written, flushed to stable storage, then renamed into place, so a crash leaves the old or the
// new file, never a partial one.
void writeAtomic(const std::string& path, const std::string& data) {
  const std::string tmp = path + ".part";
  int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  bool ok = fd >= 0 && ::write(fd, data.data(), data.size()) == ssize_t(data.size()) &&
            fcntl(fd, F_FULLFSYNC) == 0;
  if (fd >= 0) ::close(fd);
  if (!ok || ::rename(tmp.c_str(), path.c_str()) != 0) throw std::runtime_error("cannot write " + path);
  int dir = ::open(fs::path(path).parent_path().c_str(), O_RDONLY | O_CLOEXEC);
  if (dir >= 0) { fsync(dir); ::close(dir); }
}

std::string randomId() {
  uint8_t b[16];
  arc4random_buf(b, sizeof b);
  char s[37];
  std::snprintf(s, sizeof s, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", b[0], b[1], b[2],
                b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
  return s;
}

bool integer(const json::Value& v, double lo, double hi) {
  auto* d = std::get_if<double>(&v.v);
  return d && *d == std::floor(*d) && *d >= lo && *d <= hi;
}

// The machine a distribution needs (its distribution.json "machine").
void machine(const json::Value& m, VmConfig& c) {
  if (!m["transport"].isNull()) c.transport = m["transport"].str();
  // "gpu": "none" (no virtio GPU), "2d", "3d", or absent (Venus).
  if (!m["gpu"].isNull()) c.gpu = m["gpu"].str();
}

// Whether the distribution's guest mounts virtiofs shares (the share, the project, the package
// cache); "disk": it reads the share from a disk sentineld writes at each start instead.
bool sharesFolders(const json::Value& distribution) {
  return distribution["share"].isNull() || distribution["share"].str() != "disk";
}

// How long the guest may take to power itself off before it is powered off regardless: a hard power
// off mid-shutdown leaves its disks as a crash would (Windows: a dirty NTFS volume, checked at boot).
uint64_t shutdownMs(const catalog::Data& catalog, const json::Object& spec) {
  auto d = catalog.distributions.find(spec.at("distribution").str());
  if (d == catalog.distributions.end() || !integer(d->second["shutdown_s"], 1, 600)) return kShutdownMs;
  return uint64_t(d->second["shutdown_s"].num() * 1000);
}

std::string text(const json::Value& v) {
  auto* s = std::get_if<std::string>(&v.v);
  return s ? *s : std::string();
}

// Guest console output as plain printable text (ANSI escapes dropped).
std::string consoleText(std::string_view raw) {
  std::string out;
  for (size_t i = 0; i < raw.size(); ++i) {
    unsigned char c = static_cast<unsigned char>(raw[i]);
    if (c == 0x1B) {  // skip an escape sequence up to its final byte
      while (++i < raw.size() && !(raw[i] >= '@' && raw[i] <= '~' && raw[i] != '[')) {}
      continue;
    }
    if (c == '\n' || (c >= 0x20 && c < 0x7F)) out += char(c);
  }
  return out;
}

// Cuts valid UTF-8 so its JSON-escaped form stays within `cap` bytes.
std::string capEncoded(std::string s, size_t cap, bool& truncated) {
  size_t encoded = 0, i = 0;
  while (i < s.size()) {
    unsigned char c = static_cast<unsigned char>(s[i]);
    size_t n = c < 0x80 ? 1 : (c >> 5) == 6 ? 2 : (c >> 4) == 14 ? 3 : 4;
    size_t cost = c == '"' || c == '\\' || c == '\n' || c == '\r' || c == '\t' ? 2 : c < 0x20 ? 6 : n;
    if (encoded + cost > cap) { s.resize(i); truncated = true; break; }
    encoded += cost;
    i += n;
  }
  return s;
}

// A valid hostname from the workspace name: lowercase letters, digits and '-', at most 63.
std::string hostname(const std::string& name) {
  std::string h;
  for (unsigned char c : name) {
    if (h.size() == 63) break;
    h += std::isalnum(c) ? char(std::tolower(c)) : '-';
  }
  while (!h.empty() && h.back() == '-') h.pop_back();
  while (!h.empty() && h.front() == '-') h.erase(0, 1);
  return h.empty() ? "workspace" : h;
}

// Validates a spec against the fields this build supports. Unknown fields are rejected so an
// older sentineld never silently ignores a newer client's intent.
json::Object validate(const json::Value& spec, const catalog::Data& catalog) {
  auto* o = spec.obj();
  if (!o) return error("invalid", "spec must be an object");
  static const char* const known[] = {"name", "project", "distribution", "resources", "disk_sync", "desktop", "tools"};
  for (auto& [k, _] : *o)
    if (std::find(std::begin(known), std::end(known), k) == std::end(known))
      return error("unsupported", "unknown spec field", {{"field", "spec." + k}});
  std::string name = text(spec["name"]), dist = text(spec["distribution"]);
  if (name.empty() || name.size() > 120) return error("invalid", "name must be 1-120 characters");
  // What the share's catalog and distributions offer (catalog.hpp).
  const std::string desktop = spec["desktop"].isNull() ? "none" : text(spec["desktop"]);
  if (auto problem = catalog::invalid(catalog, dist, desktop, spec["tools"]); !problem.message.empty())
    return error("unsupported", problem.message, {{"field", problem.field}});
  // The distribution's install recipe (distribution.json "install") makes its base disk.
  auto d = catalog.distributions.find(dist);
  if (d == catalog.distributions.end() || d->second["install"].isNull())
    return error("unsupported", dist + " has no install recipe", {{"field", "spec.distribution"}});
  const json::Value& r = spec["resources"];
  if (!r.obj() || !integer(r["cpus"], 1, 32) || !integer(r["memory_gib"], 1, 64) || !integer(r["disk_gib"], 8, 1024))
    return error("invalid", "resources need cpus 1-32, memory_gib 1-64, disk_gib 8-1024");
  for (auto& [k, _] : *r.obj())
    if (k != "cpus" && k != "memory_gib" && k != "disk_gib")
      return error("unsupported", "unknown spec field", {{"field", "spec.resources." + k}});
  // The distribution's own floor (distribution.json "minimum"): below it the guest crawls or fails.
  if (d != catalog.distributions.end())
    if (auto* minimum = d->second["minimum"].obj())
      for (auto& [k, least] : *minimum)
        if (r[k].num() < least.num())
          return error("invalid", dist + " needs at least " + std::to_string(int64_t(least.num())) +
                                      (k == "cpus" ? " CPUs" : k == "memory_gib" ? " GiB of memory" : " GiB of disk"),
                       {{"field", "spec.resources." + k}});
  if (!spec["project"].isNull()) {
    std::string project = text(spec["project"]);
    if (!validProjectPath(project)) return error("invalid", "project must be an absolute path outside guest system directories");
    if (!fs::is_directory(project)) return error("invalid", "project directory does not exist");
  }
  std::string sync = text(spec["disk_sync"]);
  if (!spec["disk_sync"].isNull() && sync != "fsync" && sync != "full")
    return error("unsupported", "unknown disk_sync", {{"field", "spec.disk_sync"}});
  return {};
}

}  // namespace

Workspaces::Workspaces(Loop& loop, Platform& platform, std::string state, std::string cache, std::string mke2fs,
                       std::string share, std::vector<std::string> distributions, Media* media, bool keepImages)
    : loop_(loop), platform_(platform), media_(media), state_(std::move(state)), cache_(std::move(cache)), mke2fs_(std::move(mke2fs)),
      share_(std::move(share)), keepImages_(keepImages), distributionDirs_(std::move(distributions)) {
  fs::create_directories(state_ + "/workspaces");
  try {
    catalog_ = catalog::load(share_, distributionDirs_);
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld: catalog unreadable, no distribution offered: %s\n", e.what());
  }
  const std::string idPath = state_ + "/worker-id";
  workerId_ = readFile(idPath);
  if (workerId_.empty()) writeAtomic(idPath, workerId_ = randomId());

  std::string catalog = readFile(state_ + "/catalog.json");
  if (!catalog.empty()) {
    json::Value c = json::parse(catalog);
    if (auto* all = c["workspaces"].obj())
      for (auto& [id, w] : *all) {
        Workspace ws;
        ws.id = id;
        ws.spec = *w["spec"].obj();
        ws.spec.erase("image");  // recorded by sentineld before every base was installed
        ws.revision = uint64_t(w["revision"].num());
        if (!w["base"].isNull()) ws.base = w["base"].str();
        if (!w["desktop_ready"].isNull()) ws.desktopReady = w["desktop_ready"].str();
        if (auto* keyboard = w["keyboard"].obj()) ws.keyboard = *keyboard;
        workspaces_[id] = std::move(ws);  // every VM is stopped after a restart
      }
  }
  platform_.onExit = [this](const std::string& id, const std::string& err, bool reboot) {
    if (installs_.count(id)) return installExited(id, err, reboot);
    exited(id, err, reboot);
  };
  prune();
}

// The distribution folders as they are now (people edit them while sentineld runs). One a workspace or
// an install uses stays as last read while its folder is broken or gone: its problem is reported,
// and nothing running loses its distribution.
void Workspaces::reloadCatalog() {
  catalog::Data next;
  try {
    next = catalog::load(share_, distributionDirs_);
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld: catalog unreadable, keeping the last one: %s\n", e.what());
    return;
  }
  std::set<std::string> used;
  for (auto& [_, w] : workspaces_)
    if (w.spec.count("distribution")) used.insert(w.spec.at("distribution").str());
  for (auto& [_, in] : installs_) used.insert(in.distribution);
  for (auto& id : used)
    if (!next.distributions.count(id) && catalog_.distributions.count(id)) {
      next.distributions[id] = catalog_.distributions.at(id);
      next.folders[id] = catalog_.folders.at(id);
    }
  catalog_ = std::move(next);
}

std::string Workspaces::shareFile(const std::string& distribution, const std::string& path) const {
  constexpr std::string_view own = "distribution/";
  if (path.compare(0, own.size(), own) == 0) return catalog_.folders.at(distribution) + "/" + path.substr(own.size());
  return share_ + "/" + path;
}

json::Value Workspaces::record(const Workspace& w) const {
  return json::Object{{"spec", w.spec},
                      {"revision", w.revision},
                      {"state", w.state},
                      {"operation", w.operation.empty() ? json::Value() : json::Value(w.operation)},
                      {"error", w.error.empty() ? json::Value() : json::Value(w.error)},
                      {"progress", w.progress.empty() ? json::Value() : json::Value(w.progress)},
                      {"screen", w.vmUp && hasDesktop(w.spec)}};
}

json::Object Workspaces::records() const {
  json::Object all;
  for (auto& [id, w] : workspaces_) all[id] = record(w);
  return all;
}

void Workspaces::save(const Catalog& catalog) const {
  json::Object all;
  for (auto& [id, w] : catalog) {
    json::Object o{{"spec", w.spec}, {"revision", w.revision}};
    if (!w.base.empty()) o["base"] = w.base;
    if (!w.desktopReady.empty()) o["desktop_ready"] = w.desktopReady;
    if (!w.keyboard.empty()) o["keyboard"] = w.keyboard;
    all[id] = std::move(o);
  }
  writeAtomic(state_ + "/catalog.json", json::dump(json::Object{{"workspaces", std::move(all)}}));
}

void Workspaces::changed(const Workspace& w) {
  if (onChange) onChange(w.id, record(w));
}

bool Workspaces::current(const std::string& id, uint64_t epoch) const {
  auto it = workspaces_.find(id);
  return it != workspaces_.end() && it->second.epoch == epoch;
}

Workspaces::Workspace* Workspaces::find(const json::Value& r, Reply& reply) {
  auto it = workspaces_.find(r["workspace"].str());
  if (it != workspaces_.end()) return &it->second;
  reply(error("not_found", "no such workspace"));
  return nullptr;
}

void Workspaces::handle(const std::string& op, const json::Value& r, Reply reply) {
  if (draining_ && (op == "configure" || op == "start"))
    return reply(error("conflict", "sentineld is shutting down", {{"reason", "busy"}}));
  try {
    if (op == "list") return reply({{"workspaces", records()}});
    if (op == "catalog" || op == "configure" || op == "start") reloadCatalog();
    if (op == "catalog") return reply(catalog::list(catalog_));
    if (op == "configure") return configure(r, reply);
    if (op == "start") return start(r, reply);
    if (op == "stop") return stop(r, reply);
    if (op == "delete") return remove(r, reply);
    if (op == "exec") return exec(r, reply);
    if (op == "forward") return forward(r, reply);
    if (op == "display") return display(r, reply);
    if (op == "keyboard") return keyboard(r, reply);
    if (op == "clipboard") return clipboard(r, reply);
    if (op == "wallpaper") return wallpaper(r, reply);
    if (op == "computer") return computer(r, reply);
    if (op == "computer_cancel") return computerCancel(r, reply);
    reply(error("unsupported", "unknown op", {{"field", "op"}}));
  } catch (const json::TypeError& e) {
    reply(error("invalid", e.what()));
  } catch (const std::exception& e) {
    reply(error("internal", e.what()));
  }
}

void Workspaces::configure(const json::Value& r, Reply reply) {
  const std::string id = r["workspace"].str();
  if (!isId(id)) return reply(error("invalid", "workspace must be 1-64 letters, digits or '-'"));
  if (auto e = validate(r["spec"], catalog_); !e.empty()) return reply(e);
  const uint64_t revision = uint64_t(r["revision"].num());
  const json::Object& spec = *r["spec"].obj();

  auto it = workspaces_.find(id);
  if (it != workspaces_.end() && !it->second.operation.empty())
    return reply(error("conflict", "workspace is " + it->second.operation, {{"reason", "busy"}}));
  if (it == workspaces_.end() ? revision != 0 : revision != it->second.revision)
    return reply(error("conflict", "revision mismatch", {{"reason", "revision"}}));
  for (auto& [other, w] : workspaces_)
    if (other != id && w.spec["name"].str() == spec.at("name").str())
      return reply(error("conflict", "name in use", {{"reason", "name"}}));
  if (it != workspaces_.end()) {
    const json::Object& old = it->second.spec;
    if (old.at("distribution").str() != spec.at("distribution").str())
      return reply(error("invalid", "distribution changes only through start{erase}"));
    if (spec.at("resources")["disk_gib"].num() < old.at("resources")["disk_gib"].num())
      return reply(error("invalid", "disk_gib can only grow"));
  }
  Catalog next = workspaces_;  // saved before memory changes, so a failed save changes nothing
  Workspace& w = next[id];
  w.id = id;
  w.spec = spec;
  ++w.revision;
  save(next);
  workspaces_ = std::move(next);
  changed(workspaces_[id]);
  reply({{"record", record(workspaces_[id])}});
}

void Workspaces::start(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (!w->operation.empty()) return reply(error("conflict", "workspace is " + w->operation, {{"reason", "busy"}}));
  if (uint64_t(r["revision"].num()) != w->revision)
    return reply(error("conflict", "revision mismatch", {{"reason", "revision"}}));
  const bool erase = !r["erase"].isNull() && r["erase"].boolean();
  if (!r["spec"].isNull()) {
    if (!erase) return reply(error("invalid", "a new spec requires erase"));
    if (auto e = validate(r["spec"], catalog_); !e.empty()) return reply(e);
    for (auto& [other, o] : workspaces_)
      if (other != w->id && o.spec["name"].str() == r["spec"]["name"].str())
        return reply(error("conflict", "name in use", {{"reason", "name"}}));
    Catalog next = workspaces_;
    next[w->id].spec = *r["spec"].obj();
    next[w->id].base.clear();
    ++next[w->id].revision;
    save(next);
    workspaces_ = std::move(next);
    w = &workspaces_[r["workspace"].str()];
  }
  if (w->state == "running" && !erase) return reply({{"record", record(*w)}});
  w->operation = "preparing";
  w->error.clear();
  ++w->epoch;
  changed(*w);
  reply({{"record", record(*w)}});
  if (w->state != "running") return boot(w->id, erase);
  // Erasing a running workspace: power off without the guest, then reinstall.
  const std::string id = w->id;
  const uint64_t epoch = w->epoch;
  w->quietExit = true;
  platform_.powerOff(id, [this, id, epoch](std::string err) {
    if (!current(id, epoch)) return;
    if (!err.empty()) return fail(id, err);
    boot(id, true);
  });
}

void Workspaces::fail(const std::string& id, const std::string& message) {
  auto it = workspaces_.find(id);
  if (it == workspaces_.end()) return;
  Workspace& w = it->second;
  w.state = "failed";
  w.operation.clear();
  w.progress.clear();
  w.error = printable(message, 1024);
  ++w.epoch;
  abandon();
  changed(w);
  if (w.vmUp) {  // a VM that never became ready is powered off; the failure stays recorded
    w.quietExit = true;
    platform_.powerOff(id, [](std::string) {});
  }
}

// Erase drops the disk first, so a failed reinstall never boots old data under a new spec. An
// existing disk boots as is: the distribution's base matters only when a disk is created from it.
void Workspaces::boot(const std::string& id, bool erase) {
  const uint64_t epoch = workspaces_.at(id).epoch;
  const json::Object spec = workspaces_.at(id).spec;
  const std::string boot = dir(id) + "/boot.raw", root = dir(id) + "/root.raw";
  std::error_code ec;
  if (erase) {
    fs::remove(root, ec);
    fs::remove(boot, ec);
  }
  // The boot disk is cloned last (launch): with it, the workspace has every disk its base has.
  if (fs::exists(boot)) return launch(id, epoch, "");
  auto made = [this, id, epoch](std::string base, std::string err) {
    if (!current(id, epoch)) return;  // cancelled or deleted meanwhile
    if (!err.empty()) return fail(id, err);
    Catalog next = workspaces_;
    next[id].base = fs::path(base).filename();
    next[id].desktopReady.clear();  // a new disk has no desktop yet
    try {
      save(next);
    } catch (const std::exception& e) {
      return fail(id, e.what());
    }
    workspaces_ = std::move(next);
    prune();
    launch(id, epoch, base);
  };
  // The workspace starts from its layer: the distribution's base with the workspace's software (its
  // tools, then its desktop) installed.
  const std::string distribution = spec.at("distribution").str();
  std::vector<std::string> scripts;
  try {
    for (auto& tool : catalog::plan(catalog_, spec.count("tools") ? spec.at("tools") : json::Value(), distribution))
      scripts.push_back(tool.script);
  } catch (const std::exception& e) {
    return fail(id, std::string("tool catalog: ") + e.what());
  }
  if (hasDesktop(spec)) scripts.push_back(catalog::desktop(catalog_, spec.at("desktop").str(), distribution).script);
  std::erase_if(scripts, [](const std::string& s) { return catalog::empty(s); });
  // A distribution whose shell userland comes from the share (Windows: MSYS2) installs it in its
  // layers too, before the tools, whose scripts run in it.
  const bool userland = !catalog_.distributions.at(distribution)["userland"].isNull();
  installDisk(distribution, id, epoch, [this, id, epoch, distribution, scripts, userland, made](std::string base, std::string err) {
    if (err.empty() && (userland || !scripts.empty())) return layerDisk(base, distribution, scripts, id, epoch, made);
    made(base, err);
  });
}

// Boots the workspace disks, first cloning them from `base` (a base disk pair) when given.
void Workspaces::launch(const std::string& id, uint64_t epoch, const std::string& base) {
  // The previous run may still be powering off (a restart right after a failure or a stop): its
  // video feed and VM must be gone first, or the new feed replaces the live one and the new VM
  // cannot start. The platform's power off is bounded, so this wait is too.
  if (workspaces_.at(id).vmUp)
    return void(loop_.after(100, [this, id, epoch, base] { if (current(id, epoch)) launch(id, epoch, base); }));
  const json::Object& spec = workspaces_.at(id).spec;
  const std::string d = dir(id);
  VmConfig c;
  c.id = id;
  c.bootDisk = d + "/boot.raw";
  // A base without a root disk (Windows') keeps everything on its one disk.
  if (fs::exists(d + "/root.raw") || (!base.empty() && fs::exists(base + ".root.raw"))) c.rootDisk = d + "/root.raw";
  c.consoleLog = consoleLog(id);
  c.log = d + "/workspace.log";
  const json::Value& distribution = catalog_.distributions.at(spec.at("distribution").str());
  machine(distribution["machine"], c);
  const std::string folder = catalog_.folders.at(spec.at("distribution").str());
  if (sharesFolders(distribution)) {
    c.shareDir = share_;
    c.distributionDir = folder;
    if (spec.count("project")) c.projectDir = spec.at("project").str();
    // Packages downloaded once, shared by the distribution's workspaces (DESIGN.md §8). Guests write
    // it, so it is only a cache: apk and apt verify every package against their signed index.
    c.packageCacheDir = packageCache(spec.at("distribution").str());
    std::error_code ec;
    fs::create_directories(c.packageCacheDir, ec);
  } else {
    c.shareDisk = d + "/share.raw";  // the share as it is now: a new agent needs no reinstall
  }
  c.cpus = unsigned(spec.at("resources")["cpus"].num());
  c.memoryBytes = uint64_t(spec.at("resources")["memory_gib"].num()) * kGiB;
  c.sync = spec.count("disk_sync") && spec.at("disk_sync").str() == "full" ? DiskSync::Full : DiskSync::Fsync;
  Sha256 h;
  h.update(id.data(), id.size());
  const std::string hex = h.digest().substr(7);
  c.mac[0] = 0x02;  // locally administered, stable per workspace
  for (int i = 1; i < 6; ++i) c.mac[i] = uint8_t(std::stoi(hex.substr(size_t(2 * i - 2), 2), nullptr, 16));
  try {
    fs::create_directories(d);
    if (!base.empty()) {
      if (!c.rootDisk.empty()) platform_.cloneDisk(base + ".root.raw", c.rootDisk);
      platform_.cloneDisk(base + ".boot.raw", c.bootDisk);  // last: boot() takes it for a complete disk set
    }
    if (!c.shareDisk.empty()) image::writeMedia({{"fat32", "SENTINEL", share_, {{"distribution", folder}}}}, c.shareDisk);
    const uint64_t size = uint64_t(spec.at("resources")["disk_gib"].num()) * kGiB;
    if (c.rootDisk.empty()) {
      platform_.growDisk(c.bootDisk, size);  // the guest extends its own partition
    } else {
      // The root disk is the file system alone: growing it is growing the file, then resize2fs.
      platform_.growDisk(c.rootDisk, size);
      // Never fails the start: a file system that cannot grow now (one whose journal needs replaying
      // after a hard stop) boots at its current size and grows on a later start.
      const std::string grown = image::growRoot(c.rootDisk, (fs::path(mke2fs_).parent_path() / "resize2fs").string());
      if (!grown.empty()) std::fprintf(stderr, "sentineld: %s: root file system not grown: %s\n", id.c_str(), grown.c_str());
    }
  } catch (const std::exception& e) {
    return fail(id, e.what());
  }
  // A desktop: the VM gets a display, and its video and input channel to sentineld-media.
  if (hasDesktop(spec)) {
    c.display = kDisplay;
    c.mediaFd = media_ ? media_->attach(id) : -1;
  }
  platform_.start(c, [this, id, epoch](std::string startErr) {
    auto it = workspaces_.find(id);
    if (it != workspaces_.end() && startErr.empty()) {
      it->second.vmUp = true;
      changed(it->second);  // its screen can be viewed from now on
    }
    if (!current(id, epoch)) return;  // a stop or delete is already powering it off
    if (!startErr.empty()) return fail(id, startErr);
    waitReady(id, epoch, now() + kReadyMs);
  });
}

// The workspace is running once the agent reports ready (init settled and a default route).
void Workspaces::waitReady(const std::string& id, uint64_t epoch, uint64_t deadline) {
  agent::call(loop_, platform_, id, {{"op", "status"}}, false, 4096, 3000, [this, id, epoch, deadline](agent::Result r) {
    if (!current(id, epoch)) return;
    json::Value status;
    try { status = json::parse(r.out); } catch (...) {}
    if (r.error.empty() && !status["ready"].isNull() && status["ready"].boolean()) {
      const Workspace& w = workspaces_.at(id);
      const json::Value& distribution = catalog_.distributions.at(w.spec.at("distribution").str());
      std::vector<Step> steps;
      auto run = [](json::Array argv) { return json::Object{{"op", "run"}, {"stderr", "split"}, {"argv", std::move(argv)}}; };
      if (w.spec.count("project") && sharesFolders(distribution))
        steps.push_back({{{"op", "mount"}, {"path", w.spec.at("project").str()}}, "Connecting your project folder…"});
      // A system disk the guest grows itself (an installed distribution: no root for resize2fs) is
      // grown by its own command after sentineld grew the file; it is a no-op once full size.
      if (!distribution["grow"].isNull()) {
        json::Array argv;
        for (auto& a : distribution["grow"].arr()) argv.push_back(a.str());
        steps.push_back({run(std::move(argv)), "Preparing your workspace…", 5 * 60 * 1000, true});
      }
      // The catalog's tools: the base every workspace gets, then the spec's, each after what it needs.
      std::vector<catalog::Install> tools;
      try {
        tools = catalog::plan(catalog_, w.spec.count("tools") ? w.spec.at("tools") : json::Value(), w.spec.at("distribution").str());
      } catch (const std::exception& e) {
        return fail(id, std::string("tool catalog: ") + e.what());
      }
      for (auto& tool : tools)
        steps.push_back({run({"sh", "-c", tool.script}), tool.hidden ? "Preparing your workspace…" : "Installing " + tool.name + "…",
                         30 * 60 * 1000});
      if (hasDesktop(w.spec)) {
        const std::string desktop = w.spec.at("desktop").str(), name = w.spec.at("distribution").str();
        // The first install downloads the desktop from the distribution's mirrors: minutes, not seconds,
        // on a slow connection many. The agent retries a stalled download itself; this only bounds the total.
        // A desktop the OS already has (Windows) has nothing to install, nor a session to start.
        if (const std::string script = catalog::desktop(catalog_, desktop, name).script; !catalog::empty(script))
          steps.push_back({run({"sh", "-c", script}),
                           w.desktopReady == desktop ? "Checking your desktop…"
                                                     : "Installing your desktop — the first start takes a few minutes…",
                           45 * 60 * 1000});
        if (!distribution["desktop"].isNull())
          steps.push_back({run({"sh", "-c", catalog::desktopStart(catalog_, desktop, name)}), "Starting your desktop…",
                           3 * 60 * 1000});
        // The viewer's keyboard layouts; the desktop works with its own if they cannot be applied.
        if (!w.keyboard.empty()) {
          json::Object request = w.keyboard;
          request["op"] = "keyboard";
          steps.push_back({std::move(request), "Setting your keyboard layout…", 60 * 1000, true});
        }
      }
      // The distribution's hostname command, "{name}" the workspace's name made a valid hostname.
      if (!distribution["hostname"].isNull()) {
        json::Array argv;
        for (auto& a : distribution["hostname"].arr()) argv.push_back(a.str() == "{name}" ? hostname(w.spec.at("name").str()) : a.str());
        steps.push_back({run(std::move(argv)), "Finishing up…"});
      }
      return provision(id, epoch, std::move(steps), 0);
    }
    // The console tail turns a kernel panic or a timeout into an error the user can act on.
    const std::string console = consoleText(platform_.consoleTail(id, 8192));
    if (size_t at = console.find("Kernel panic"); at != std::string::npos)
      return fail(id, console.substr(console.rfind('\n', at) + 1, 1024));
    if (now() > deadline)
      return fail(id, "the guest agent did not become ready:\n" + console.substr(console.size() - std::min<size_t>(console.size(), 1024)));
    loop_.after(300, [this, id, epoch, deadline] { if (current(id, epoch)) waitReady(id, epoch, deadline); });
  });
}

// Runs provisioning requests in order; any failure fails the start.
void Workspaces::provision(const std::string& id, uint64_t epoch, std::vector<Step> steps, size_t next) {
  if (!current(id, epoch)) return;
  Workspace& w = workspaces_.at(id);
  if (next == steps.size()) {
    w.state = "running";
    w.reboots = 0;
    w.operation.clear();
    w.progress.clear();
    trimPackageCaches();
    if (hasDesktop(w.spec) && w.desktopReady != w.spec.at("desktop").str()) {
      Catalog after = workspaces_;
      after[id].desktopReady = w.spec.at("desktop").str();
      try {
        save(after);
        w.desktopReady = after[id].desktopReady;
      } catch (const std::exception&) {}  // only the next start's wording depends on it
    }
    return changed(w);
  }
  if (w.progress != steps[next].message) {
    w.progress = steps[next].message;
    changed(w);
  }
  const json::Object request = steps[next].request;
  const uint64_t limit = steps[next].timeoutMs;
  agent::call(loop_, platform_, id, request, true, 256 * 1024, limit,
              [this, id, epoch, steps = std::move(steps), next](agent::Result r) mutable {
                if (!current(id, epoch)) return;
                if (!r.error.empty() || r.timedOut || r.code.value_or(1) != 0) {
                  // Name the step (its command, for runs) and why it failed: a blank reason helps nobody.
                  const json::Object& step = steps[next].request;
                  std::string what = step.at("op").str();
                  if (step.count("argv"))
                    for (const json::Value& a : step.at("argv").arr()) what += " " + a.str().substr(0, 80);
                  std::string why = !r.error.empty() ? r.error
                                    : r.timedOut ? "timed out after " + std::to_string(steps[next].timeoutMs / 1000) + " s"
                                                 : "exit " + std::to_string(r.code.value_or(-1));
                  // The end of the output: where a failing command says why.
                  const std::string err = printable(r.err.size() > 512 ? r.err.substr(r.err.size() - 512) : r.err, 512);
                  if (!steps[next].optional)
                    return fail(id, "provisioning (" + what.substr(0, 200) + "): " + why + (err.empty() ? "" : "\n" + err));
                  std::fprintf(stderr, "sentineld: %s: %s: %s%s%s\n", id.c_str(), what.c_str(), printable(why, 512).c_str(),
                               err.empty() ? "" : ": ", err.c_str());
                }
                if (steps[next].request.at("op").str() == "keyboard") {
                  json::Value applied;
                  try { applied = json::parse(r.out); } catch (...) {}
                  keepKeys(id, applied);
                }
                provision(id, epoch, std::move(steps), next + 1);
              });
}

// Where a command starts: the project, mounted at its host path, or root's home; a guest that
// mounts no folders (Windows) starts it in its own default.
json::Value Workspaces::cwd(const Workspace& w) const {
  auto d = catalog_.distributions.find(w.spec.at("distribution").str());
  if (d != catalog_.distributions.end() && !sharesFolders(d->second)) return json::Value();
  return w.spec.count("project") ? w.spec.at("project").str() : "/root";
}

void Workspaces::closeForwards(Workspace& w) {
  for (auto& [port, fd] : w.forwards) {
    loop_.remove(fd);
    ::close(fd);
    ::unlink(forwardPath(w.id, port).c_str());
  }
  w.forwards.clear();
}

// Each connection to a forward socket is bridged to a new agent `forward` connection. Out of
// descriptors, the listener pauses for 100 ms instead of spinning on a connection it cannot take.
void Workspaces::acceptForwards(const std::string& id, int fd, int port) {
  loop_.onReadable(fd, [this, id, fd, port] {
    int client;
    while ((client = ::accept(fd, nullptr, nullptr)) >= 0) {
      fcntl(client, F_SETFD, FD_CLOEXEC);
      json::Object request{{"op", "forward"}, {"port", port}};
      agent::open(loop_, platform_, id, request, 10000, [this, client, port](int vfd, std::string pending, std::string err) {
        if (vfd < 0) {  // refused in the guest: the client sees EOF
          std::fprintf(stderr, "sentineld: forward to guest port %d: %s\n", port, printable(err, 1024).c_str());
          ::close(client);
          return;
        }
        agent::bridge(loop_, client, vfd, std::move(pending));
      });
    }
    if (errno != EMFILE && errno != ENFILE) return;
    loop_.remove(fd);
    loop_.after(100, [this, id, fd, port] {
      auto it = workspaces_.find(id);
      if (it != workspaces_.end() && it->second.forwards.count(port) && it->second.forwards.at(port) == fd)
        acceptForwards(id, fd, port);
    });
  });
}

// <state>/forwards/<id>-<port>.sock; a state path too long for a socket address uses
// /tmp/sentinel-forward-<sha256(state)[:16]>/ instead (clients derive the same path).
std::string Workspaces::forwardPath(const std::string& id, int port) const {
  const std::string name = "/" + id + "-" + std::to_string(port) + ".sock";
  if ((state_ + "/forwards" + name).size() < sizeof(sockaddr_un{}.sun_path)) return state_ + "/forwards" + name;
  Sha256 h;
  h.update(state_.data(), state_.size());
  return "/tmp/sentinel-forward-" + h.digest().substr(7, 16) + name;
}

// A directory only we can enter: created 0700, or an existing one we own that nobody else can write.
static bool privateDir(const std::string& dir) {
  ::mkdir(dir.c_str(), 0700);
  struct stat st;
  return ::lstat(dir.c_str(), &st) == 0 && S_ISDIR(st.st_mode) && st.st_uid == ::getuid() && !(st.st_mode & 077);
}

void Workspaces::forward(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (w->state != "running" || !w->operation.empty())
    return reply(error("conflict", "workspace is not running", {{"reason", "stopped"}}));
  if (!integer(r["port"], 1, 65535)) return reply(error("invalid", "port must be 1-65535"));
  const int port = int(r["port"].num());
  const std::string path = forwardPath(w->id, port);
  if (w->forwards.count(port)) return reply({{"socket", path}});

  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  if (path.size() >= sizeof addr.sun_path) return reply(error("internal", "state directory path is too long for a socket"));
  std::memcpy(addr.sun_path, path.c_str(), path.size() + 1);
  if (!privateDir(path.substr(0, path.rfind('/')))) return reply(error("internal", "cannot create the forward directory"));
  ::unlink(path.c_str());
  int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
  mode_t old = ::umask(077);
  int bound = fd < 0 ? -1 : ::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr);
  ::umask(old);
  if (bound != 0 || ::listen(fd, 64) != 0) {
    if (fd >= 0) ::close(fd);
    return reply(error("internal", "cannot listen on " + path));
  }
  fcntl(fd, F_SETFL, O_NONBLOCK);
  fcntl(fd, F_SETFD, FD_CLOEXEC);
  w->forwards[port] = fd;
  const std::string id = w->id;
  acceptForwards(id, fd, port);
  reply({{"socket", path}});
}

// A base disk is made (or failed): every boot waiting for it continues.
void Workspaces::converted(const std::string& distribution, const std::string& disk, const std::string& err) {
  Conversion c = std::move(converting_.at(distribution));
  converting_.erase(distribution);
  if (err.empty()) lastBase_ = fs::path(disk).filename();
  for (auto& w : c.waiters) {
    // Abandoned, then wanted again before the work stopped: that boot starts it anew.
    if (*c.cancel && !draining_ && !err.empty() && current(w.id, w.epoch) && catalog_.distributions.count(distribution))
      installDisk(distribution, w.id, w.epoch, std::move(w.done));
    else w.done(disk, err);
  }
  prune();
  if (drained_ && converting_.empty()) std::exchange(drained_, nullptr)();
}

// An installed distribution's base disk (image/install.hpp), made once per recipe: the sources are
// downloaded and turned into install media on a worker thread, then an install VM boots the media
// with an empty target disk first (the firmware falls through to the media while the target cannot
// boot; once the installer has made it bootable, restarts boot it). The install ends when the guest
// powers off, and sentinel/result on the media or the installed disk's first partition says how:
// "ok", or why it failed.
void Workspaces::installDisk(const std::string& distribution, const std::string& id, uint64_t epoch, Converted done) {
  const json::Value& d = catalog_.distributions.at(distribution);
  const std::string folder = catalog_.folders.at(distribution);
  std::string key;
  try {
    key = image::installKey(d["install"], folder).substr(0, 32);
  } catch (const std::exception& e) {
    return loop_.post([done, err = std::string("install: ") + e.what()] { done("", err); });
  }
  const std::string base = cache_ + "/images/install-" + key;
  if (fs::exists(base + ".boot.raw")) return loop_.post([done, base] { done(base, ""); });
  if (auto w = workspaces_.find(id); w != workspaces_.end()) {
    w->second.progress = "Installing the operating system — the first start takes a while…";
    changed(w->second);
  }
  auto& conversion = converting_[distribution];
  conversion.waiters.push_back({id, epoch, std::move(done)});
  if (conversion.waiters.size() > 1) return;
  const std::string work = cache_ + "/install/" + key, tools = fs::path(mke2fs_).parent_path().string();
  std::thread([this, distribution, folder, key, base, work, tools, install = d["install"], cancel = conversion.cancel] {
    std::string err;
    try {
      image::prepareInstall(install, folder, cache_ + "/sources", work, tools, *cancel);
    } catch (const std::exception& e) {
      err = e.what();
    }
    loop_.post([this, distribution, key, base, work, err, cancel] {
      if (!err.empty() || *cancel) return converted(distribution, "", "install: " + (err.empty() ? "cancelled" : err));
      const json::Value& d = catalog_.distributions.at(distribution);
      const json::Value& install = d["install"];
      Install in{distribution, base, work, {}, cancel, 0, false, {}, ""};
      VmConfig& c = in.vm;
      c.id = key;  // hex: never a workspace id
      c.bootDisk = work + "/target.raw";
      if (fs::exists(work + "/root.raw")) c.rootDisk = work + "/root.raw";
      c.consoleLog = work + "/console.log";
      c.log = work + "/vm.log";
      c.cpus = unsigned(install["cpus"].num());
      // An installer's memory is its file cache: the recipe asks for plenty, and gets at most half of
      // the host's.
      const uint64_t host = uint64_t(::sysconf(_SC_PHYS_PAGES)) * uint64_t(::sysconf(_SC_PAGESIZE));
      c.memoryBytes = std::min(uint64_t(install["memory_gib"].num()) * kGiB, host / 2 / kGiB * kGiB);
      for (int i = 0; i < 6; ++i) c.mac[i] = uint8_t(std::stoi(key.substr(size_t(2 * i), 2), nullptr, 16));
      c.mac[0] = 0x02;
      c.display = kDisplay;  // the installer's screen: what a person (or a screenshot) can follow
      machine(d["machine"], c);
      c.network = install["network"].isNull() || install["network"].boolean();  // off: no online setup steps
      const uint64_t limit = uint64_t(install["timeout_min"].num()) * 60 * 1000;
      in.timer = loop_.after(limit, [this, vm = c.id] {
        auto it = installs_.find(vm);
        if (it == installs_.end()) return;
        it->second.timedOut = true;
        platform_.powerOff(vm, [](std::string) {});
      });
      const VmConfig config = c;
      installs_[c.id] = std::move(in);
      platform_.start(config, [this, vm = config.id](std::string startErr) {
        if (!startErr.empty() && installs_.count(vm)) installExited(vm, startErr, false);
      });
    });
  }).detach();
}

void Workspaces::installExited(const std::string& vm, const std::string& error, bool reboot) {
  Install& in = installs_.at(vm);
  if (reboot && error.empty() && !*in.cancel && !in.timedOut) {
    return platform_.start(in.vm, [this, vm](std::string startErr) {
      if (!startErr.empty() && installs_.count(vm)) installExited(vm, startErr, false);
    });
  }
  loop_.cancel(in.timer);
  std::string why = error;
  if (in.timedOut) why = "the installer did not finish in time";
  else if (*in.cancel) why = "cancelled";
  else if (!in.layer.empty()) why = why.empty() ? in.failure : why;
  else if (why.empty()) try {
    // The installed system reports on its disk's first partition (its EFI system partition).
    auto result = image::readMedia(in.work + "/target.raw", "sentinel/result");
    std::string text = result.value_or("");
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back()))) text.pop_back();
    if (!result) why = "the installer stopped without a result";
    else if (text != "ok") why = "the installer failed: " + printable(text, 512);
  } catch (const std::exception& e) {
    why = e.what();
  }
  std::error_code ec;
  if (why.empty()) {
    fs::create_directories(cache_ + "/images", ec);
    // The root first: the boot disk being there says the install is complete.
    if (fs::exists(in.work + "/root.raw") && ::rename((in.work + "/root.raw").c_str(), (in.base + ".root.raw").c_str()) != 0)
      why = "cannot store the installed disk";
    else if (::rename((in.work + "/target.raw").c_str(), (in.base + ".boot.raw").c_str()) != 0)
      why = "cannot store the installed disk";
  }
  // Installed: the sources are needed again only for a new recipe, which downloads its own.
  if (why.empty() && in.layer.empty())
    if (auto* sources = catalog_.distributions.at(in.distribution)["install"]["sources"].obj())
      for (auto& [_, source] : *sources) fs::remove(cache_ + "/sources/" + source["sha256"].str(), ec);
  // The work folder goes either way; a failed install keeps its log and its disk (the installer's
  // own logs are on it) for the next look, until the next attempt or a success.
  fs::remove(in.work + ".failed.raw", ec);
  if (why.empty())  // installed: no earlier failure (of this recipe or an older one) needs a look any more
    for (auto& e : fs::directory_iterator(cache_ + "/install", ec))
      if (e.path().extension() == ".raw" || e.path().extension() == ".log") fs::remove(e.path(), ec);
  if (!why.empty()) {
    fs::copy_file(in.work + "/console.log", in.work + ".console.log", fs::copy_options::overwrite_existing, ec);
    fs::rename(in.work + "/target.raw", in.work + ".failed.raw", ec);
  }
  fs::remove_all(in.work, ec);
  const std::string base = in.base, name = in.layer.empty() ? in.distribution : fs::path(base).filename().string();
  const std::string what = in.layer.empty() ? "install: " : "layer: ";
  const bool layer = !in.layer.empty();
  const std::string distribution = in.distribution;
  installs_.erase(vm);
  converted(name, why.empty() ? base : "", why.empty() ? "" : what + why);
  // The distribution's next layer, now that its package cache is free.
  if (auto q = layerQueue_.find(distribution); layer && q != layerQueue_.end()) {
    auto next = std::move(q->second.front());
    q->second.erase(q->second.begin());
    if (q->second.empty()) layerQueue_.erase(q);
    next();
  }
}

// A layer: the distribution's base with a workspace's software installed (its tools and desktop),
// made once per base and install scripts, so a workspace starts with its software instead of
// installing it. It boots a copy of the base as a workspace would, runs the scripts through the
// agent and powers off as the distribution does (a clean root, which workspaces grow). The layer
// names its base (<layer>.parent), which the cache keeps while the layer is.
void Workspaces::layerDisk(const std::string& base, const std::string& distribution, std::vector<std::string> scripts,
                           const std::string& id, uint64_t epoch, Converted done) {
  Sha256 h;
  const std::string parent = fs::path(base).filename().string();
  h.update(parent.data(), parent.size() + 1);
  for (auto& script : scripts) h.update(script.data(), script.size() + 1);
  // The userland's command and the share files it installs from (`key`): a new userland, a new layer.
  if (const json::Value& u = catalog_.distributions.at(distribution)["userland"]; !u.isNull()) {
    const std::string command = json::dump(u["run"]);
    h.update(command.data(), command.size() + 1);
    for (auto& file : u["key"].arr()) {
      const std::string data = readFile(shareFile(distribution, file.str()));
      h.update(data.data(), data.size());
    }
  }
  const std::string key = h.digest().substr(7, 32), name = "layer-" + key, layer = cache_ + "/images/" + name;
  if (fs::exists(layer + ".boot.raw")) return loop_.post([done, layer] { done(layer, ""); });
  if (auto w = workspaces_.find(id); w != workspaces_.end()) {
    w->second.progress = "Installing your software — the first start takes a few minutes…";
    changed(w->second);
  }
  auto& conversion = converting_[name];
  conversion.waiters.push_back({id, epoch, std::move(done)});
  if (conversion.waiters.size() > 1) return;
  const bool building = std::any_of(installs_.begin(), installs_.end(), [&](auto& i) {
    return !i.second.layer.empty() && i.second.distribution == distribution;
  });
  if (building) {
    layerQueue_[distribution].push_back([this, base, distribution, scripts = std::move(scripts), name, cancel = conversion.cancel] {
      if (*cancel) return converted(name, "", "layer: cancelled");
      startLayer(base, distribution, scripts, name);
    });
    return;
  }
  startLayer(base, distribution, std::move(scripts), name);
}

// Boots a copy of `base` as the layer `name` and installs `scripts` in it (layerDisk).
void Workspaces::startLayer(const std::string& base, const std::string& distribution, std::vector<std::string> scripts,
                            const std::string& name) {
  const std::string parent = fs::path(base).filename().string(), key = name.substr(6), layer = cache_ + "/images/" + name;
  const std::string work = cache_ + "/install/" + name;
  const auto& conversion = converting_.at(name);
  const json::Value& d = catalog_.distributions.at(distribution);
  // Its steps: the userland's command (the distribution's own), then each script through `sh`.
  std::vector<json::Array> steps;
  if (const json::Value& u = d["userland"]; !u.isNull()) {
    json::Array argv;
    for (auto& a : u["run"].arr()) argv.push_back(a.str());
    steps.push_back(std::move(argv));
  }
  for (auto& script : scripts) steps.push_back(json::Array{"sh", "-c", std::move(script)});
  Install in{distribution, layer, work, {}, conversion.cancel, 0, false, std::move(steps), ""};
  VmConfig& c = in.vm;
  c.id = key;  // hex: never a workspace id
  c.consoleLog = work + "/console.log";
  c.log = work + "/vm.log";
  try {
    fs::remove_all(work);
    fs::create_directories(work);
    if (fs::exists(base + ".root.raw")) {
      c.rootDisk = work + "/root.raw";
      platform_.cloneDisk(base + ".root.raw", c.rootDisk);
      // Room for the desktop, at most the smallest workspace disk: workspaces only grow it.
      platform_.growDisk(c.rootDisk, kLayerRootBytes);
      if (const std::string grown = image::growRoot(c.rootDisk, (fs::path(mke2fs_).parent_path() / "resize2fs").string()); !grown.empty())
        throw std::runtime_error(grown);
    }
    c.bootDisk = work + "/target.raw";
    platform_.cloneDisk(base + ".boot.raw", c.bootDisk);
    fs::create_directories(cache_ + "/images");
    std::ofstream(layer + ".parent") << parent;
  } catch (const std::exception& e) {
    return loop_.post([this, name, err = std::string("layer: ") + e.what()] { converted(name, "", err); });
  }
  machine(d["machine"], c);
  if (sharesFolders(d)) {
    c.shareDir = share_;
    c.distributionDir = catalog_.folders.at(distribution);
    c.packageCacheDir = packageCache(distribution);
    std::error_code ec;
    fs::create_directories(c.packageCacheDir, ec);
  } else {
    // A guest that reads the share from a disk boots the machine its workspaces do, the installer's
    // (§7: its devices keep their addresses, with the display's first), with the share disk.
    c.shareDisk = work + "/share.raw";
    c.display = kDisplay;
    try {
      image::writeMedia({{"fat32", "SENTINEL", share_, {{"distribution", catalog_.folders.at(distribution)}}}}, c.shareDisk);
    } catch (const std::exception& e) {
      return loop_.post([this, name, err = std::string("layer: ") + e.what()] { converted(name, "", err); });
    }
  }
  c.cpus = 4;
  // At least the distribution's own floor (Windows: 8 GiB, its file cache), at most half the host's.
  const uint64_t host = uint64_t(::sysconf(_SC_PHYS_PAGES)) * uint64_t(::sysconf(_SC_PAGESIZE));
  const uint64_t least = d["minimum"]["memory_gib"].isNull() ? 4 : std::max<uint64_t>(4, uint64_t(d["minimum"]["memory_gib"].num()));
  c.memoryBytes = std::min(least * kGiB, host / 2 / kGiB * kGiB);
  for (int i = 0; i < 6; ++i) c.mac[i] = uint8_t(std::stoi(key.substr(size_t(2 * i), 2), nullptr, 16));
  c.mac[0] = 0x02;
  in.timer = loop_.after(kLayerMs, [this, vm = c.id] {
    auto it = installs_.find(vm);
    if (it == installs_.end()) return;
    it->second.timedOut = true;
    platform_.powerOff(vm, [](std::string) {});
  });
  const VmConfig config = c;
  installs_[c.id] = std::move(in);
  platform_.start(config, [this, vm = config.id](std::string startErr) {
    if (!installs_.count(vm)) return;
    if (!startErr.empty()) return installExited(vm, startErr, false);
    layerReady(vm, now() + kReadyMs);
  });
}

// Waits for a layer VM's agent, then runs its scripts.
void Workspaces::layerReady(const std::string& vm, uint64_t deadline) {
  agent::call(loop_, platform_, vm, {{"op", "status"}}, false, 4096, 3000, [this, vm, deadline](agent::Result r) {
    auto it = installs_.find(vm);
    if (it == installs_.end()) return;
    json::Value status;
    try { status = json::parse(r.out); } catch (...) {}
    if (r.error.empty() && !status["ready"].isNull() && status["ready"].boolean()) return layerRun(vm, 0);
    if (now() > deadline) {
      it->second.failure = "the guest agent did not become ready";
      return platform_.powerOff(vm, [](std::string) {});
    }
    loop_.after(300, [this, vm, deadline] { if (installs_.count(vm)) layerReady(vm, deadline); });
  });
}

// Runs a layer's scripts in order from `next`; after the last, or the first that fails, powers the
// guest off the distribution's way, so the root is clean; a guest that does not stop is stopped.
void Workspaces::layerRun(const std::string& vm, size_t next) {
  auto it = installs_.find(vm);
  if (it == installs_.end()) return;
  Install& in = it->second;
  if (next < in.layer.size() && in.failure.empty()) {
    json::Object run{{"op", "run"}, {"stderr", "split"}, {"argv", in.layer[next]}};
    return agent::call(loop_, platform_, vm, run, true, 64 * 1024, kLayerMs, [this, vm, next](agent::Result r) {
      auto it = installs_.find(vm);
      if (it == installs_.end()) return;
      if (!r.error.empty() || r.code != 0)
        it->second.failure = "the software did not install: " +
                             printable(r.error.empty() ? r.err.substr(r.err.size() - std::min<size_t>(r.err.size(), 1024)) : r.error, 1024);
      layerRun(vm, next + 1);
    });
  }
  json::Array argv;
  for (auto& a : catalog_.distributions.at(in.distribution)["poweroff"].arr()) argv.push_back(a.str());
  agent::call(loop_, platform_, vm, {{"op", "run"}, {"argv", std::move(argv)}}, true, 4096, 10000, [](agent::Result) {});
  loop_.cancel(in.timer);
  // As long as the distribution's own shutdown may take (Windows: 120 s), at least a desktop system's.
  const json::Value& grace = catalog_.distributions.at(in.distribution)["shutdown_s"];
  const uint64_t stop = std::max(kLayerStopMs, integer(grace, 1, 600) ? uint64_t(grace.num() * 1000) : 0);
  in.timer = loop_.after(stop, [this, vm] {
    auto it = installs_.find(vm);
    if (it == installs_.end()) return;
    if (it->second.failure.empty()) it->second.failure = "the guest did not power off";
    platform_.powerOff(vm, [](std::string) {});
  });
}

// Stops installs that no current boot waits for (the workspace stopped, failed or was deleted).
void Workspaces::abandon() {
  for (auto& [_, c] : converting_)
    if (std::none_of(c.waiters.begin(), c.waiters.end(), [&](auto& w) { return current(w.id, w.epoch); })) *c.cancel = true;
  for (auto& [vm, in] : installs_)
    if (*in.cancel) platform_.powerOff(vm, [](std::string) {});
}

// A new console log for each boot, kept in <state>/logs/<id>/ (outside the workspace directory, so a
// reinstall or delete keeps it): the kernel's own account of a guest that failed. The newest 5 stay.
std::string Workspaces::consoleLog(const std::string& id) const {
  const fs::path logs = fs::path(state_) / "logs" / id;
  std::error_code ec;
  fs::create_directories(logs, ec);
  std::vector<fs::path> old;
  for (const auto& e : fs::directory_iterator(logs, ec))
    if (e.path().filename().string().rfind("console-", 0) == 0) old.push_back(e.path());
  std::sort(old.begin(), old.end());  // the names sort by time
  for (size_t i = 0; i + 4 < old.size(); i++) fs::remove(old[i], ec);
  char stamp[32];
  const std::time_t t = std::time(nullptr);
  std::strftime(stamp, sizeof stamp, "%Y%m%dT%H%M%SZ", std::gmtime(&t));
  return (logs / ("console-" + std::string(stamp) + ".log")).string();
}

std::string Workspaces::packageCache(const std::string& distribution) const {
  return cache_ + "/packages/" + distribution;
}

// Keeps each distribution's package cache under kPackageCacheBytes, dropping the oldest files first:
// guests fill it, and a full host disk breaks every VM.
void Workspaces::trimPackageCaches() const {
  constexpr uint64_t kPackageCacheBytes = 4ull << 30;
  std::error_code ec;
  for (const auto& dist : fs::directory_iterator(cache_ + "/packages", ec)) {
    std::vector<std::pair<fs::file_time_type, fs::path>> files;
    uint64_t total = 0;
    for (auto it = fs::recursive_directory_iterator(dist.path(), fs::directory_options::skip_permission_denied, ec);
         it != fs::recursive_directory_iterator(); it.increment(ec)) {
      if (ec) break;
      if (!it->is_regular_file(ec) || it->is_symlink(ec)) continue;
      total += it->file_size(ec);
      files.push_back({it->last_write_time(ec), it->path()});
    }
    if (total <= kPackageCacheBytes) continue;
    std::sort(files.begin(), files.end());
    for (const auto& [_, path] : files) {
      const uint64_t size = fs::file_size(path, ec);
      if (fs::remove(path, ec)) total -= std::min(total, size);
      if (total <= kPackageCacheBytes) break;
    }
  }
}

// Caches: a base disk lives while a workspace records it as its base, or while it is the newest one.
// Runs only between installs, which write into the cache.
void Workspaces::prune() {
  trimPackageCaches();
  if (!converting_.empty()) return;
  std::set<std::string> bases, keep;
  for (auto& [_, w] : workspaces_)
    if (!w.base.empty()) bases.insert(w.base);
  if (!lastBase_.empty()) bases.insert(lastBase_);  // its boot may have been cancelled; the next start likely wants it
  // A desktop layer's base (its .parent) stays while the layer does.
  for (auto& b : std::set<std::string>(bases))
    if (std::string parent = readFile(cache_ + "/images/" + b + ".parent"); !parent.empty()) bases.insert(parent);
  for (auto& b : bases) {
    keep.insert(b + ".boot.raw");
    keep.insert(b + ".root.raw");
    keep.insert(b + ".parent");
  }
  std::error_code ec;
  if (!keepImages_)
    for (auto& e : fs::directory_iterator(cache_ + "/images", ec))
      if (!keep.count(e.path().filename().string())) fs::remove(e.path(), ec);
  fs::remove_all(cache_ + "/blobs", ec);  // image layers sentineld pulled before every base was installed
  // Install sources no distribution's recipe pins any more (a new pin, a removed distribution).
  if (installs_.empty() && !catalog_.distributions.empty()) {  // no catalog: nothing is known unpinned
    std::set<std::string> pinned;
    for (auto& [_, d] : catalog_.distributions)
      if (auto* sources = d["install"]["sources"].obj())
        for (auto& [_, source] : *sources) pinned.insert(source["sha256"].str());
    for (auto& e : fs::directory_iterator(cache_ + "/sources", ec))
      if (!pinned.count(e.path().filename().string())) fs::remove(e.path(), ec);
  }
}

// A viewer ticket for a running desktop (DESIGN.md §6): the client connects to the loopback port
// with WebTransport, pins the certificate by hash and presents the ticket once.
void Workspaces::display(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (!w->vmUp || !hasDesktop(w->spec)) return reply(error("conflict", "workspace has no running desktop", {{"reason", "state"}}));
  json::Object endpoint = media_ ? media_->display(w->id) : json::Object{};
  if (endpoint.empty()) return reply(error("internal", "viewer endpoint is not running"));
  reply(std::move(endpoint));
}

// The viewer's keyboard layouts (DESIGN.md §8): kept for the workspace and applied to its running
// desktop by the agent, now and after every desktop start.
void Workspaces::keyboard(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (!hasDesktop(w->spec)) return reply(error("unsupported", "workspace has no desktop", {{"field", "workspace"}}));
  json::Object layouts;
  for (const char* field : {"layout", "variant", "model", "options"}) {
    const json::Value& v = r[field];
    if (v.isNull()) continue;
    if (!std::holds_alternative<std::string>(v.v) || v.str().size() > 256) return reply(error("invalid", std::string(field) + " must be an XKB string"));
    layouts[field] = v;
  }
  if (!layouts.count("layout") || layouts.at("layout").str().empty()) return reply(error("invalid", "layout is required"));
  if (json::dump(layouts) != json::dump(w->keyboard)) {
    Catalog after = workspaces_;
    after[w->id].keyboard = layouts;
    save(after);
    w->keyboard = std::move(layouts);
  }
  // Not running: the next desktop start applies them.
  if (!w->vmUp || w->state != "running" || !w->operation.empty()) return reply({{"applied", json::Value()}});
  json::Object request = w->keyboard;
  request["op"] = "keyboard";
  agent::call(loop_, platform_, w->id, request, true, 256 * 1024, 60 * 1000, [this, id = w->id, reply](agent::Result res) {
    if (!res.error.empty()) return reply(error("guest", res.error));
    if (res.timedOut || res.code.value_or(1) != 0) return reply(error("guest", "the keyboard layouts were not applied"));
    json::Value applied;
    try { applied = json::parse(res.out); } catch (...) {}
    keepKeys(id, applied);
    reply({{"applied", applied.obj() ? json::Value(json::Object{{"layout", applied["layout"]}, {"variant", applied["variant"]}}) : json::Value()}});
  });
}

// One clipboard transfer with the workspace's running desktop (DESIGN.md §8), by the agent.
void Workspaces::clipboard(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (!hasDesktop(w->spec)) return reply(error("unsupported", "workspace has no desktop", {{"field", "workspace"}}));
  if (!w->vmUp || w->state != "running" || !w->operation.empty())
    return reply(error("conflict", "workspace has no running desktop", {{"reason", "state"}}));
  const std::string action = r["action"].isNull() ? "" : r["action"].str();
  json::Object request{{"op", "clipboard"}, {"action", action}};
  if (action == "write") {
    if (!std::holds_alternative<std::string>(r["text"].v) || r["text"].str().size() > 256 * 1024)
      return reply(error("invalid", "text must be a string of at most 256 KiB"));
    request["text"] = r["text"];
  } else if (action != "read") {
    return reply(error("invalid", "action must be read or write"));
  }
  agent::call(loop_, platform_, w->id, request, true, 1 << 20, 15 * 1000, [reply](agent::Result res) {
    if (!res.error.empty()) return reply(error("guest", res.error));
    if (res.timedOut || res.code.value_or(1) != 0) return reply(error("guest", "the clipboard transfer did not complete"));
    json::Value out;
    try { out = json::parse(res.out); } catch (...) {}
    reply(out.obj() ? *out.obj() : json::Object{});
  });
}

// One of the catalog's wallpapers on the workspace's running desktop (DESIGN.md §8), by the agent.
void Workspaces::wallpaper(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (!hasDesktop(w->spec)) return reply(error("unsupported", "workspace has no desktop", {{"field", "workspace"}}));
  const json::Value& design = r["design"];
  if (!std::holds_alternative<std::string>(design.v) || catalog_.catalog["wallpapers"][design.str()].isNull())
    return reply(error("unsupported", "unknown wallpaper", {{"field", "design"}}));
  if (!w->vmUp || w->state != "running" || !w->operation.empty())
    return reply(error("conflict", "workspace has no running desktop", {{"reason", "state"}}));
  agent::call(loop_, platform_, w->id, {{"op", "wallpaper"}, {"design", design}}, true, 64 * 1024, 60 * 1000, [reply](agent::Result res) {
    if (!res.error.empty()) return reply(error("guest", res.error));
    if (res.timedOut || res.code.value_or(1) != 0) return reply(error("guest", "the wallpaper was not applied"));
    reply(json::Object{});
  });
}

// The key chords the agent applied with the keyboard layouts, for computer use.
void Workspaces::keepKeys(const std::string& id, const json::Value& applied) {
  auto it = workspaces_.find(id);
  if (it != workspaces_.end() && applied.obj() && applied["keys"].obj()) it->second.keys = *applied["keys"].obj();
}

// One computer-use batch on the workspace's running desktop (DESIGN.md §22), from the host through
// the VM process's display service; only typing reaches into the guest.
void Workspaces::computer(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (!hasDesktop(w->spec)) return reply(error("unsupported", "workspace has no desktop", {{"field", "workspace"}}));
  if (!w->vmUp || w->state != "running" || !w->operation.empty())
    return reply(error("conflict", "workspace has no running desktop", {{"reason", "state"}}));
  if (w->computing)
    return reply({{"batch", json::Object{{"ok", false}, {"completed_actions", 0}, {"error", "Workspace desktop is busy; observe again before retrying"}}}});
  computer::Batch batch;
  batch.socket = platform_.displaySocket(w->id);
  batch.request = r;
  batch.keys = w->keys;
  const json::Value& desktop = catalog_.distributions.count(w->spec.at("distribution").str())
                                   ? catalog_.distributions.at(w->spec.at("distribution").str())["desktops"][w->spec.at("desktop").str()]
                                   : json::Value();
  if (!desktop["paste"].isNull())
    for (auto& k : desktop["paste"].arr()) batch.paste.push_back(k.str());
  const std::string id = w->id;
  // From the worker thread: an agent request made on the loop, waited for.
  batch.guest = [this, id](json::Object op) {
    auto result = std::make_shared<std::promise<std::string>>();
    auto done = result->get_future();
    loop_.post([this, id, op = std::move(op), result] {
      agent::call(loop_, platform_, id, op, true, 64 * 1024, 30 * 1000, [result](agent::Result res) {
        result->set_value(!res.error.empty() ? res.error
                          : res.timedOut || res.code.value_or(1) != 0 ? "the guest could not complete the request"
                                                                      : std::string());
      });
    });
    return done.wait_for(std::chrono::seconds(40)) == std::future_status::ready ? done.get() : std::string("the guest did not answer");
  };
  w->computing = std::make_shared<std::atomic<bool>>(false);
  batch.cancel = w->computing.get();
  std::thread([this, id, reply, batch = std::move(batch), cancel = w->computing] {
    json::Object result = computer::run(batch);
    loop_.post([this, id, reply, cancel, result = std::move(result)] {
      auto it = workspaces_.find(id);
      if (it != workspaces_.end() && it->second.computing == cancel) it->second.computing.reset();
      // Nested: a top-level "error" would make the whole request a failure, hiding what completed.
      reply({{"batch", result}});
    });
  }).detach();
}

// Stops the workspace's running computer-use batch after its current action.
void Workspaces::computerCancel(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (w->computing) w->computing->store(true);
  reply({{"cancelled", bool(w->computing)}});
}

void Workspaces::exited(const std::string& id, const std::string& err, bool reboot) {
  auto it = workspaces_.find(id);
  if (it == workspaces_.end()) return;
  Workspace& w = it->second;
  w.vmUp = false;
  closeForwards(w);
  if (std::exchange(w.quietExit, false)) return;  // our own power-off: state is already set
  const bool stopping = w.operation == "stopping";
  // The guest restarted itself: the same disks boot again, and the agent reports ready again. A
  // guest that keeps restarting before it ever runs (a crash at boot) fails instead of looping.
  constexpr unsigned kMaxReboots = 5;
  if (reboot && !stopping && ++w.reboots <= kMaxReboots) {
    w.operation = "preparing";
    w.error.clear();
    ++w.epoch;
    changed(w);
    return launch(id, w.epoch, "");
  }
  const bool rebootLoop = reboot && !stopping;
  w.reboots = 0;
  w.state = (err.empty() && !rebootLoop) || stopping ? "stopped" : "failed";
  w.error = stopping ? std::string() : rebootLoop ? "The system restarted " + std::to_string(kMaxReboots) + " times without starting" : printable(err, 1024);
  w.operation.clear();
  ++w.epoch;
  loop_.cancel(w.stopTimer);
  auto replies = std::move(w.stopReplies);
  w.stopReplies.clear();
  changed(w);
  for (auto& r : replies) r({{"record", record(w)}});
}

void Workspaces::stop(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  if (w->operation == "stopping") return w->stopReplies.push_back(std::move(reply));
  if (w->operation.empty() && w->state != "running" && !w->vmUp) return reply({{"record", record(*w)}});
  const std::string id = w->id;
  const bool booting = w->operation == "preparing" && w->state != "running";
  w->operation = "stopping";
  ++w->epoch;  // cancels a pending boot
  abandon();
  w->stopReplies.push_back(std::move(reply));
  changed(*w);
  auto force = [this, id] {
    platform_.powerOff(id, [this, id](std::string err) {
      auto it = workspaces_.find(id);
      if (it == workspaces_.end() || it->second.operation != "stopping") return;
      if (err.empty()) return exited(id, "", false);  // no VM was running yet: nothing will report an exit
      it->second.operation.clear();
      auto replies = std::move(it->second.stopReplies);
      it->second.stopReplies.clear();
      changed(it->second);
      for (auto& rep : replies) rep(error("internal", err));
    });
  };
  if (booting) return force();
  // Ask the guest to shut down; power off regardless after the grace period.
  w->stopTimer = loop_.after(shutdownMs(catalog_, w->spec), force);
  agent::call(loop_, platform_, id, {{"op", "shutdown"}}, false, 4096, 5000, [force](agent::Result res) {
    if (!res.error.empty()) force();  // unreachable agent: no reason to wait
  });
}

void Workspaces::remove(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  const std::string id = w->id;
  w->operation = "stopping";
  ++w->epoch;  // cancels any job
  abandon();
  platform_.powerOff(id, [this, id, reply](std::string err) {
    auto it = workspaces_.find(id);
    if (it == workspaces_.end()) return reply({});
    if (!err.empty()) {
      fail(id, err);
      return reply(error("internal", err));
    }
    Catalog next = workspaces_;
    next.erase(id);
    save(next);
    for (auto& rep : it->second.stopReplies) rep(error("not_found", "workspace was deleted"));
    workspaces_ = std::move(next);
    std::error_code ec;
    fs::remove_all(dir(id), ec);
    prune();
    if (onChange) onChange(id, nullptr);
    reply({});
  });
}

void Workspaces::exec(const json::Value& r, Reply reply) {
  Workspace* w = find(r, reply);
  if (!w) return;
  // Also while provisioning once the VM is up: what a long first start is doing must be visible.
  const bool provisioning = w->operation == "preparing" && w->vmUp;
  if ((w->state != "running" || !w->operation.empty()) && !provisioning)
    return reply(error("conflict", "workspace is not running", {{"reason", "stopped"}}));
  json::Array argv;
  for (auto& a : r["argv"].arr()) argv.push_back(a.str());
  if (argv.empty() || !integer(r["timeout"], 1, 1800)) return reply(error("invalid", "exec needs argv and timeout 1-1800"));
  json::Object run{{"op", "run"}, {"argv", argv}, {"cwd", cwd(*w)}, {"stderr", "split"}};
  agent::call(loop_, platform_, w->id, run, true, kExecCap, uint64_t(r["timeout"].num()) * 1000, [reply](agent::Result res) {
    if (!res.error.empty()) return reply(error("guest", res.error));
    bool truncated = res.truncated;
    std::string out = capEncoded(utf8(res.out), kExecCap, truncated);
    std::string err = capEncoded(utf8(res.err), kExecCap, truncated);
    reply({{"code", res.code ? json::Value(*res.code) : json::Value()},
           {"stdout", std::move(out)},
           {"stderr", std::move(err)},
           {"truncated", truncated},
           {"timed_out", res.timedOut}});
  });
}

void Workspaces::process(const json::Value& r, std::function<void(int, std::string, json::Object)> done) {
  try {
    auto it = workspaces_.find(r["workspace"].str());
    if (it == workspaces_.end()) return done(-1, "", error("not_found", "no such workspace"));
    if (it->second.state != "running" || !it->second.operation.empty())
      return done(-1, "", error("conflict", "workspace is not running", {{"reason", "stopped"}}));
    json::Array argv;
    for (auto& a : r["argv"].arr()) argv.push_back(a.str());
    if (argv.empty()) return done(-1, "", error("invalid", "process needs argv"));
    const bool tty = !r["tty"].isNull() && r["tty"].boolean();
    json::Object run{{"op", "run"}, {"argv", argv}, {"cwd", cwd(it->second)}, {"tty", tty}, {"stderr", "merge"}};
    if (tty) {
      run["cols"] = r["cols"].isNull() ? 80.0 : r["cols"].num();
      run["rows"] = r["rows"].isNull() ? 24.0 : r["rows"].num();
    }
    agent::open(loop_, platform_, it->first, run, 60000, [done](int fd, std::string pending, std::string err) {
      if (fd < 0) return done(-1, "", error("guest", err));
      done(fd, std::move(pending), {});
    });
  } catch (const json::TypeError& e) {
    done(-1, "", error("invalid", e.what()));
  }
}

void Workspaces::drain(std::function<void()> done) {
  draining_ = true;
  for (auto& [_, c] : converting_) *c.cancel = true;  // downloads stop
  for (auto& [vm, _] : installs_) platform_.powerOff(vm, [](std::string) {});  // installs start over next time
  auto shutdown = [this, done] { shutdownGuests([this, done] { platform_.drain(done); }); };
  if (converting_.empty()) return shutdown();
  drained_ = shutdown;  // conversions finish first: their threads post back to this loop
}

// Running guests shut down as a stop would (services stop, file systems unmount cleanly: no journal
// to replay and nothing half-written at the next boot); `done` runs when they are all off, or after
// the grace period. A guest still booting has no agent to ask: platform_.drain powers it off.
void Workspaces::shutdownGuests(std::function<void()> done) {
  auto up = std::make_shared<std::vector<std::string>>();
  for (auto& [id, w] : workspaces_)
    if (w.vmUp && w.state == "running") up->push_back(id);
  if (up->empty()) return done();
  uint64_t grace = 0;
  for (const auto& id : *up) {
    Workspace& w = workspaces_.at(id);
    w.operation = "stopping";
    grace = std::max(grace, shutdownMs(catalog_, w.spec));
    agent::call(loop_, platform_, id, {{"op", "shutdown"}}, false, 4096, 5000, [](agent::Result) {});
  }
  const uint64_t deadline = now() + grace;
  auto wait = std::make_shared<std::function<void()>>();
  *wait = [this, up, deadline, done, wait] {
    const bool anyUp = std::any_of(up->begin(), up->end(), [this](const std::string& id) {
      auto it = workspaces_.find(id);
      return it != workspaces_.end() && it->second.vmUp;
    });
    if (!anyUp || now() > deadline) {
      auto finish = done;
      *wait = nullptr;  // breaks the cycle
      return finish();
    }
    loop_.after(200, *wait);
  };
  loop_.after(200, *wait);
}

}  // namespace sentineld
