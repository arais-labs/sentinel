#include "sha256.hpp"

#include <CommonCrypto/CommonDigest.h>

namespace sentineld {

static_assert(sizeof(CC_SHA256_CTX) <= 112, "Sha256 state buffer too small");

static CC_SHA256_CTX* ctx(unsigned char* s) { return reinterpret_cast<CC_SHA256_CTX*>(s); }

Sha256::Sha256() { CC_SHA256_Init(ctx(state_)); }

void Sha256::update(const void* data, size_t n) { CC_SHA256_Update(ctx(state_), data, CC_LONG(n)); }

std::string Sha256::digest() {
  unsigned char d[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256_Final(d, ctx(state_));
  static const char* k = "0123456789abcdef";
  std::string s = "sha256:";
  for (unsigned char c : d) { s += k[c >> 4]; s += k[c & 15]; }
  return s;
}

}  // namespace sentineld
