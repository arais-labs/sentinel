#include "help.hpp"

#include <cstring>

namespace sentineld {
namespace {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wc23-extensions"
constexpr char kHelp[] = {
#embed "sentineld.txt"
    , 0};
constexpr char kDistributionsHelp[] = {
#embed "distributions.txt"
    , 0};
#pragma clang diagnostic pop
}  // namespace

const char* helpText(const char* topic) {
  if (!*topic) return kHelp;
  if (!std::strcmp(topic, "distributions")) return kDistributionsHelp;
  return nullptr;
}
}  // namespace sentineld
