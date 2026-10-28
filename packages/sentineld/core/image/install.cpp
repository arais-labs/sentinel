#include "install.hpp"

#include <algorithm>
#include <archive.h>
#include <archive_entry.h>
#include <cerrno>
#include <climits>
#include <cstdlib>
#include <fcntl.h>
#include <filesystem>
#include <fnmatch.h>
#include <map>
#include <set>
#include <memory>
#include <spawn.h>
#include <sys/stat.h>
#include <stdexcept>
#include <sys/clonefile.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include "../util/sha256.hpp"
#include "../util/text.hpp"
#include "media.hpp"
#include "download.hpp"

namespace fs = std::filesystem;

namespace sentineld::image {
namespace {

std::string real(const std::string& path) {
  char buf[PATH_MAX];
  if (!::realpath(path.c_str(), buf)) throw std::runtime_error("cannot resolve " + path);
  return buf;
}

std::string readAll(const fs::path& path) {
  std::string out;
  const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
  if (fd < 0) throw std::runtime_error("cannot read " + path.string());
  char buf[65536];
  ssize_t n;
  while ((n = ::read(fd, buf, sizeof buf)) > 0) out.append(buf, size_t(n));
  ::close(fd);
  return out;
}

// Replaces each {name} in `text` with its value; an unknown name is an error in the recipe.
std::string expand(const std::string& text, const std::map<std::string, std::string>& vars) {
  std::string out;
  for (size_t i = 0; i < text.size();) {
    if (text[i] != '{') {
      out += text[i++];
      continue;
    }
    const size_t end = text.find('}', i);
    if (end == std::string::npos) throw std::runtime_error("install: unclosed { in " + text);
    auto v = vars.find(text.substr(i + 1, end - i - 1));
    if (v == vars.end()) throw std::runtime_error("install: unknown " + text.substr(i, end - i + 1));
    out += v->second;
    i = end + 1;
  }
  return out;
}

// A path a step may write: inside the work folder.
fs::path inside(const std::string& path, const std::string& work) {
  const fs::path p = fs::path(path).lexically_normal();
  const auto rel = p.lexically_relative(work);
  if (rel.empty() || *rel.begin() == "..") throw std::runtime_error("install: " + path + " is outside the work folder");
  return p;
}

// Runs one of our tools in a sandbox: it reads the system, its inputs and the
// work folder, and writes only the work folder.
void run(const json::Array& command, const json::Value& environment, const std::map<std::string, std::string>& vars,
         const std::string& tools, const std::vector<std::string>& readable, const std::string& work) {
  if (command.empty()) throw std::runtime_error("install: empty run step");
  const std::string name = command[0].str();
  if (name.empty() || name.find('/') != std::string::npos || name[0] == '.') throw std::runtime_error("install: invalid tool " + name);
  const std::string tool = real(tools + "/" + name);
  std::string profile =
      "(version 1)(deny default)(allow sysctl-read)(allow file-read-metadata)"
      "(allow process-exec (literal \"" + tool + "\"))"
      "(allow file-read* (subpath \"/usr/lib\") (subpath \"/System\") (subpath \"/private/var/db/dyld\")"
      " (subpath \"/usr/share/i18n\") (literal \"/\") (literal \"" + tool + "\") (literal \"/dev/null\") (literal \"/dev/urandom\")";
  for (const std::string& r : readable) profile += " (subpath \"" + real(r) + "\")";
  profile += ")(allow file-read* file-write* (subpath \"" + real(work) + "\"))";
  std::vector<std::string> argv = {"/usr/bin/sandbox-exec", "-p", profile, tool};
  for (size_t i = 1; i < command.size(); ++i) argv.push_back(expand(command[i].str(), vars));
  std::vector<char*> args;
  for (auto& a : argv) args.push_back(a.data());
  args.push_back(nullptr);
  std::vector<std::string> settings = {"PATH=/usr/bin:/bin"};
  if (auto* e = environment.obj())
    for (auto& [name, value] : *e) settings.push_back(name + "=" + expand(value.str(), vars));
  std::vector<char*> env;
  for (auto& setting : settings) env.push_back(setting.data());
  env.push_back(nullptr);
  int out[2];
  if (::pipe(out) != 0) throw std::runtime_error("pipe failed");
  posix_spawn_file_actions_t fa;
  posix_spawn_file_actions_init(&fa);
  posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
  posix_spawn_file_actions_adddup2(&fa, out[1], 1);
  posix_spawn_file_actions_adddup2(&fa, out[1], 2);
  pid_t pid = -1;
  const int rc = posix_spawn(&pid, args[0], &fa, nullptr, args.data(), env.data());
  posix_spawn_file_actions_destroy(&fa);
  ::close(out[1]);
  std::string output;
  if (rc == 0) {
    char buf[4096];
    ssize_t n;
    while ((n = ::read(out[0], buf, sizeof buf)) > 0 || (n < 0 && errno == EINTR))
      if (n > 0) {
        output.append(buf, size_t(n));
        if (output.size() > 2048) output.erase(0, output.size() - 2048);  // the end says why it failed
      }
  }
  ::close(out[0]);
  if (rc != 0) throw std::runtime_error("install: cannot start " + name);
  int status = 0;
  while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) throw std::runtime_error("install: " + name + " failed: " + output);
}

// Unpacks the archive members matching any of `include` (all when empty) under `to`.
void extract(const std::string& from, const fs::path& to, const json::Value& include) {
  std::vector<std::string> patterns;
  if (!include.isNull())
    for (auto& p : include.arr()) patterns.push_back(p.str());
  std::unique_ptr<archive, int (*)(archive*)> a(archive_read_new(), archive_read_free);
  archive_read_support_format_all(a.get());
  archive_read_support_filter_all(a.get());
  if (archive_read_open_filename(a.get(), from.c_str(), 1 << 20) != ARCHIVE_OK)
    throw std::runtime_error("install: cannot open " + from + ": " + archive_error_string(a.get()));
  archive_entry* e;
  int r;
  while ((r = archive_read_next_header(a.get(), &e)) == ARCHIVE_OK) {
    const fs::path name = fs::path(archive_entry_pathname(e)).lexically_normal();
    if (archive_entry_filetype(e) != AE_IFREG || archive_entry_hardlink(e)) continue;
    if (name.is_absolute() || name.empty() || *name.begin() == "..") throw std::runtime_error("install: unsafe archive member " + name.string());
    bool wanted = patterns.empty();
    for (auto& p : patterns) wanted = wanted || fnmatch(p.c_str(), name.c_str(), 0) == 0;
    if (!wanted) continue;
    const fs::path out = to / name;
    fs::create_directories(out.parent_path());
    const int fd = ::open(out.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) throw std::runtime_error("install: cannot write " + out.string());
    const int w = archive_read_data_into_fd(a.get(), fd);
    ::close(fd);
    if (w != ARCHIVE_OK) throw std::runtime_error("install: cannot extract " + name.string());
  }
  if (r != ARCHIVE_EOF) throw std::runtime_error("install: cannot read " + from + ": " + archive_error_string(a.get()));
}

// A file or a folder tree; files are clones where the file system allows (APFS), so large sources
// cost no space.
void copyTree(const fs::path& from, const fs::path& to) {
  fs::create_directories(to.parent_path());
  if (fs::is_directory(from)) {
    fs::copy(from, to, fs::copy_options::recursive | fs::copy_options::overwrite_existing);
    return;
  }
  fs::remove(to);
  if (::clonefile(from.c_str(), to.c_str(), 0) != 0) fs::copy_file(from, to);
}

// An archive member's or a folder entry's path as the packed archive names it: relative, normalized.
std::string memberName(const std::string& raw) {
  std::string name = fs::path(raw).lexically_normal().generic_string();
  while (name.rfind("./", 0) == 0) name.erase(0, 2);
  while (!name.empty() && name.back() == '/') name.pop_back();
  if (name == ".") name.clear();
  if (fs::path(name).is_absolute() || name.rfind("..", 0) == 0) throw std::runtime_error("install: unsafe archive member " + raw);
  return name;
}

using Reader = std::unique_ptr<archive, int (*)(archive*)>;

Reader openArchive(const std::string& from) {
  Reader a(archive_read_new(), archive_read_free);
  archive_read_support_format_all(a.get());
  archive_read_support_filter_all(a.get());
  if (archive_read_open_filename(a.get(), from.c_str(), 1 << 20) != ARCHIVE_OK)
    throw std::runtime_error("install: cannot open " + from + ": " + archive_error_string(a.get()));
  return a;
}

// Packs `inputs` into one archive at `to`: "tar" (what mke2fs -d makes a file system of) or "newc"
// (the cpio an initramfs is). An input is an archive, whose members matching `include` keep their
// owners, modes and links, or a folder, whose entries are root's. A file in several inputs is the
// last one's, so a folder's own files replace an archive's.
void pack(const std::vector<std::pair<std::string, json::Value>>& inputs, const fs::path& to, const std::string& format) {
  auto wanted = [](const std::string& name, const json::Value& include) {
    if (include.isNull()) return true;
    for (auto& p : include.arr())
      if (fnmatch(p.str().c_str(), name.c_str(), 0) == 0) return true;
    return false;
  };
  // Which input each path comes from: a file the last one's, a folder the first one's, so that it
  // comes before what it holds (mke2fs creates entries in order).
  std::map<std::string, size_t> owner;
  auto claim = [&](const std::string& name, bool folder, size_t i) {
    if (!folder || !owner.count(name)) owner[name] = i;
  };
  for (size_t i = 0; i < inputs.size(); ++i) {
    const auto& [from, include] = inputs[i];
    if (fs::is_directory(from)) {
      for (auto& e : fs::recursive_directory_iterator(from))
        claim(memberName(e.path().lexically_relative(from).string()), e.symlink_status().type() == fs::file_type::directory, i);
      continue;
    }
    Reader a = openArchive(from);
    archive_entry* e;
    while (archive_read_next_header(a.get(), &e) == ARCHIVE_OK)
      if (const std::string name = memberName(archive_entry_pathname(e)); !name.empty() && wanted(name, include))
        claim(name, archive_entry_filetype(e) == AE_IFDIR, i);
  }
  fs::create_directories(to.parent_path());
  std::unique_ptr<archive, int (*)(archive*)> out(archive_write_new(), archive_write_free);
  if (format == "tar") archive_write_set_format_pax_restricted(out.get());
  else if (format == "newc") archive_write_set_format_cpio_newc(out.get());
  else throw std::runtime_error("install: unknown archive format " + format);
  if (archive_write_open_filename(out.get(), to.c_str()) != ARCHIVE_OK)
    throw std::runtime_error("install: cannot write " + to.string());
  // Every member's folders come first (neither mke2fs nor an initramfs creates them): a folder not
  // written yet is written as root's when its first member is.
  std::set<std::string> written;
  auto parents = [&](const std::string& name) {
    for (size_t at = name.find('/'); at != std::string::npos; at = name.find('/', at + 1)) {
      const std::string folder = name.substr(0, at);
      if (!written.insert(folder).second) continue;
      std::unique_ptr<archive_entry, void (*)(archive_entry*)> d(archive_entry_new(), archive_entry_free);
      archive_entry_set_pathname(d.get(), folder.c_str());
      archive_entry_set_mode(d.get(), AE_IFDIR | 0755);
      archive_entry_set_nlink(d.get(), 2);
      archive_entry_set_size(d.get(), 0);
      if (archive_write_header(out.get(), d.get()) != ARCHIVE_OK)
        throw std::runtime_error("install: cannot pack " + folder + ": " + archive_error_string(out.get()));
    }
  };
  auto write = [&](archive_entry* e, const std::string& name, auto&& data) {
    if (archive_entry_filetype(e) == AE_IFDIR && written.count(name)) return;
    parents(name);
    written.insert(name);
    archive_entry_set_pathname(e, name.c_str());
    if (archive_write_header(out.get(), e) != ARCHIVE_OK)
      throw std::runtime_error("install: cannot pack " + name + ": " + archive_error_string(out.get()));
    data();
  };
  char buf[65536];
  for (size_t i = 0; i < inputs.size(); ++i) {
    const auto& [from, include] = inputs[i];
    if (fs::is_directory(from)) {
      for (auto& d : fs::recursive_directory_iterator(from)) {
        const std::string name = memberName(d.path().lexically_relative(from).string());
        if (owner[name] != i) continue;
        struct stat st;
        if (::lstat(d.path().c_str(), &st) != 0) throw std::runtime_error("install: cannot read " + d.path().string());
        std::unique_ptr<archive_entry, void (*)(archive_entry*)> e(archive_entry_new(), archive_entry_free);
        archive_entry_copy_stat(e.get(), &st);
        archive_entry_set_uid(e.get(), 0), archive_entry_set_gid(e.get(), 0);
        archive_entry_set_uname(e.get(), "root"), archive_entry_set_gname(e.get(), "root");
        if (S_ISLNK(st.st_mode)) archive_entry_set_symlink(e.get(), fs::read_symlink(d.path()).c_str());
        write(e.get(), name, [&] {
          if (!S_ISREG(st.st_mode)) return;
          const int fd = ::open(d.path().c_str(), O_RDONLY | O_CLOEXEC);
          ssize_t n;
          while (fd >= 0 && (n = ::read(fd, buf, sizeof buf)) > 0) archive_write_data(out.get(), buf, size_t(n));
          if (fd >= 0) ::close(fd);
        });
      }
      continue;
    }
    Reader a = openArchive(from);
    archive_entry* e;
    while (archive_read_next_header(a.get(), &e) == ARCHIVE_OK) {
      const std::string name = memberName(archive_entry_pathname(e));
      if (name.empty() || !wanted(name, include) || owner[name] != i) continue;
      if (const char* link = archive_entry_hardlink(e)) archive_entry_set_hardlink(e, memberName(link).c_str());
      write(e, name, [&] {
        la_ssize_t n;
        while ((n = archive_read_data(a.get(), buf, sizeof buf)) > 0) archive_write_data(out.get(), buf, size_t(n));
      });
    }
  }
  if (archive_write_close(out.get()) != ARCHIVE_OK) throw std::runtime_error("install: cannot write " + to.string());
}

// A GUID the recipe determines: the same recipe writes the same disk.
std::string guidOf(const std::string& key, const std::string& name) {
  Sha256 h;
  const std::string seed = key + ":" + name;
  h.update(seed.data(), seed.size());
  std::string x = h.digest().substr(7, 32);
  x[12] = '4';                                              // version 4
  x[16] = "89ab"[std::stoi(x.substr(16, 1), nullptr, 16) & 3];  // RFC 4122 variant
  return x.substr(0, 8) + "-" + x.substr(8, 4) + "-" + x.substr(12, 4) + "-" + x.substr(16, 4) + "-" + x.substr(20, 12);
}

}  // namespace

