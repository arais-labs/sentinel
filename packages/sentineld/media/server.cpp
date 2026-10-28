#include "server.hpp"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <ngtcp2/ngtcp2_crypto_ossl.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <openssl/x509v3.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <cstdio>
#include <algorithm>
#include <cstring>
#include <deque>
#include <set>
#include <stdexcept>
#include <vector>

namespace sentineld::media {

namespace {

constexpr size_t kCidLen = 18;
constexpr uint64_t kWtBidi = 0x41, kWtUni = 0x54;  // WebTransport stream signals
constexpr size_t kMaxPending = 3;                  // unsent frames per viewer before it skips to a key
constexpr size_t kMaxControl = 64 * 1024;          // buffered bytes on any client stream

ngtcp2_tstamp now() { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }

void fail(const char* what) {
  char err[256];
  ERR_error_string_n(ERR_get_error(), err, sizeof err);
  throw std::runtime_error(std::string(what) + ": " + err);
}

void varint(std::vector<uint8_t>& out, uint64_t v) {
  int n = v < 64 ? 0 : v < 16384 ? 1 : v < (1u << 30) ? 2 : 3;
  for (int i = (1 << n) - 1; i >= 0; --i) out.push_back(uint8_t(v >> (8 * i)) | (i == (1 << n) - 1 ? n << 6 : 0));
}

// Reads a QUIC varint at `at`; false if the buffer ends first.
bool readVarint(std::string_view s, size_t& at, uint64_t& v) {
  if (at >= s.size()) return false;
  size_t n = size_t(1) << (uint8_t(s[at]) >> 6);
  if (at + n > s.size()) return false;
  v = uint8_t(s[at]) & 0x3f;
  for (size_t i = 1; i < n; ++i) v = v << 8 | uint8_t(s[at + i]);
  at += n;
  return true;
}

void frame(std::vector<uint8_t>& out, uint64_t type, const std::vector<uint8_t>& payload) {
  varint(out, type);
  varint(out, payload.size());
  out.insert(out.end(), payload.begin(), payload.end());
}

int selectAlpn(SSL*, const unsigned char** out, unsigned char* outLen, const unsigned char* in, unsigned inLen,
               void*) {
  for (unsigned i = 0; i < inLen; i += 1 + in[i])
    if (in[i] == 2 && i + 3 <= inLen && !memcmp(in + i + 1, "h3", 2)) {
      *out = in + i + 1;
      *outLen = 2;
      return SSL_TLSEXT_ERR_OK;
    }
  return SSL_TLSEXT_ERR_ALERT_FATAL;
}

}  // namespace

using Bytes = std::shared_ptr<const std::vector<uint8_t>>;

struct Conn {
  struct Out {
    int64_t stream;
    Bytes data;
    size_t offset;
    bool frame;
  };
  Server& server;
  uint64_t id;
  ngtcp2_conn* conn = nullptr;
  ngtcp2_crypto_ossl_ctx* crypto = nullptr;
  SSL* ssl = nullptr;
  ngtcp2_crypto_conn_ref ref{};
  sockaddr_storage remote{};
  socklen_t remoteLen = 0;
  sockaddr_in local{};
  std::set<std::string> cids;
  uint64_t timer = 0;
  std::map<int64_t, std::string> rx;       // bidi stream bytes not yet consumed
  std::map<int64_t, Bytes> held;           // sent bytes, kept until the stream closes
  std::deque<Out> sendq;
  int64_t session = -1, control = -1;      // the CONNECT stream and the viewer's control stream
  std::string workspace;                   // set once the ticket is admitted
  std::set<uint32_t> pressed;              // device << 16 | key code: released when the viewer leaves
  uint16_t refreshHz = 0;                  // the viewer's screen (kRefresh), 0 until it says
  bool waitingKey = true;

  Conn(Server& s, uint64_t i) : server(s), id(i) {}
  ~Conn() {
    if (conn) ngtcp2_conn_del(conn);
    if (ssl) SSL_free(ssl);
    if (crypto) ngtcp2_crypto_ossl_ctx_del(crypto);
  }

