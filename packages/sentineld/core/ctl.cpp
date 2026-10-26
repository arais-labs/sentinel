// sentineld ctl: one request to a running sentineld (DESIGN.md §4), from a shell.
#include "ctl.hpp"

#include <cstdio>
#include <cstring>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "util/json.hpp"

namespace sentineld {

namespace {

constexpr int kApi = 1;

// The operations ctl sends, for `ctl --help`, `ctl help OP` and to refuse a mistyped one before it is
// sent. Every op the daemon handles (server.cpp, Workspaces::handle) has its entry here, except
// `process`, whose binary stream a shell cannot drive.
struct Op {
  const char* name;
  const char* fields;
  const char* what;
  const char* example;
};
constexpr Op kOps[] = {
    {"catalog", "",
     "What a spec may select, read again from the distribution folders: distributions (id, name, detail,\n"
     "features, minimum), problems (folders that could not be used and why), desktops and tools (with the\n"
     "distributions offering them), stacks, wallpapers.",
     "catalog"},
    {"distribution", "distribution, files",
     "Writes a distribution (sentineld help distributions) into sentineld's own folder, creating it, or\n"
     "changes one written so. files: {\"<path in the folder>\": {\"text\": ..., \"executable\": bool} to write,\n"
     "or null to remove}; other files stay. Replies its folder, files [{path, bytes}] and problem (null when usable).",
     "distribution '{\"distribution\":\"d1\",\"files\":{\"distribution.json\":{\"text\":\"{...}\"}}}'"},
    {"distribution_read", "distribution, [path]",
     "Any distribution's folder, files, problem and whether it is writable; with path, that file's text.",
     "distribution_read '{\"distribution\":\"d1\",\"path\":\"distribution.json\"}'"},
    {"distribution_delete", "distribution",
     "Removes a distribution sentineld wrote, unless a workspace uses it (the reply names them).",
     "distribution_delete '{\"distribution\":\"d1\"}'"},
    {"source", "url, [match | paths, offset, length], [limit]",
     "Downloads a file a distribution's install recipe may pin (install.sources) into sentineld's cache.\n"
     "Replies state downloading (bytes so far) at once; ask again later: state ready with sha256 and\n"
     "bytes, or the download's error. An install pinning that sha256 uses the copy.\n"
     "Once ready, inside an archive: match (a glob, * for all) lists members {path, bytes, type};\n"
     "paths reads members whole, each {bytes, text} or {bytes, binary: true}; with one path, offset\n"
     "and length read a byte range of it. A reply over limit characters (default 50000) is refused\n"
     "with what matched and where, to narrow the request.",
     "source '{\"url\":\"https://example.org/rootfs.tar.gz\",\"match\":\"boot/*\"}'"},
    {"list", "[watch]",
     "Every workspace's record: spec, revision, state (running|stopped|failed), operation\n"
     "(preparing|stopping|null), progress, error, screen. With watch:true, then prints one\n"
     "{\"event\":\"workspace\",\"id\",\"record\"} line per change until sentineld exits (record null: deleted).",
     "list '{\"watch\":true}'"},
    {"configure", "workspace, revision, spec",
     "Creates a workspace (revision 0) or edits it (its current revision). spec: name, distribution,\n"
     "desktop (\"none\" or a desktop the distribution offers), tools (tool ids), resources {cpus 1-32,\n"
     "memory_gib 1-64, disk_gib 8-1024}, project (an absolute host folder shared into the VM, optional).\n"
     "Replies the record, with its new revision.",
     "configure '{\"workspace\":\"w1\",\"revision\":0,\"spec\":{\"name\":\"w1\",\"distribution\":\"<id>\",\"desktop\":\"none\",\n"
     "      \"resources\":{\"cpus\":2,\"memory_gib\":4,\"disk_gib\":16}}}'"},
    {"start", "workspace, revision, [erase], [spec]",
     "Boots the workspace and provisions it, in the background: the reply comes at once with operation\n"
     "\"preparing\"; follow it with list. The first start of a distribution installs its base disk.\n"
     "erase:true reinstalls from scratch (its disk is deleted), optionally with a new spec.",
     "start '{\"workspace\":\"w1\",\"revision\":1}'"},
    {"stop", "workspace",
     "Cancels provisioning, asks the guest to power off, then powers it off regardless. Waits for it.",
     "stop '{\"workspace\":\"w1\"}'"},
    {"delete", "workspace",
     "Powers the VM off and removes the workspace and its disks (never its project folder).",
     "delete '{\"workspace\":\"w1\"}'"},
    {"exec", "workspace, argv, timeout",
     "Runs a command in the guest as root and waits: code (null when timed out), stdout, stderr, truncated,\n"
     "timed_out. timeout in seconds. Works while running, and while preparing once the VM is up.",
     "exec '{\"workspace\":\"w1\",\"argv\":[\"sh\",\"-c\",\"uname -a\"],\"timeout\":30}'"},
    {"forward", "workspace, port",
     "Makes a guest TCP port reachable as a Unix socket on this Mac (the reply's socket), until the VM stops.",
     "forward '{\"workspace\":\"w1\",\"port\":8080}'"},
    {"computer", "workspace, [actions, viewport]",
     "Uses the running desktop like a person: runs up to 16 actions, then replies batch {ok,\n"
     "completed_actions, error, viewport, cursor, screenshot (a data URL)}. Without actions: a screenshot.\n"
     "viewport is the {width, height} of the last screenshot. Actions ({\"type\": ...}):\n"
     "  move|click|double_click {x, y, [button left|middle|right]}   drag {path: [{x, y}, ...], [button]}\n"
     "  scroll {x, y, direction up|down|left|right, ticks 1-20}       keypress {keys: X11 key names, 1-5}\n"
     "  type {text}   wait {milliseconds 0-2000}",
     "computer '{\"workspace\":\"w1\",\"actions\":[{\"type\":\"click\",\"x\":10,\"y\":10}],\"viewport\":{\"width\":1920,\"height\":1200}}'"},
    {"computer_cancel", "workspace", "Stops the workspace's running computer batch after its current action.",
     "computer_cancel '{\"workspace\":\"w1\"}'"},
    {"clipboard", "workspace, action, [text]", "Reads (action read: replies text) or writes (action write, text) the desktop's clipboard.",
     "clipboard '{\"workspace\":\"w1\",\"action\":\"write\",\"text\":\"hello\"}'"},
    {"keyboard", "workspace, layout, [variant, model, options]",
     "Sets the desktop's keyboard layouts (XKB names, comma lists), now and at every desktop start.",
     "keyboard '{\"workspace\":\"w1\",\"layout\":\"us,fr\"}'"},
    {"wallpaper", "workspace, design", "Sets one of the catalog's wallpapers on the running desktop.",
     "wallpaper '{\"workspace\":\"w1\",\"design\":\"<id>\"}'"},
    {"log", "workspace, [lines]",
     "What the guest printed: console (its newest boot's console log), build (the console of the\n"
     "install or layer that made its disks: running, or the last failed one) and failure (the\n"
     "workspace's error). lines: the last lines of each (default 200, at most 5000); null when\n"
     "there is none.",
     "log '{\"workspace\":\"w1\",\"lines\":400}'"},
    {"usage", "",
     "Disk space sentineld uses, in bytes: workspaces {id: its disks and logs, by what is its own (its\n"
     "disks share their base's): what deleting it frees}, shared {systems (installed bases and layers),\n"
     "packages, sources, installs, distributions}, total, and the volume's free and capacity.",
     "usage"},
    {"display", "workspace",
     "A ticket for a viewer of the workspace's screen: port, cert_sha256, ticket, expires (DESIGN.md §6).\n"
     "To open the screen in a window: sentineld view --state DIR WORKSPACE.",
     "display '{\"workspace\":\"w1\"}'"},
    {"help", "[topic]",
     "The text `sentineld help [topic]` prints, for clients without the binary: the overview, or topic\n"
     "\"distributions\" (how to write one).",
     "help '{\"topic\":\"distributions\"}'"},
    {"status", "", "sentineld's API version and worker_id (ctl checks the version itself first).", "status"},
    {"shutdown", "", "Powers off every VM and exits sentineld.", "shutdown"},
};

const Op* find(const std::string& name) {
  for (const Op& op : kOps)
    if (name == op.name) return &op;
  return nullptr;
}

void describe(const Op& op) {
  std::printf("%s%s%s\n", op.name, *op.fields ? "  " : "", op.fields);
  for (const char *line = op.what, *end; *line; line = *end ? end + 1 : end) {
    end = std::strchr(line, '\n');
    if (!end) end = line + std::strlen(line);
    std::printf("    %.*s\n", int(end - line), line);
  }
  std::printf("    e.g. sentineld ctl --state DIR %s\n", op.example);
}

int help(const char* argv0, const char* name) {
  if (name) {
    const Op* op = find(name);
    if (!op) {
      std::fprintf(stderr, "sentineld ctl: no operation %s (sentineld ctl --help lists them)\n", name);
      return 2;
    }
    describe(*op);
    return 0;
  }
  std::printf("usage: %s ctl (--state DIR | --socket PATH) OP [FIELDS]\n"
              "Sends one request to the sentineld whose state folder is DIR (or whose socket is PATH).\n"
              "  FIELDS: the request's fields as one JSON object (default {}); a field in [brackets] is optional.\n"
              "  Prints the reply as one JSON line; exits 1 when it is an error ({\"error\":{code, message, details}}).\n"
              "  sentineld rejects unknown fields and values (unsupported, with details.field).\n"
              "  An edit needs the workspace's current revision (from list); a stale one is a conflict.\n\n"
              "Operations:\n\n",
              argv0);
  for (const Op& op : kOps) {
    describe(op);
    std::printf("\n");
  }
  return 0;
}

int usage(const char* argv0) {
  std::fprintf(stderr, "usage: %s ctl (--state DIR | --socket PATH) OP [FIELDS]\n"
                       "       %s ctl --help | help OP\n", argv0, argv0);
  return 2;
}

}  // namespace

int ctl(int argc, char** argv) {
  std::string socket, op, fields = "{}";
  if (argc >= 3 && (!std::strcmp(argv[2], "--help") || !std::strcmp(argv[2], "-h") || !std::strcmp(argv[2], "help")))
    return argc <= 4 ? help(argv[0], argc == 4 ? argv[3] : nullptr) : usage(argv[0]);
  int i = 2;
  for (; i < argc && argv[i][0] == '-'; ++i) {
    const std::string a = argv[i];
    if (a == "--state" && i + 1 < argc) socket = std::string(argv[++i]) + "/sentineld.sock";
    else if (a == "--socket" && i + 1 < argc) socket = argv[++i];
    else return usage(argv[0]);
  }
  if (i < argc) op = argv[i++];
  if (i < argc) fields = argv[i++];
  if (socket.empty() || op.empty() || i != argc) return usage(argv[0]);
  if (!find(op)) {
    std::fprintf(stderr, "sentineld ctl: no operation %s (sentineld ctl --help lists them)\n", op.c_str());
    return 2;
  }

  json::Object request;
  try {
    const json::Value parsed = json::parse(fields);
    if (!parsed.obj()) throw std::runtime_error("not an object");
    request = *parsed.obj();
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld ctl: FIELDS must be a JSON object: %s\n", e.what());
    return 2;
  }
  request["op"] = op;
  request["id"] = 2;

  // A long state path does not fit sockaddr_un: connect from the socket's folder.
  if (const size_t slash = socket.rfind('/'); slash != std::string::npos && socket.size() >= sizeof(sockaddr_un::sun_path)) {
    if (::chdir(socket.substr(0, slash).c_str()) != 0) {
      std::fprintf(stderr, "sentineld ctl: cannot reach %s\n", socket.c_str());
      return 1;
    }
    socket = socket.substr(slash + 1);
  }
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  if (socket.size() >= sizeof addr.sun_path) return usage(argv[0]);
  std::memcpy(addr.sun_path, socket.c_str(), socket.size() + 1);
  const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0 || ::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
    std::fprintf(stderr, "sentineld ctl: no sentineld at %s: %s\n", socket.c_str(), std::strerror(errno));
    return 1;
  }
  const std::string out = json::dump(json::Object{{"id", 1}, {"op", "status"}, {"api", kApi}}) + "\n" + json::dump(request) + "\n";
  for (size_t sent = 0; sent < out.size();) {
    const ssize_t n = ::write(fd, out.data() + sent, out.size() - sent);
    if (n <= 0) {
      std::fprintf(stderr, "sentineld ctl: write failed\n");
      return 1;
    }
    sent += size_t(n);
  }

