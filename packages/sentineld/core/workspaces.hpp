// Workspace catalog and lifecycle ops (DESIGN.md §5). Runs on the Loop thread.
#pragma once
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "catalog.hpp"
#include "media.hpp"
#include "platform.hpp"
#include "util/json.hpp"

namespace sentineld {

// A reply is either a result object or {"error":{code,message,details?}}; the server adds "id".
using Reply = std::function<void(json::Object)>;
json::Object error(const std::string& code, const std::string& message, json::Object details = {});

class Workspaces {
 public:
  // `media`: the viewer endpoint for desktops.
  // `keepImages`: keep every installed base and layer instead of pruning those no workspace uses (the
  // end-to-end harness, whose runs delete their workspaces, reuses installs across runs).
  // `distributions`: the folders of distribution folders (catalog.hpp), re-read at every catalog,
  // configure and start.
  Workspaces(Loop& loop, Platform& platform, std::string state, std::string cache, std::string mke2fs, std::string share,
             std::vector<std::string> distributions, Media* media, bool keepImages = false);

  void handle(const std::string& op, const json::Value& request, Reply reply);
  // `process`: opens the interactive run; on success the caller relays fd (see agent::relay).
  void process(const json::Value& request, std::function<void(int fd, std::string pending, json::Object error)> done);
  const std::string& workerId() const { return workerId_; }
  json::Object records() const;
  void drain(std::function<void()> done);  // stops installs, shuts guests down, powers off the rest

  std::function<void(const std::string& id, json::Value record)> onChange;

 private:
  struct Workspace {
    std::string id;
    json::Object spec;
    uint64_t revision = 0;
    std::string state = "stopped";  // stopped | running | failed
    std::string operation;          // "" | preparing | stopping
    std::string error;
    uint64_t epoch = 0;             // bumped on every boot and cancel; stale callbacks compare it
    std::vector<Reply> stopReplies;
    uint64_t stopTimer = 0;
    std::map<int, int> forwards;  // guest port -> listening socket, closed when the VM stops
    bool vmUp = false;            // the platform has a VM for this workspace
    unsigned reboots = 0;         // guest restarts since it last reached running
    bool quietExit = false;       // the next exit is our own power-off (erase, failed start)
    std::string base;             // base disk (images/<base>.{boot,root}.raw) the disks were cloned from
    std::string desktopReady;     // the desktop last provisioned on this disk ("" for a new disk)
    json::Object keyboard;        // the viewer's keyboard layouts (the keyboard op), applied after every desktop start
    json::Object keys;            // their key chords, as the agent last applied them (computer use)
    std::shared_ptr<std::atomic<bool>> computing;  // the running computer-use batch's cancel flag; null when none
    std::string progress;         // what provisioning is doing, for people (record.progress)
    std::string build;            // the work folder of the install or layer it last waited for (the log op)
  };
  using Converted = std::function<void(std::string base, std::string error)>;
  // A running install (image/install.hpp): its install VM, by the VM's id.
  struct Install {
    std::string distribution, base, work;
    VmConfig vm;
    std::shared_ptr<std::atomic<bool>> cancel;
    uint64_t timer = 0;
    bool timedOut = false;
    std::vector<json::Array> layer;  // a layer's steps (argv), run in order (layerDisk); none for a distribution's install
    std::string failure;  // why a layer failed before its VM stopped
    std::string panic;    // the guest kernel's panic line, when it panicked (watchInstall)
  };
  struct Conversion {
    struct Waiter { std::string id; uint64_t epoch; Converted done; };
    std::vector<Waiter> waiters;
    std::shared_ptr<std::atomic<bool>> cancel = std::make_shared<std::atomic<bool>>(false);
  };
  using Catalog = std::map<std::string, Workspace>;