  void send(int64_t stream, std::vector<uint8_t> bytes, bool isFrame = false) {
    auto data = std::make_shared<const std::vector<uint8_t>>(std::move(bytes));
    held[stream] = data;
    sendq.push_back({stream, data, 0, isFrame});
  }
  // Connection ids are registered so incoming packets find their connection.
  int addCid(const ngtcp2_cid& cid) {
    std::string key(reinterpret_cast<const char*>(cid.data), cid.datalen);
    cids.insert(key);
    server.byCid_[key] = this;
    return 0;
  }
  int removeCid(const ngtcp2_cid& cid) {
    std::string key(reinterpret_cast<const char*>(cid.data), cid.datalen);
    cids.erase(key);
    server.byCid_.erase(key);
    return 0;
  }
  int resetToken(uint8_t* token, const ngtcp2_cid& cid) {
    return ngtcp2_crypto_generate_stateless_reset_token(token, server.secret_, sizeof server.secret_, &cid);
  }
  size_t pendingFrames() const {
    size_t n = 0;
    for (auto& o : sendq) n += o.frame;
    return n;
  }

  // Our HTTP/3 control stream: SETTINGS for extended CONNECT, datagrams and WebTransport.
  void openControl() {
    int64_t sid;
    if (ngtcp2_conn_open_uni_stream(conn, &sid, nullptr)) return;
    std::vector<uint8_t> settings;
    for (auto [k, v] : {std::pair<uint64_t, uint64_t>{0x08, 1},  // ENABLE_CONNECT_PROTOCOL
                        {0x33, 1},                               // H3_DATAGRAM
                        {0xffd277, 1},                           // H3_DATAGRAM (draft)
                        {0x2b603742, 1},                         // ENABLE_WEBTRANSPORT (draft 02)
                        {0xc671706a, 1}}) {                      // WEBTRANSPORT_MAX_SESSIONS (draft 07)
      varint(settings, k);
      varint(settings, v);
    }
    std::vector<uint8_t> out{0x00};
    frame(out, 0x04, settings);
    send(sid, std::move(out));
  }

  // A complete HEADERS frame on a request stream: the WebTransport CONNECT. Authentication is the
  // ticket on the control stream, so the request itself is not decoded.
  void accept(int64_t stream) {
    if (session >= 0) return void(ngtcp2_conn_shutdown_stream(conn, 0, stream, 0x10c));  // H3_REQUEST_REJECTED
    session = stream;
    static const char name[] = "sec-webtransport-http3-draft", value[] = "draft02";
    std::vector<uint8_t> fields{0x00, 0x00, 0xd9};  // QPACK prefix; static 25 = ":status: 200"
    fields.push_back(0x27);                         // literal name, length 28 (3-bit prefix 7 + 21)
    fields.push_back(sizeof name - 1 - 7);
    fields.insert(fields.end(), name, name + sizeof name - 1);
    fields.push_back(sizeof value - 1);
    fields.insert(fields.end(), value, value + sizeof value - 1);
    std::vector<uint8_t> out;
    frame(out, 0x01, fields);
    send(stream, std::move(out));
  }

  // False refuses the viewer.
  bool controlPacket(uint8_t type, std::span<const uint8_t> packet) {
    if (workspace.empty()) {
      if (type != kHello) return false;
      std::string_view ticket(reinterpret_cast<const char*>(packet.data()) + kHeader, packet.size() - kHeader);
      workspace = server.hooks_.admit(ticket);
      if (workspace.empty()) return false;
      server.watching(workspace);
      waitingKey = true;
      uint8_t request[kHeader] = {kKeyframe};
      server.hooks_.control(workspace, request);
      return true;
    }
    if (type == kInput) track(packet.subspan(kHeader));
    if (type == kInput || type == kKeyframe) server.hooks_.control(workspace, packet);
    if (type == kRefresh && packet.size() == kHeader + 2) {
      const uint16_t hz = uint16_t(packet[kHeader] << 8 | packet[kHeader + 1]);
      if (hz < 24 || hz > 240) return false;
      refreshHz = hz;
      server.refresh(workspace);
    }
    return true;
  }

  // Input payload: device u16be, count u16be, count x (type u16be, code u16be, value i32be).
  void track(std::span<const uint8_t> p) {
    if (p.size() < 4) return;
    uint32_t device = uint32_t(p[0] << 8 | p[1]);
    for (size_t at = 4; at + 8 <= p.size(); at += 8) {
      if ((p[at] << 8 | p[at + 1]) != 1) continue;  // EV_KEY
      uint32_t key = device << 16 | uint32_t(p[at + 2] << 8 | p[at + 3]);
      if (p[at + 4] | p[at + 5] | p[at + 6] | p[at + 7]) pressed.insert(key);
      else pressed.erase(key);
    }
  }