std::string installKey(const json::Value& install, const std::string& folder) {
  Sha256 h;
  const std::string recipe = json::dump(install);
  h.update(recipe.data(), recipe.size());
  const fs::path dir = fs::path(folder) / "install";
  std::vector<fs::path> files;
  if (fs::is_directory(dir))
    for (auto& e : fs::recursive_directory_iterator(dir))
      if (e.is_regular_file()) files.push_back(e.path());
  std::sort(files.begin(), files.end());
  for (auto& f : files) {
    const std::string name = f.lexically_relative(dir).generic_string(), data = readAll(f);
    h.update(name.c_str(), name.size() + 1);
    const uint64_t size = data.size();
    h.update(&size, sizeof size);
    h.update(data.data(), data.size());
  }
  return h.digest().substr(7);
}

void prepareInstall(const json::Value& install, const std::string& folder, const std::string& sources,
                    const std::string& work, const std::string& tools, const std::atomic<bool>& cancel) {
  fs::create_directories(sources);
  fs::remove_all(work);
  fs::create_directories(work);
  std::map<std::string, std::string> vars{{"work", real(work)}, {"install", real(folder + "/install")}};
  std::vector<std::string> readable{vars["install"]};
  for (auto& [name, source] : *install["sources"].obj()) {
    const std::string digest = source["sha256"].str();
    if (!isDigest("sha256:" + digest)) throw std::runtime_error("install: source " + name + " needs a sha256");
    const std::string path = sources + "/" + digest;
    if (!fs::exists(path)) download(source["url"].str(), "sha256:" + digest, path, cancel);
    vars[name] = real(path);
    readable.push_back(vars[name]);
  }
  // The disk, its partitions placed and named by GUIDs the recipe determines.
  const std::string key = installKey(install, folder);
  const json::Value& disk = install["disk"];
  std::map<std::string, DiskPartition> partitions;
  std::vector<DiskPartition> layout;
  for (auto& p : disk["partitions"].arr())
    layout.push_back({p["name"].str(), p["type"].str(), p["mib"].isNull() ? 0 : uint64_t(p["mib"].num()), guidOf(key, p["name"].str())});
  vars["disk"] = vars["work"] + "/target.raw";
  vars["disk.guid"] = guidOf(key, "disk");
  createDisk(vars["disk"], uint64_t(disk["gib"].num()) << 30, vars["disk.guid"], layout);
  // A root disk: a file system on the whole disk, no partitions, so workspaces grow it from the host
  // before the guest boots (a file system inside a partition grows neither there nor safely online).
  if (const json::Value& root = install["root"]; !root.isNull()) {
    vars["root"] = vars["work"] + "/root.raw";
    const int fd = ::open(vars["root"].c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0 || ::ftruncate(fd, off_t(uint64_t(root["gib"].num()) << 30)) != 0) throw std::runtime_error("install: cannot create the root disk");
    ::close(fd);
  }
  for (auto& p : layout) {
    partitions[p.name] = p;
    vars[p.name + ".guid"] = p.guid;
    vars[p.name + ".start"] = std::to_string(p.start);
    vars[p.name + ".sectors"] = std::to_string(p.sectors);
    vars[p.name + ".offset"] = std::to_string(p.start * 512);
    vars[p.name + ".size"] = std::to_string(p.sectors * 512);
    vars[p.name + ".kib"] = std::to_string(p.sectors / 2);
  }
  // What a step may read: the folder's install/ files, the sources and the work folder.
  auto source = [&](const std::string& text) {
    const std::string from = expand(text, vars);
    bool allowed = from.rfind(vars["work"] + "/", 0) == 0;
    for (auto& r : readable) allowed = allowed || from == r || from.rfind(r + "/", 0) == 0;
    if (!allowed || from.find("/../") != std::string::npos) throw std::runtime_error("install: cannot read " + from);
    return from;
  };
  for (auto& step : install["steps"].arr()) {
    if (cancel) throw std::runtime_error("install cancelled");
    if (!step["run"].isNull()) {
      run(step["run"].arr(), step["env"], vars, tools, readable, work);
    } else if (!step["extract"].isNull()) {
      extract(expand(step["extract"].str(), vars), inside(expand(step["to"].str(), vars), vars["work"]), step["include"]);
    } else if (!step["copy"].isNull()) {
      copyTree(source(step["copy"].str()), inside(expand(step["to"].str(), vars), vars["work"]));
    } else if (!step["archive"].isNull()) {
      std::vector<std::pair<std::string, json::Value>> inputs;
      for (auto& input : step["archive"].arr()) inputs.push_back({source(input["from"].str()), input["include"]});
      pack(inputs, inside(expand(step["to"].str(), vars), vars["work"]), step["format"].str());
    } else if (!step["fat32"].isNull()) {
      auto p = partitions.find(step["fat32"].str());
      if (p == partitions.end()) throw std::runtime_error("install: no partition " + step["fat32"].str());
      formatFat32(vars["disk"], p->second, step["label"].str(), inside(expand(step["from"].str(), vars), vars["work"]).string());
    } else {
      throw std::runtime_error("install: unknown step " + json::dump(step));
    }
  }
}