  void configure(const json::Value& r, Reply reply);
  void start(const json::Value& r, Reply reply);
  void stop(const json::Value& r, Reply reply);
  void remove(const json::Value& r, Reply reply);
  void exec(const json::Value& r, Reply reply);
  void forward(const json::Value& r, Reply reply);
  std::string forwardPath(const std::string& id, int port) const;
  std::string packageCache(const std::string& distribution) const;
  void trimPackageCaches() const;
  void display(const json::Value& r, Reply reply);
  void keyboard(const json::Value& r, Reply reply);
  void clipboard(const json::Value& r, Reply reply);
  void wallpaper(const json::Value& r, Reply reply);
  void computer(const json::Value& r, Reply reply);
  void computerCancel(const json::Value& r, Reply reply);
  // Distributions written through sentineld: <state>/distributions/<id>, read like the others.
  void distribution(const json::Value& r, Reply reply);
  void distributionRead(const json::Value& r, Reply reply);
  void distributionDelete(const json::Value& r, Reply reply);
  json::Object distributionState(const std::string& id) const;
  std::string ownDistributions() const { return state_ + "/distributions"; }
  // A file a distribution may pin as an install source, downloaded into the cache (source op).
  void source(const json::Value& r, Reply reply);
  void watchInstall(const std::string& vm);
  void browseSource(const std::string& url, const json::Value& r, Reply reply);
  void log(const json::Value& r, Reply reply);
  void usage(Reply reply);
  struct Fetch {
    std::shared_ptr<std::atomic<uint64_t>> got = std::make_shared<std::atomic<uint64_t>>(0);
    bool done = false;
    std::string sha256, error;
  };
  std::map<std::string, Fetch> fetches_;  // by URL
  std::shared_ptr<std::atomic<bool>> fetchCancel_ = std::make_shared<std::atomic<bool>>(false);
  void keepKeys(const std::string& id, const json::Value& applied);
  void acceptForwards(const std::string& id, int fd, int port);

  Workspace* find(const json::Value& r, Reply& reply);
  bool current(const std::string& id, uint64_t epoch) const;
  void boot(const std::string& id, bool erase);
  void launch(const std::string& id, uint64_t epoch, const std::string& base);
  void waitReady(const std::string& id, uint64_t epoch, uint64_t deadline);
  // One provisioning request to the guest agent, with what it means to a person and its time limit.
  struct Step {
    json::Object request;
    std::string message;
    uint64_t timeoutMs = 120000;
    bool optional = false;  // a failure is logged, not a failed start
  };
  void provision(const std::string& id, uint64_t epoch, std::vector<Step> steps, size_t next);
  void closeForwards(Workspace& w);
  json::Value cwd(const Workspace& w) const;
  void installDisk(const std::string& distribution, const std::string& id, uint64_t epoch, Converted done);
  void installExited(const std::string& vm, const std::string& error, bool reboot);
  void layerDisk(const std::string& base, const std::string& distribution, std::vector<std::string> scripts,
                 const std::string& id, uint64_t epoch, Converted done);
  void startLayer(const std::string& base, const std::string& distribution, std::vector<std::string> scripts,
                  const std::string& name);
  void layerReady(const std::string& vm, uint64_t deadline);
  void layerRun(const std::string& vm, size_t next);
  void converted(const std::string& distribution, const std::string& base, const std::string& error);
  void abandon();
  void prune();
  void fail(const std::string& id, const std::string& message);
  void exited(const std::string& id, const std::string& error, bool reboot);
  void changed(const Workspace& w);
  void save(const Catalog& catalog) const;
  json::Value record(const Workspace& w) const;
  std::string dir(const std::string& id) const { return state_ + "/workspaces/" + id; }
  std::string consoleLog(const std::string& id) const;
  void shutdownGuests(std::function<void()> done);

  Loop& loop_;
  Platform& platform_;
  Media* media_;
  std::string state_, cache_, mke2fs_, share_, workerId_;  // cache_: base disks, install sources, packages
  bool keepImages_ = false;
  std::vector<std::string> distributionDirs_;
  void reloadCatalog();
  // A file of the share as the distribution's guests see it: "distribution/…" is in its own folder.
  std::string shareFile(const std::string& distribution, const std::string& path) const;
  catalog::Data catalog_;  // the share's catalog and distributions (catalog.hpp)
  Catalog workspaces_;
  std::map<std::string, Conversion> converting_;  // installs, by distribution
  std::map<std::string, Install> installs_;       // running install VMs, by VM id
  // Layers waiting for their distribution's running one: they share its package cache, which two
  // package managers at once would race on.
  std::map<std::string, std::vector<std::function<void()>>> layerQueue_;
  std::function<void()> drained_;  // set while draining: runs once conversions finish
  bool draining_ = false;
  std::string lastBase_;     // the newest base disk, kept until the next install
};

}  // namespace sentineld