  void releasePressed() {
    for (uint32_t key : pressed) {
      uint8_t packet[kHeader + 12] = {kInput};
      packet[7] = 12;
      uint8_t* p = packet + kHeader;
      p[0] = uint8_t(key >> 24), p[1] = uint8_t(key >> 16), p[3] = 1;   // device, count 1
      p[5] = 1, p[6] = uint8_t(key >> 8), p[7] = uint8_t(key);          // EV_KEY code, value 0
      server.hooks_.control(workspace, packet);
    }
    pressed.clear();
  }

  // Bytes on a client-initiated bidirectional stream.
  int receive(int64_t stream, const uint8_t* data, size_t len) {
    if (stream % 4 != 0) return 0;  // client unidirectional: HTTP/3 control and QPACK, nothing we need
    auto& buf = rx[stream];
    buf.append(reinterpret_cast<const char*>(data), len);
    if (buf.size() > kMaxControl) return NGTCP2_ERR_CALLBACK_FAILURE;
    size_t at = 0;
    if (stream != control && stream != session) {
      uint64_t first, sid;
      size_t p = 0;
      if (!readVarint(buf, p, first)) return 0;
      if (first == kWtBidi) {
        if (!readVarint(buf, p, sid)) return 0;
        if (int64_t(sid) != session || control >= 0) return NGTCP2_ERR_CALLBACK_FAILURE;
        control = stream;
        at = p;
      }
    }
    if (stream == control) {
      while (buf.size() - at >= kHeader) {
        auto* h = reinterpret_cast<const uint8_t*>(buf.data() + at);
        uint32_t n = uint32_t(h[4]) << 24 | uint32_t(h[5]) << 16 | uint32_t(h[6]) << 8 | h[7];
        if (n > 1024) return NGTCP2_ERR_CALLBACK_FAILURE;
        if (buf.size() - at < kHeader + n) break;
        if (!controlPacket(h[0], {h, kHeader + n})) return NGTCP2_ERR_CALLBACK_FAILURE;
        at += kHeader + n;
      }
    } else {
      for (;;) {  // HTTP/3 frames on a request stream
        uint64_t type, n;
        size_t p = at;
        if (!readVarint(buf, p, type) || !readVarint(buf, p, n) || buf.size() - p < n) break;
        if (type == 0x01) accept(stream);
        at = p + n;
      }
    }
    buf.erase(0, at);
    return 0;
  }

  // Writes every packet the connection may send now.
  bool flush() {
    uint8_t buf[1452];
    ngtcp2_path_storage ps;
    ngtcp2_path_storage_zero(&ps);
    ngtcp2_pkt_info pi;
    auto ts = now();
    size_t i = 0;
    for (;;) {
      Out* o = i < sendq.size() ? &sendq[i] : nullptr;
      ngtcp2_vec v{};
      if (o) v = {const_cast<uint8_t*>(o->data->data()) + o->offset, o->data->size() - o->offset};
      uint32_t flags = NGTCP2_WRITE_STREAM_FLAG_MORE | (o && o->frame ? NGTCP2_WRITE_STREAM_FLAG_FIN : 0);
      ngtcp2_ssize consumed = -1;
      auto n = ngtcp2_conn_writev_stream(conn, &ps.path, &pi, buf, sizeof buf, &consumed, flags,
                                         o ? o->stream : -1, &v, o ? 1 : 0, ts);
      if (consumed >= 0 && o && (o->offset += size_t(consumed)) == o->data->size()) {
        sendq.erase(sendq.begin() + ptrdiff_t(i));
        o = nullptr;
      }
      if (n == NGTCP2_ERR_WRITE_MORE) continue;
      if (n == NGTCP2_ERR_STREAM_DATA_BLOCKED) { ++i; continue; }
      if (n == NGTCP2_ERR_STREAM_SHUT_WR) { sendq.erase(sendq.begin() + ptrdiff_t(i)); continue; }
      if (n < 0) return false;
      if (n == 0) break;
      ::sendto(server.fd_, buf, size_t(n), 0, reinterpret_cast<sockaddr*>(&remote), remoteLen);
    }
    ngtcp2_conn_update_pkt_tx_time(conn, ts);
    return true;
  }