json::Object browseArchive(const std::string& file, const std::string& match, const std::vector<std::string>& paths,
                           size_t limit, uint64_t offset, uint64_t length) {
  const bool range = offset || length;
  if (range && paths.size() != 1) throw std::runtime_error("offset and length read one member: give one path");
  std::unique_ptr<archive, int (*)(archive*)> a(archive_read_new(), archive_read_free);
  archive_read_support_format_all(a.get());
  archive_read_support_filter_all(a.get());
  if (archive_read_open_filename(a.get(), file.c_str(), 1 << 20) != ARCHIVE_OK)
    throw std::runtime_error(std::string("not an archive sentineld reads: ") + archive_error_string(a.get()));
  const std::set<std::string> wanted(paths.begin(), paths.end());
  json::Object found;                   // paths: path -> {bytes, text | binary}
  json::Array members;                  // match: {path, bytes, type}
  std::map<std::string, double> where;  // match: count per top folder, for a refusal
  size_t size = 0, matched = 0;
  archive_entry* e;
  int r;
  while ((r = archive_read_next_header(a.get(), &e)) == ARCHIVE_OK) {
    std::string name = archive_entry_pathname(e);
    if (name.rfind("./", 0) == 0) name.erase(0, 2);
    if (name.empty()) continue;
    const double bytes = double(archive_entry_size(e));
    if (!paths.empty()) {
      if (!wanted.count(name)) continue;
      json::Object member{{"bytes", bytes}};
      // A range: the member's bytes [offset, offset + length), length defaulting to what fits.
      const uint64_t from = std::min<uint64_t>(offset, uint64_t(bytes));
      const uint64_t take = range ? std::min<uint64_t>(length ? length : limit, uint64_t(bytes) - from) : uint64_t(bytes);
      if (range) member["offset"] = double(from);
      if (archive_entry_filetype(e) == AE_IFREG && size + take <= limit) {
        std::string data(size_t(from + take), '\0');
        if (archive_read_data(a.get(), data.data(), data.size()) != la_ssize_t(data.size()))
          throw std::runtime_error("cannot read " + name);
        data.erase(0, size_t(from));
        // A range may cut a character at either end: those bytes read as U+FFFD, the rest as text.
        const bool text = data.find('\0') == std::string::npos && (range || utf8(data) == data);
        member[text ? "text" : "binary"] = text ? json::Value(utf8(data)) : json::Value(true);
      } else if (archive_entry_filetype(e) != AE_IFREG) {
        member["type"] = archive_entry_filetype(e) == AE_IFDIR ? "dir" : "link";
      }
      size += size_t(take);
      found[name] = std::move(member);
      continue;
    }
    if (fnmatch(match.c_str(), name.c_str(), 0) != 0) continue;
    ++matched;
    where[name.substr(0, name.find('/'))] += 1;
    const unsigned type = archive_entry_filetype(e);
    json::Object member{{"path", name}, {"bytes", bytes}, {"type", type == AE_IFREG ? "file" : type == AE_IFDIR ? "dir" : "link"}};
    if (const char* target = archive_entry_symlink(e)) member["target"] = target;
    size += json::dump(member).size() + 1;
    if (size <= limit) members.push_back(std::move(member));
  }
  if (r != ARCHIVE_EOF) throw std::runtime_error(std::string("cannot read the archive: ") + archive_error_string(a.get()));
  if (!paths.empty()) {
    std::string missing;
    for (auto& p : paths)
      if (!found.count(p)) missing += (missing.empty() ? "" : ", ") + p;
    if (!missing.empty()) throw std::runtime_error("not in the archive: " + missing);
    if (size > limit) {
      json::Object sizes;
      for (auto& [p, m] : found) sizes[p] = m["bytes"];
      throw TooLarge("these members take " + std::to_string(size) + " bytes, over the " + std::to_string(limit) +
                     " one reply holds; read fewer at a time. Sizes: " + json::dump(sizes));
    }
    return {{"files", std::move(found)}};
  }
  if (size > limit) {
    json::Object counts;
    for (auto& [folder, n] : where) counts[folder] = n;
    throw TooLarge(std::to_string(matched) + " members match, over what one reply holds; narrow match. Matches per top folder: " +
                   json::dump(counts));
  }
  return {{"members", std::move(members)}, {"matched", double(matched)}};
}

}  // namespace sentineld::image