  // Replies come as lines; the handshake's (id 1) first, then this request's (id 2), then, for a
  // watch, events until sentineld closes the connection.
  const bool watch = op == "list" && request.count("watch") && !request.at("watch").isNull() && request.at("watch").boolean();
  std::string buffer;
  int status = 1;
  bool replied = false;
  char chunk[65536];
  for (;;) {
    size_t eol;
    while ((eol = buffer.find('\n')) != std::string::npos) {
      const std::string line = buffer.substr(0, eol);
      buffer.erase(0, eol + 1);
      json::Value reply;
      try {
        reply = json::parse(line);
      } catch (const std::exception&) {
        continue;
      }
      // A reply's id is the request's number; an event's id is its workspace's name.
      const double* number = std::get_if<double>(&reply["id"].v);
      const int id = number ? int(*number) : 0;
      const bool error = !reply["error"].isNull();
      if (id == 1) {
        if (error) {
          std::printf("%s\n", line.c_str());
          return 1;
        }
        continue;
      }
      if (id == 2) {
        std::printf("%s\n", line.c_str());
        std::fflush(stdout);
        status = error ? 1 : 0;
        replied = true;
        if (!watch || error) return status;
        continue;
      }
      if (replied) {  // a watch's event
        std::printf("%s\n", line.c_str());
        std::fflush(stdout);
      }
    }
    const ssize_t n = ::read(fd, chunk, sizeof chunk);
    if (n <= 0) break;
    buffer.append(chunk, size_t(n));
  }
  if (!replied) std::fprintf(stderr, "sentineld ctl: sentineld closed the connection\n");
  return status;
}

}  // namespace sentineld