  // Frames already skipped: the viewer shows nothing newer until a key frame, so ask for one now (a
  // quiet desktop may not produce one by itself for minutes).
  void skipToKey() {
    waitingKey = true;
    uint8_t request[kHeader] = {kKeyframe};
    server.hooks_.control(workspace, request);
  }

  // A newer frame is ready: a viewer more than kMaxPending frames behind drops what it has not sent
  // and waits for the next key frame, so it never shows stale video.
  void offer(const Bytes& packet, bool key) {
    if (waitingKey && !key) return;
    if (key || pendingFrames() >= kMaxPending) {
      for (auto it = sendq.begin(); it != sendq.end();)
        if (it->frame) {
          ngtcp2_conn_shutdown_stream_write(conn, 0, it->stream, 0);
          it = sendq.erase(it);
        } else ++it;
      if (!key) return skipToKey();
    }
    int64_t sid;
    if (ngtcp2_conn_open_uni_stream(conn, &sid, nullptr)) return skipToKey();  // no stream credit yet
    waitingKey = false;
    std::vector<uint8_t> out;
    varint(out, kWtUni);
    varint(out, uint64_t(session));
    out.insert(out.end(), packet->begin(), packet->end());
    send(sid, std::move(out), true);
  }

  // A packet newer frames do not replace (the guest cursor, sound), on the viewer's control stream:
  // in order, and using none of the stream credit frames need.
  void deliver(std::span<const uint8_t> packet) {
    if (control >= 0) send(control, std::vector<uint8_t>(packet.begin(), packet.end()));
  }
};

namespace {

Conn& owner(void* user) { return *static_cast<Conn*>(user); }

int onStreamData(ngtcp2_conn* conn, uint32_t, int64_t stream, uint64_t, const uint8_t* data, size_t len,
                 void* user, void*) {
  if (int rv = owner(user).receive(stream, data, len)) return rv;
  ngtcp2_conn_extend_max_stream_offset(conn, stream, len);
  ngtcp2_conn_extend_max_offset(conn, len);
  return 0;
}

int onStreamClose(ngtcp2_conn*, uint32_t, int64_t stream, uint64_t, uint64_t, void* user, void*) {
  auto& c = owner(user);
  c.held.erase(stream);
  c.rx.erase(stream);
  if (stream == c.session || stream == c.control) return NGTCP2_ERR_CALLBACK_FAILURE;  // viewer left
  return 0;
}

int onHandshake(ngtcp2_conn*, void* user) {
  owner(user).openControl();
  return 0;
}

void onRand(uint8_t* dest, size_t len, const ngtcp2_rand_ctx*) { arc4random_buf(dest, len); }

ngtcp2_conn* connOf(ngtcp2_crypto_conn_ref* ref) { return static_cast<Conn*>(ref->user_data)->conn; }

}  // namespace

namespace {

int onNewCid(ngtcp2_conn*, ngtcp2_cid* cid, ngtcp2_stateless_reset_token* token, size_t len, void* user) {
  arc4random_buf(cid->data, len);
  cid->datalen = len;
  if (owner(user).resetToken(token->data, *cid)) return NGTCP2_ERR_CALLBACK_FAILURE;
  return owner(user).addCid(*cid);
}

int onRemoveCid(ngtcp2_conn*, const ngtcp2_cid* cid, void* user) { return owner(user).removeCid(*cid); }

}  // namespace

Server::Server(Loop& loop, Hooks hooks) : loop_(loop), hooks_(std::move(hooks)) {
  arc4random_buf(secret_, sizeof secret_);
  if (ngtcp2_crypto_ossl_init()) throw std::runtime_error("ngtcp2_crypto_ossl_init failed");
  tls_ = SSL_CTX_new(TLS_server_method());
  if (!tls_) fail("SSL_CTX_new");
  SSL_CTX_set_min_proto_version(tls_, TLS1_3_VERSION);
  SSL_CTX_set_alpn_select_cb(tls_, selectAlpn, nullptr);
  rotateCertificate();

  fd_ = ::socket(AF_INET, SOCK_DGRAM, 0);
  sockaddr_in addr{.sin_len = sizeof addr, .sin_family = AF_INET, .sin_addr = {htonl(INADDR_LOOPBACK)}};
  socklen_t len = sizeof addr;
  int size = 8 << 20;
  setsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &size, sizeof size);
  setsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &size, sizeof size);
  if (fd_ < 0 || ::bind(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) ||
      getsockname(fd_, reinterpret_cast<sockaddr*>(&addr), &len) || fcntl(fd_, F_SETFL, O_NONBLOCK))
    throw std::runtime_error("cannot open the viewer UDP port");
  port_ = ntohs(addr.sin_port);
  loop_.onReadable(fd_, [this] { receive(); });
}

