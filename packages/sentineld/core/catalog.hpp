// What workspaces can run and install (DESIGN.md §8), as data: the share's catalog.json (the tools,
// stacks and desktops, the same everywhere) and the distribution folders, `<id>/distribution.json`
// each (how that distribution does each of them), in the folders sentineld is given
// (--distributions). sentineld turns the data into the commands it runs in the guest; nothing here
// knows a distribution by name.
#pragma once
#include <map>
#include <string>
#include <vector>

#include "util/json.hpp"

namespace sentineld::catalog {

struct Data {
  json::Value catalog;                               // catalog.json
  std::map<std::string, json::Value> distributions;  // by id: each distribution.json
  std::map<std::string, std::string> folders;        // by id: the distribution's folder
  std::map<std::string, std::string> problems;       // by id: why a folder offers no distribution
};

// The share's catalog and every distribution folder in `distributions` (folders of folders). A folder
// whose distribution.json is missing or unreadable, or whose id another folder also has, is a
// problem instead of a distribution.
Data load(const std::string& share, const std::vector<std::string>& distributions);

// For the `catalog` op: the distributions ({id, name, detail, os, installed}), the desktops and the
// tools a client may select (not the hidden ones), each with the distributions it is available on,
// and the stacks.
json::Object list(const Data& data);

// Why `distribution`, `desktop` (a desktop id, or "none") or `tools` (a spec's list) cannot be used;
// empty when they can. The field names the spec field at fault.
struct Problem {
  std::string field, message;
};
Problem invalid(const Data& data, const std::string& distribution, const std::string& desktop, const json::Value& tools);

// One installation: a shell script run in the guest, and its name for progress.
struct Install {
  std::string id;
  std::string name;
  std::string script;
  bool hidden = false;  // a base tool every workspace gets, not one the spec selected
};

// Whether an action script does nothing (the actions were empty).
bool empty(const std::string& script);

// The hidden base tools the distribution has, then `tools`, each after what it requires; every
// tool once.
std::vector<Install> plan(const Data& data, const json::Value& tools, const std::string& distribution);

// The desktop's packages, and the script that (re)starts its session and waits for its display.
Install desktop(const Data& data, const std::string& desktop, const std::string& distribution);
std::string desktopStart(const Data& data, const std::string& desktop, const std::string& distribution);

}  // namespace sentineld::catalog
