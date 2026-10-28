#include "catalog.hpp"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <set>
#include <sstream>
#include <stdexcept>

namespace sentineld::catalog {

namespace {

namespace fs = std::filesystem;

std::string quote(const std::string& s) {
  std::string q = "'";
  for (char c : s) q += c == '\'' ? std::string("'\\''") : std::string(1, c);
  return q + "'";
}

json::Value readJson(const fs::path& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return {};
  std::stringstream text;
  text << in.rdbuf();
  return json::parse(text.str());
}

bool flag(const json::Value& v) { return !v.isNull() && v.boolean(); }

// A path inside an unpacked release; it may hold `*` for a folder named after the release.
bool archivePath(const std::string& s) {
  return !s.empty() && s.find("..") == std::string::npos &&
         std::all_of(s.begin(), s.end(), [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || std::strchr("._-/*+", c); });
}

// A distribution's command (an argument list) as a shell line, `{name}` replaced.
std::string line(const json::Value& command, const std::string& name = "") {
  std::string s;
  for (auto& a : command.arr()) {
    std::string arg = a.str();
    for (size_t at; (at = arg.find("{name}")) != std::string::npos;) arg.replace(at, 6, name);
    s += (s.empty() ? "" : " ") + quote(arg);
  }
  return s;
}

// A distribution's list of commands (`service`, `restart`): one shell line each.
std::string lines(const json::Value& commands, const std::string& name = "") {
  std::string s;
  if (!commands.isNull())
    for (auto& c : commands.arr()) s += line(c, name) + "\n";
  return s;
}

// `version` holds: the installed tool already is the pinned one.
std::string installed(const json::Value& version) {
  return "{ " + version["command"].str() + "; } 2>&1 | grep -F -- " + quote(version["contains"].str()) + " >/dev/null";
}

// Sets $url (and $sum, with `sum`) from a single value, or one per `uname -m`.
std::string pick(const json::Value& url, const json::Value* sum) {
  std::string s;
  if (auto* byArch = url.obj()) {
    s += "case \"$(uname -m)\" in\n";
    for (auto& [arch, address] : *byArch)
      s += arch + ") url=" + quote(address.str()) + (sum ? " sum=" + quote((*sum)[arch].str()) : "") + " ;;\n";
    return s + "*) echo 'Unsupported architecture' >&2; exit 1 ;;\nesac\n";
  }
  // A single release is built for ARM64, the architecture workspaces run on.
  s += "[ \"$(uname -m)\" = aarch64 ] || { echo 'This release is published for ARM64 only' >&2; exit 1; }\n";
  return s + "url=" + quote(url.str()) + (sum ? " sum=" + quote(sum->str()) : "") + "\n";
}

const char* const kCurl = "curl --fail --location --retry 3 --connect-timeout 15 --max-time 900 \"$url\"";

// Downloads `url` to $work/download and checks its sha256/512.
std::string download(const json::Value& url, const json::Value& sum) {
  const size_t length = (sum.obj() ? sum.obj()->begin()->second : sum).str().size();
  return pick(url, &sum) + kCurl + " -o \"$work/download\"\n" +
         "printf '%s  %s\\n' \"$sum\" \"$work/download\" | " + (length == 128 ? "sha512sum" : "sha256sum") + " -c - >/dev/null\n";
}

std::string release(const json::Value& r) {
  std::string unpack = r["unpack"].str(), s = "work=$(mktemp -d)\n" + download(r["url"], r["sha256"]);
  if (unpack == "tar" || unpack == "zip") {
    std::string into = r["into"].isNull() ? "\"$work/x\"" : quote(r["into"].str());
    s += "mkdir -p " + into + "\n";
    if (unpack == "zip") {
      s += "unzip -q \"$work/download\" -d " + into + "\n";
    } else {
      s += "tar -xf \"$work/download\" -C " + into;
      if (!r["strip"].isNull()) s += " --strip-components=" + std::to_string(int(r["strip"].num()));
      s += "\n";
    }
  } else if (unpack != "binary") {
    throw std::invalid_argument("unknown unpack: " + unpack);
  }
  if (auto* files = r["install"].obj())
    for (auto& [path, destination] : *files) {
      if (!archivePath(path)) throw std::invalid_argument("unsafe release path: " + path);
      std::string source = unpack == "binary" ? "\"$work/download\"" : "\"$work/x\"/" + path;
      s += "install -D -m 755 " + source + " " + quote(destination.str()) + "\n";
    }
  s += "rm -rf \"$work\"\n";
  return "if ! " + installed(r["version"]) + "; then\n" + s + "fi\n";
}

// The commands of one tool's (or desktop's) block on a distribution, from its actions, in order.
std::string script(const json::Value& b, const json::Value& distribution) {
  std::string s = "set -eu\n";
  if (!b["packages"].isNull() && !b["packages"].arr().empty()) {
    s += "/run/sentinel/sentineld-agent install";
    for (auto& p : b["packages"].arr()) s += " " + quote(p.str());
    s += "\n";
  }
  if (auto* pkg = b["package_url"].obj()) {
    // A vendor's own package file, installed by the package manager.
    const std::string file = pkg->at("url").obj() ? pkg->at("url").obj()->begin()->second.str() : pkg->at("url").str();
    const std::string name = "package" + file.substr(std::min(file.size(), file.rfind('.')));
    s += "if ! " + installed(pkg->at("version")) + "; then\nwork=$(mktemp -d)\n" + pick(pkg->at("url"), nullptr);
    s += std::string(kCurl) + " -o \"$work/" + name + "\"\n";
    s += "/run/sentinel/sentineld-agent install \"$work/" + name + "\"\nrm -rf \"$work\"\nfi\n";
  }
  if (!b["release"].isNull()) s += release(b["release"]);
  if (auto* npm = b["npm"].obj())
    s += "if ! " + installed(npm->at("version")) + "; then\nnpm install --global --prefix /usr/local --no-audit --no-fund " +
         quote(npm->at("package").str()) + "\nfi\n";
  if (auto* links = b["links"].obj())
    for (auto& [link, target] : *links)
      s += "mkdir -p \"$(dirname " + quote(link) + ")\" && ln -sf " + quote(target.str()) + " " + quote(link) + "\n";
  if (auto* files = b["files"].obj())
    for (auto& [path, content] : *files)
      s += "mkdir -p \"$(dirname " + quote(path) + ")\" && printf '%s' " + quote(content.str()) + " > " + quote(path) + "\n";
  if (!b["service"].isNull()) s += lines(distribution["service"], b["service"].str());
  return s;
}

const json::Value& distributionOf(const Data& data, const std::string& id) {
  static const json::Value none;
  auto it = data.distributions.find(id);
  return it == data.distributions.end() ? none : it->second;
}

}  // namespace

bool empty(const std::string& script) { return script == "set -eu\n"; }

Data load(const std::string& share, const std::vector<std::string>& distributions) {
  Data data;
  data.catalog = readJson(fs::path(share) / "catalog.json");
  for (auto& dir : distributions) {
    std::error_code ec;
    for (auto& entry : fs::directory_iterator(dir, ec)) {
      if (!entry.is_directory()) continue;
      const std::string id = entry.path().filename().string(), folder = entry.path().string();
      if (id.empty() || id[0] == '.') continue;
      if (auto other = data.folders.find(id); other != data.folders.end()) {
        data.problems[id] = "also in " + other->second + ": " + folder;
        data.distributions.erase(id);
        continue;
      }
      data.folders[id] = folder;
      if (data.problems.count(id)) continue;  // a third folder with the same id
      try {
        json::Value d = readJson(entry.path() / "distribution.json");
        if (d.isNull()) data.problems[id] = folder + "/distribution.json: missing";
        else if (!d.obj()) data.problems[id] = folder + "/distribution.json: not a JSON object";
        else data.distributions[id] = std::move(d);
      } catch (const std::exception& e) {
        data.problems[id] = folder + "/distribution.json: " + e.what();
      }
    }
  }
  // A desktop or tool a distribution offers that the share's catalog does not name is the
  // distribution's own: listed and selectable like the others, with the name (and, for a tool, the
  // detail and category) its block gives. The first distribution to offer an id names it.
  if (!data.catalog.obj()) data.catalog = json::Object{};
  auto& catalog = std::get<json::Object>(data.catalog.v);
  for (auto& [id, d] : data.distributions)
    for (const std::string section : {"desktops", "tools"}) {
      auto* own = d[section].obj();
      if (!own) continue;
      if (!catalog[section].obj()) catalog[section] = json::Object{};
      auto& known = std::get<json::Object>(catalog[section].v);
      for (auto& [name, block] : *own) {
        if (known.count(name)) continue;
        json::Object entry{{"name", std::holds_alternative<std::string>(block["name"].v) ? block["name"] : json::Value(name)}};
        if (section == "tools") {
          entry["detail"] = std::holds_alternative<std::string>(block["detail"].v) ? block["detail"] : json::Value("");
          entry["category"] = std::holds_alternative<std::string>(block["category"].v) ? block["category"] : json::Value("Other");
        }
        known[name] = std::move(entry);
      }
    }
  return data;
}

json::Object list(const Data& data) {
  auto availability = [&](const char* section, const std::string& id) {
    json::Array on;
    for (auto& [name, d] : data.distributions)
      if (!d[section][id].isNull()) on.push_back(name);
    return on;
  };
  json::Array distributions, desktops, tools;
  // What a client offers and names: the folder's name and detail, the guest features it offers,
  // what agents working in its shell are told about it, and the least resources it runs well with.
  for (auto& [id, d] : data.distributions)
    distributions.push_back(json::Object{{"id", id},
                                         {"name", d["name"].isNull() ? json::Value(id) : d["name"]},
                                         {"detail", d["detail"]},
                                         {"features", d["features"].isNull() ? json::Value(json::Array{}) : d["features"]},
                                         {"guide", d["guide"].isNull() ? json::Value("") : d["guide"]},
                                         {"minimum", d["minimum"].isNull() ? json::Value(json::Object{}) : d["minimum"]}});
  if (auto* all = data.catalog["desktops"].obj())
    for (auto& [id, desktop] : *all)
      desktops.push_back(json::Object{{"id", id}, {"name", desktop["name"]}, {"distributions", availability("desktops", id)}});
  if (auto* all = data.catalog["tools"].obj())
    for (auto& [id, tool] : *all) {
      if (flag(tool["hidden"])) continue;
      json::Object entry{{"id", id}, {"name", tool["name"]}, {"detail", tool["detail"]}, {"category", tool["category"]},
                         {"distributions", availability("tools", id)}};
      if (!tool["requires"].isNull()) entry["requires"] = tool["requires"];
      tools.push_back(std::move(entry));
    }
  json::Array wallpapers;
  if (auto* all = data.catalog["wallpapers"].obj())
    for (auto& [id, wallpaper] : *all) wallpapers.push_back(json::Object{{"id", id}, {"name", wallpaper["name"]}});
  json::Array problems;
  for (auto& [id, why] : data.problems) problems.push_back(json::Object{{"id", id}, {"problem", why}});
  const json::Value& stacks = data.catalog["stacks"];
  return {{"distributions", std::move(distributions)}, {"desktops", std::move(desktops)}, {"tools", std::move(tools)},
          {"stacks", stacks.isNull() ? json::Value(json::Array{}) : stacks}, {"wallpapers", std::move(wallpapers)},
          {"problems", std::move(problems)}};
}

Problem invalid(const Data& data, const std::string& distribution, const std::string& desktop, const json::Value& tools) {
  const json::Value& d = distributionOf(data, distribution);
  if (auto p = data.problems.find(distribution); d.isNull() && p != data.problems.end()) return {"spec.distribution", p->second};
  if (d.isNull()) return {"spec.distribution", "unknown distribution"};
  if (desktop != "none") {
    if (data.catalog["desktops"][desktop].isNull()) return {"spec.desktop", "unknown desktop"};
    if (d["desktops"][desktop].isNull()) return {"spec.desktop", desktop + " is not available on " + distribution};
  }
  if (tools.isNull()) return {};
  if (!std::holds_alternative<json::Array>(tools.v)) return {"spec.tools", "tools must be a list of tool ids"};
  std::set<std::string> seen;
  for (auto& t : tools.arr()) {
    if (!std::holds_alternative<std::string>(t.v)) return {"spec.tools", "tools must be a list of tool ids"};
    const std::string& id = t.str();
    const json::Value& tool = data.catalog["tools"][id];
    if (tool.isNull() || flag(tool["hidden"])) return {"spec.tools", "unknown tool: " + id};
    if (d["tools"][id].isNull()) return {"spec.tools", id + " is not available on " + distribution};
    if (!seen.insert(id).second) return {"spec.tools", "tools lists " + id + " twice"};
  }
  return {};
}

std::vector<Install> plan(const Data& data, const json::Value& tools, const std::string& distribution) {
  const json::Value& d = distributionOf(data, distribution);
  std::vector<Install> out;
  std::set<std::string> done, visiting;
  std::function<void(const std::string&)> visit = [&](const std::string& id) {
    if (done.count(id)) return;
    if (!visiting.insert(id).second) throw std::invalid_argument("catalog: " + id + " requires itself");
    const json::Value& tool = data.catalog["tools"][id];
    const json::Value& b = d["tools"][id];
    if (b.isNull()) throw std::invalid_argument("catalog: " + id + " is not available on " + distribution);
    for (const json::Value* needs : {&tool["requires"], &b["requires"]})
      if (!needs->isNull())
        for (auto& r : needs->arr()) visit(r.str());
    std::string commands = script(b, d);
    if (!empty(commands)) out.push_back({id, tool["name"].str(), std::move(commands), flag(tool["hidden"])});  // only what it requires
    done.insert(id);
  };
  // The hidden tools Sentinel needs, where the distribution has them (Windows' come with its userland).
  if (auto* all = data.catalog["tools"].obj())
    for (auto& [id, tool] : *all)
      if (flag(tool["hidden"]) && !d["tools"][id].isNull()) visit(id);
  if (!tools.isNull())
    for (auto& t : tools.arr()) visit(t.str());
  return out;
}

Install desktop(const Data& data, const std::string& desktop, const std::string& distribution) {
  const json::Value& d = distributionOf(data, distribution);
  return {desktop, data.catalog["desktops"][desktop]["name"].str(), script(d["desktops"][desktop], d)};
}

std::string desktopStart(const Data& data, const std::string& desktop, const std::string& distribution) {
  const json::Value& d = distributionOf(data, distribution);
  const json::Value& session = d["desktop"];
  const std::string display = d["desktops"][desktop]["display"].str();
  const std::string folder = "/run/sentinel/distribution";  // the VM's own distribution folder (DESIGN.md §17)
  const std::string unit = folder + "/" + session["session"][display].str();
  const std::string path = quote(session["install"].str());
  const std::string user = session["user"].str();
  // A value the shell writes into /etc/sentinel/desktop, `{uid}` the user's: plain characters only,
  // so the file reads the same as shell assignments and as KEY=VALUE lines.
  auto value = [](const std::string& v) {
    if (!std::all_of(v.begin(), v.end(), [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || std::strchr("_-.,:/=+{}", c); }))
      throw std::invalid_argument("unsafe desktop setting: " + v);
    std::string out = v;
    for (size_t at = 0; (at = out.find("{uid}", at)) != std::string::npos; at += 6) out.replace(at, 5, "${uid}");
    return "\"" + out + "\"";
  };
  auto named = [&](const json::Value& command, const std::string& group = "") {
    std::string s = line(command, user);
    for (size_t at; (at = s.find("{user}")) != std::string::npos;) s.replace(at, 6, user);
    for (size_t at; (at = s.find("{group}")) != std::string::npos;) s.replace(at, 7, group);
    return s;
  };
  std::string s = "set -eu\nuser=" + quote(user) + "\n";
  s += "id \"$user\" >/dev/null 2>&1 || " + named(session["create_user"]) + "\n";
  // Device access for the session: whichever of the groups the distribution has.
  if (!session["groups"].isNull())
    for (auto& g : session["groups"].arr()) s += named(session["join_group"], g.str()) + " 2>/dev/null || :\n";
  s += "uid=$(id -u \"$user\") home=$(eval echo \"~$user\")\n";
  if (!session["prepare"].isNull())
    for (auto& c : session["prepare"].arr()) s += line(c) + " 2>/dev/null || :\n";
  // The running desktop, for the distribution's session scripts and the agent: which desktop, on
  // which display system, started by which script, for which user, with which environment.
  s += "mkdir -p /etc/sentinel && {\n";
  s += "printf '%s\\n' SENTINEL_DESKTOP=" + value(desktop) + " SENTINEL_DISPLAY=" + value(display) +
       " SENTINEL_SESSION=" + value(folder + "/desktops/" + desktop + "/session") + " SENTINEL_USER=" + value(user) + "\n";
  if (auto* env = session["environment"][display].obj())
    for (auto& [key, v] : *env) {
      if (key.empty() || !std::all_of(key.begin(), key.end(), [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '_'; }))
        throw std::invalid_argument("unsafe desktop environment name: " + key);
      s += "printf '%s\\n' " + key + "=" + value(v.str()) + "\n";
    }
  s += "} > /etc/sentinel/desktop\n";
  // The distribution's default look for the desktop (desktops/<desktop>/defaults, a home folder tree),
  // once and before its first session, so the session never writes over it. Only what it holds is
  // handed to the user: a project may be mounted under the home.
  s += "defaults=" + quote(folder + "/desktops/" + desktop + "/defaults") + "\n";
  s += "if [ -d \"$defaults\" ] && [ ! -e \"$home/.config/sentinel/defaults-seeded\" ]; then\n"
       "cp -R \"$defaults/.\" \"$home/\" && mkdir -p \"$home/.config/sentinel\" && : > \"$home/.config/sentinel/defaults-seeded\"\n"
       "{ (cd \"$defaults\" && find . -mindepth 1); echo ./.config/sentinel; echo ./.config/sentinel/defaults-seeded; } |\n"
       "while read -r p; do chown -h \"$user:$(id -g \"$user\")\" \"$home/$p\"; done\nfi\n";
  s += "mkdir -p \"$(dirname " + path + ")\" && rm -f " + path + " && cp " + quote(unit) + " " + path + "\n";
  s += lines(session["restart"]);
  // Up once the display server answers (a first GNOME or Plasma start takes a while).
  s += "socket=" + value(session["socket"][display].str()) + "\n";
  s += "for _ in $(seq 600); do [ -S \"$socket\" ] && exit 0; sleep 0.1; done\n";
  s += "echo 'the desktop session did not start:' >&2\n";
  if (!session["failure"].isNull()) s += line(session["failure"]) + " >&2 || :\n";  // why, as far as the distribution can tell
  return s + "exit 1\n";
}

}  // namespace sentineld::catalog