Server::~Server() {
  conns_.clear();
  if (fd_ >= 0) ::close(fd_);
  SSL_CTX_free(tls_);
}

// WebTransport pins certificates by hash only if ECDSA and valid for at most 14 days (W3C spec).
void Server::rotateCertificate() {
  EVP_PKEY* key = EVP_EC_gen("P-256");
  X509* cert = X509_new();
  if (!key || !cert) fail("certificate key");
  X509_set_version(cert, 2);
  ASN1_INTEGER_set_int64(X509_get_serialNumber(cert), int64_t(arc4random() & 0x7fffffff));
  X509_gmtime_adj(X509_getm_notBefore(cert), -60);
  X509_gmtime_adj(X509_getm_notAfter(cert), 13L * 24 * 3600);
  X509_NAME* name = X509_get_subject_name(cert);
  X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC, reinterpret_cast<const unsigned char*>("sentineld"), -1, -1,
                             0);
  X509_set_issuer_name(cert, name);
  X509_set_pubkey(cert, key);
  // Chromium's certificate parser requires extensions.
  X509V3_CTX ext;
  X509V3_set_ctx_nodb(&ext);
  X509V3_set_ctx(&ext, cert, cert, nullptr, nullptr, 0);
  for (auto [nid, value] : {std::pair{NID_subject_alt_name, "IP:127.0.0.1,DNS:localhost"},
                            {NID_basic_constraints, "critical,CA:FALSE"}}) {
    X509_EXTENSION* e = X509V3_EXT_conf_nid(nullptr, &ext, nid, value);
    if (!e || !X509_add_ext(cert, e, -1)) fail("certificate extension");
    X509_EXTENSION_free(e);
  }
  if (!X509_sign(cert, key, EVP_sha256()) || SSL_CTX_use_certificate(tls_, cert) != 1 ||
      SSL_CTX_use_PrivateKey(tls_, key) != 1)
    fail("certificate");
  unsigned char digest[32];
  unsigned n = 0;
  X509_digest(cert, EVP_sha256(), digest, &n);
  static const char hex[] = "0123456789abcdef";
  hash_.clear();
  for (unsigned i = 0; i < n; ++i) hash_ += {hex[digest[i] >> 4], hex[digest[i] & 15]};
  X509_free(cert);
  EVP_PKEY_free(key);
}

void Server::receive() {
  uint8_t buf[65536];
  for (int i = 0; i < 256; ++i) {
    sockaddr_storage from{};
    socklen_t fromLen = sizeof from;
    ssize_t n = ::recvfrom(fd_, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&from), &fromLen);
    if (n <= 0) return;
    ngtcp2_version_cid vc;
    if (ngtcp2_pkt_decode_version_cid(&vc, buf, size_t(n), kCidLen)) continue;
    auto it = byCid_.find(std::string_view(reinterpret_cast<const char*>(vc.dcid), vc.dcidlen));
    if (it == byCid_.end()) {
      accept({buf, size_t(n)}, from, fromLen);
      continue;
    }
    Conn& c = *it->second;
    ngtcp2_path path{{reinterpret_cast<sockaddr*>(&c.local), sizeof c.local},
                     {reinterpret_cast<sockaddr*>(&from), fromLen}, nullptr};
    ngtcp2_pkt_info pi{};
    uint64_t id = c.id;
    int rv = ngtcp2_conn_read_pkt(c.conn, &path, &pi, buf, size_t(n), now());
    if (!conns_.count(id)) continue;  // a callback closed it
    if (rv) {
      close(c);
      continue;
    }
    if (!c.flush()) close(c);
    else schedule(c);
  }
}

void Server::accept(std::span<const uint8_t> packet, const sockaddr_storage& from, uint32_t fromLen) {
  ngtcp2_pkt_hd hd;
  if (ngtcp2_accept(&hd, packet.data(), packet.size()) || conns_.size() >= 64) return;
  static constexpr ngtcp2_callbacks callbacks{
      .recv_client_initial = ngtcp2_crypto_recv_client_initial_cb,
      .recv_crypto_data = ngtcp2_crypto_recv_crypto_data_cb,
      .handshake_completed = onHandshake,
      .encrypt = ngtcp2_crypto_encrypt_cb,
      .decrypt = ngtcp2_crypto_decrypt_cb,
      .hp_mask = ngtcp2_crypto_hp_mask_cb,
      .recv_stream_data = onStreamData,
      .rand = onRand,
      .remove_connection_id = onRemoveCid,
      .update_key = ngtcp2_crypto_update_key_cb,
      .delete_crypto_aead_ctx = ngtcp2_crypto_delete_crypto_aead_ctx_cb,
      .delete_crypto_cipher_ctx = ngtcp2_crypto_delete_crypto_cipher_ctx_cb,
      .version_negotiation = ngtcp2_crypto_version_negotiation_cb,
      .get_new_connection_id2 = onNewCid,
      .get_path_challenge_data2 = ngtcp2_crypto_get_path_challenge_data2_cb,
      .stream_close2 = onStreamClose,
  };
  uint64_t id = nextId_++;
  auto owned = std::make_unique<Conn>(*this, id);
  Conn& c = *owned;
  memcpy(&c.remote, &from, fromLen);
  c.remoteLen = fromLen;
  socklen_t len = sizeof c.local;
  getsockname(fd_, reinterpret_cast<sockaddr*>(&c.local), &len);

  ngtcp2_cid scid{.datalen = kCidLen};
  arc4random_buf(scid.data, kCidLen);
  ngtcp2_settings settings;
  ngtcp2_settings_default(&settings);
  settings.initial_ts = now();
  ngtcp2_transport_params params;
  ngtcp2_transport_params_default(&params);
  params.initial_max_data = 1 << 20;
  params.initial_max_stream_data_bidi_remote = 256 << 10;
  params.initial_max_stream_data_uni = 256 << 10;
  params.initial_max_streams_bidi = 4;
  params.initial_max_streams_uni = 4;
  params.max_idle_timeout = 10 * NGTCP2_SECONDS;
  params.max_datagram_frame_size = 65535;
  params.stateless_reset_token_present = 1;
  params.active_connection_id_limit = 4;
  params.original_dcid = hd.dcid;
  params.original_dcid_present = 1;
  if (c.resetToken(params.stateless_reset_token, scid)) return;
  ngtcp2_path path{{reinterpret_cast<sockaddr*>(&c.local), sizeof c.local},
                   {reinterpret_cast<sockaddr*>(&c.remote), c.remoteLen}, nullptr};
  if (ngtcp2_conn_server_new(&c.conn, &hd.scid, &scid, &path, hd.version, &callbacks, &settings, &params, nullptr,
                             &c))
    return;
  ngtcp2_conn_set_keep_alive_timeout(c.conn, 2 * NGTCP2_SECONDS);  // an idle desktop sends no frames
  c.ref = {connOf, &c};
  if (ngtcp2_crypto_ossl_ctx_new(&c.crypto, nullptr) || !(c.ssl = SSL_new(tls_))) return;
  ngtcp2_crypto_ossl_ctx_set_ssl(c.crypto, c.ssl);
  if (ngtcp2_crypto_ossl_configure_server_session(c.ssl)) return;
  SSL_set_app_data(c.ssl, &c.ref);
  SSL_set_accept_state(c.ssl);
  ngtcp2_conn_set_tls_native_handle(c.conn, c.crypto);

  c.addCid(scid);
  conns_[id] = std::move(owned);
  // A viewer that does not present its ticket within 5 s is dropped.
  loop_.after(5000, [this, id] {
    auto it = conns_.find(id);
    if (it != conns_.end() && it->second->workspace.empty()) close(*it->second);
  });
  ngtcp2_pkt_info pi{};
  if (ngtcp2_conn_read_pkt(c.conn, &path, &pi, packet.data(), packet.size(), now()) || !c.flush()) {
    if (conns_.count(id)) close(c);
    return;
  }
  schedule(c);
}

void Server::schedule(Conn& c) {
  loop_.cancel(c.timer);
  auto expiry = ngtcp2_conn_get_expiry(c.conn), t = now();
  uint64_t ms = expiry <= t ? 0 : std::min<uint64_t>((expiry - t + 999999) / 1000000, 3600000);
  uint64_t id = c.id;
  c.timer = loop_.after(ms, [this, id] {
    auto it = conns_.find(id);
    if (it == conns_.end()) return;
    Conn& c = *it->second;
    c.timer = 0;
    if (ngtcp2_conn_handle_expiry(c.conn, now()) || !c.flush()) return close(c);
    schedule(c);
  });
}

void Server::close(Conn& c) {
  if (!ngtcp2_conn_in_closing_period(c.conn) && !ngtcp2_conn_in_draining_period(c.conn)) {
    uint8_t buf[1452];
    ngtcp2_path_storage ps;
    ngtcp2_path_storage_zero(&ps);
    ngtcp2_pkt_info pi;
    ngtcp2_ccerr err;
    ngtcp2_ccerr_default(&err);
    auto n = ngtcp2_conn_write_connection_close(c.conn, &ps.path, &pi, buf, sizeof buf, &err, now());
    if (n > 0) ::sendto(fd_, buf, size_t(n), 0, reinterpret_cast<sockaddr*>(&c.remote), c.remoteLen);
  }
  if (!c.workspace.empty()) c.releasePressed();
  loop_.cancel(c.timer);
  for (auto& cid : c.cids) byCid_.erase(cid);
  const std::string workspace = c.workspace;
  const bool reported = c.refreshHz != 0;
  conns_.erase(c.id);
  if (reported) refresh(workspace);
  if (!workspace.empty()) watching(workspace);
}

void Server::watching(const std::string& workspace) {
  bool any = false;
  for (auto& [_, c] : conns_) any = any || c->workspace == workspace;
  if (any == watched_.count(workspace) > 0) return;
  if (any) watched_.insert(workspace);
  else watched_.erase(workspace);
  uint8_t packet[kHeader + 1] = {kWatching};
  packet[7] = 1;
  packet[kHeader] = any;
  hooks_.control(workspace, packet);
}

void Server::refresh(const std::string& workspace) {
  uint16_t hz = 0;
  for (auto& [_, c] : conns_)
    if (c->workspace == workspace) hz = std::max(hz, c->refreshHz);
  if (!hz) return;  // no viewer says: the display keeps its rate
  auto& last = refresh_[workspace];
  if (hz == last) return;
  last = hz;
  uint8_t packet[kHeader + 2] = {kRefresh};
  packet[7] = 2;
  packet[kHeader] = uint8_t(hz >> 8), packet[kHeader + 1] = uint8_t(hz);
  hooks_.control(workspace, packet);
}

void Server::publish(const std::string& workspace, std::span<const uint8_t> head, std::span<const uint8_t> frame,
                     bool key) {
  Bytes packet;
  for (auto it = conns_.begin(); it != conns_.end();) {
    Conn& c = *it++->second;
    if (c.workspace != workspace) continue;
    if (!packet) {
      auto bytes = std::make_shared<std::vector<uint8_t>>();
      if (key) bytes->assign(head.begin(), head.end());
      bytes->insert(bytes->end(), frame.begin(), frame.end());
      packet = std::move(bytes);
    }
    c.offer(packet, key);
    if (!c.flush()) close(c);
    else schedule(c);
  }
}

void Server::relay(const std::string& workspace, std::span<const uint8_t> packet) {
  for (auto it = conns_.begin(); it != conns_.end();) {
    Conn& c = *it++->second;
    if (c.workspace != workspace) continue;
    c.deliver(packet);
    if (!c.flush()) close(c);
    else schedule(c);
  }
}

void Server::drop(const std::string& workspace) {
  for (auto it = conns_.begin(); it != conns_.end();) {
    Conn& c = *it++->second;
    if (c.workspace == workspace) close(c);
  }
  refresh_.erase(workspace);  // a new provider hears the rate from its new viewers
  watched_.erase(workspace);  // and that it has viewers, from the first one
}

}  // namespace sentineld::media
