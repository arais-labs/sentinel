#include "transport.hpp"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <ngtcp2/ngtcp2_crypto_ossl.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <poll.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <atomic>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <thread>

namespace viewer {

namespace {

constexpr uint64_t kWtBidi = 0x41, kWtUni = 0x54;
constexpr size_t kHeader = 16;

ngtcp2_tstamp now() { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }

void varint(std::vector<uint8_t>& out, uint64_t v) {
  int n = v < 64 ? 0 : v < 16384 ? 1 : v < (1u << 30) ? 2 : 3;
  for (int i = (1 << n) - 1; i >= 0; --i) out.push_back(uint8_t(v >> (8 * i)) | (i == (1 << n) - 1 ? n << 6 : 0));
}

bool readVarint(const std::string& s, size_t& at, uint64_t& v) {
  if (at >= s.size()) return false;
  size_t n = size_t(1) << (uint8_t(s[at]) >> 6);
  if (at + n > s.size()) return false;
  v = uint8_t(s[at]) & 0x3f;
  for (size_t i = 1; i < n; ++i) v = v << 8 | uint8_t(s[at + i]);
  at += n;
  return true;
}

void h3frame(std::vector<uint8_t>& out, uint64_t type, const std::vector<uint8_t>& payload) {
  varint(out, type);
  varint(out, payload.size());
  out.insert(out.end(), payload.begin(), payload.end());
}

uint32_t be32(const uint8_t* p) { return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3]; }
uint64_t be64(const uint8_t* p) { return uint64_t(be32(p)) << 32 | be32(p + 4); }

std::vector<uint8_t> packet(uint8_t type, const std::vector<uint8_t>& payload) {
  std::vector<uint8_t> out(kHeader, 0);
  out[0] = type;
  const uint32_t n = uint32_t(payload.size());
  out[4] = uint8_t(n >> 24), out[5] = uint8_t(n >> 16), out[6] = uint8_t(n >> 8), out[7] = uint8_t(n);
  const uint64_t us = now() / 1000;
  for (int i = 0; i < 8; ++i) out[8 + i] = uint8_t(us >> (56 - 8 * i));
  out.insert(out.end(), payload.begin(), payload.end());
  return out;
}

}  // namespace

struct Transport::State {
  Transport* owner = nullptr;
  int fd = -1, wake[2] = {-1, -1};
  sockaddr_in local{}, remote{};
  ngtcp2_conn* conn = nullptr;
  ngtcp2_crypto_ossl_ctx* crypto = nullptr;
  SSL_CTX* tls = nullptr;
  SSL* ssl = nullptr;
  ngtcp2_crypto_conn_ref ref{};
  std::string hash, ticket;
  struct Out {
    int64_t stream;
    std::vector<uint8_t> data;
    size_t offset;
  };
  std::deque<Out> sendq;
  std::map<int64_t, std::string> rx;
  std::map<int64_t, int> kind;  // 1 = WebTransport packets, 2 = ignored
  int64_t session = -1, control = -1;
  bool connected = false, closed = false;
  std::string reason;
  std::thread thread;
  std::atomic<bool> stopping{false};
  std::mutex lock;  // pending: control packets from other threads
  std::vector<std::vector<uint8_t>> pending;

  void queue(int64_t stream, std::vector<uint8_t> bytes) { sendq.push_back({stream, std::move(bytes), 0}); }

  void packets(std::string& buf) {
    size_t at = 0;
    while (buf.size() - at >= kHeader) {
      auto* h = reinterpret_cast<const uint8_t*>(buf.data() + at);
      const uint32_t n = be32(h + 4);
      if (buf.size() - at < kHeader + n) break;
      if (owner->onPacket) owner->onPacket(h[0], h[1], be64(h + 8), h + kHeader, n);
      at += kHeader + n;
    }
    buf.erase(0, at);
  }

  void receive(int64_t stream, const uint8_t* data, size_t len) {
    auto& buf = rx[stream];
    buf.append(reinterpret_cast<const char*>(data), len);
    if (stream == control) return packets(buf);
    if (stream == session) {
      // The CONNECT response: a HEADERS frame whose QPACK block starts with static 25 (":status: 200").
      size_t at = 0;
      uint64_t type, n;
      if (!connected && readVarint(buf, at, type) && readVarint(buf, at, n) && buf.size() - at >= n && type == 0x01) {
        if (n < 3 || uint8_t(buf[at + 2]) != 0xd9) return fail("the WebTransport CONNECT was refused");
        connected = true;
        openControl();
        buf.clear();
      }
      return;
    }
    if (!kind.count(stream)) {
      size_t at = 0;
      uint64_t first, sid;
      if (!readVarint(buf, at, first)) return;
      if (first != kWtUni) {
        kind[stream] = 2;
        buf.clear();
        return;
      }
      if (!readVarint(buf, at, sid)) return;
      kind[stream] = 1;
      buf.erase(0, at);
    }
    if (kind[stream] == 1) packets(buf);
    else buf.clear();
  }

  void fail(const std::string& why) {
    if (closed) return;
    closed = true;
    reason = why;
  }

  void openControl() {
    if (ngtcp2_conn_open_bidi_stream(conn, &control, nullptr)) return fail("no stream for the control channel");
    std::vector<uint8_t> out;
    varint(out, kWtBidi);
    varint(out, uint64_t(session));
    const auto hello = packet(16, std::vector<uint8_t>(ticket.begin(), ticket.end()));
    out.insert(out.end(), hello.begin(), hello.end());
    queue(control, std::move(out));
    if (owner->onReady) owner->onReady();
  }

  void handshake() {
    const X509* cert = SSL_get0_peer_certificate(ssl);
    unsigned char digest[32];
    unsigned n = 0;
    std::string got;
    if (cert && X509_digest(cert, EVP_sha256(), digest, &n))
      for (unsigned i = 0; i < n; ++i) got += {"0123456789abcdef"[digest[i] >> 4], "0123456789abcdef"[digest[i] & 15]};
    if (got != hash) return fail("the certificate does not match cert_sha256");
    int64_t uni;
    if (ngtcp2_conn_open_uni_stream(conn, &uni, nullptr) == 0) {
      std::vector<uint8_t> settings, out{0x00};
      for (auto [k, v] : {std::pair<uint64_t, uint64_t>{0x33, 1}, {0x2b603742, 1}}) varint(settings, k), varint(settings, v);
      h3frame(out, 0x04, settings);
      queue(uni, std::move(out));
    }
    if (ngtcp2_conn_open_bidi_stream(conn, &session, nullptr)) return fail("no stream for the CONNECT");
    std::vector<uint8_t> fields{0x00, 0x00, 0xcf, 0xd7, 0xc1};
    const std::string authority = "localhost:" + std::to_string(ntohs(remote.sin_port));
    fields.push_back(0x50);
    fields.push_back(uint8_t(authority.size()));
    fields.insert(fields.end(), authority.begin(), authority.end());
    static const char protocol[] = ":protocol", value[] = "webtransport";
    fields.push_back(0x27);
    fields.push_back(uint8_t(sizeof protocol - 1 - 7));
    fields.insert(fields.end(), protocol, protocol + sizeof protocol - 1);
    fields.push_back(uint8_t(sizeof value - 1));
    fields.insert(fields.end(), value, value + sizeof value - 1);
    std::vector<uint8_t> out;
    h3frame(out, 0x01, fields);
    queue(session, std::move(out));
  }

  bool flush() {
    {
      std::lock_guard<std::mutex> g(lock);
      if (control >= 0)
        for (auto& p : pending) queue(control, std::move(p));
      pending.clear();
    }
    uint8_t buf[1452];
    ngtcp2_path_storage ps;
    ngtcp2_path_storage_zero(&ps);
    ngtcp2_pkt_info pi;
    const auto ts = now();
    size_t i = 0;
    for (;;) {
      Out* o = i < sendq.size() ? &sendq[i] : nullptr;
      ngtcp2_vec v{};
      if (o) v = {o->data.data() + o->offset, o->data.size() - o->offset};
      ngtcp2_ssize consumed = -1;
      auto n = ngtcp2_conn_writev_stream(conn, &ps.path, &pi, buf, sizeof buf, &consumed, NGTCP2_WRITE_STREAM_FLAG_MORE,
                                         o ? o->stream : -1, &v, o ? 1 : 0, ts);
      if (consumed >= 0 && o && (o->offset += size_t(consumed)) == o->data.size()) sendq.erase(sendq.begin() + ptrdiff_t(i));
      if (n == NGTCP2_ERR_WRITE_MORE) continue;
      if (n == NGTCP2_ERR_STREAM_DATA_BLOCKED) {
        ++i;
        continue;
      }
      if (n == NGTCP2_ERR_STREAM_SHUT_WR) {
        sendq.erase(sendq.begin() + ptrdiff_t(i));
        continue;
      }
      if (n < 0) return false;
      if (n == 0) break;
      ::send(fd, buf, size_t(n), 0);
    }
    ngtcp2_conn_update_pkt_tx_time(conn, ts);
    return true;
  }

  void run(int port);
};

namespace {

Transport::State& stateOf(void* user) { return *static_cast<Transport::State*>(user); }

int onStreamData(ngtcp2_conn* conn, uint32_t, int64_t stream, uint64_t, const uint8_t* data, size_t len, void* user, void*) {
  stateOf(user).receive(stream, data, len);
  ngtcp2_conn_extend_max_stream_offset(conn, stream, len);
  ngtcp2_conn_extend_max_offset(conn, len);
  return 0;
}

int onStreamClose(ngtcp2_conn* conn, uint32_t, int64_t stream, uint64_t, uint64_t, void* user, void*) {
  auto& s = stateOf(user);
  s.rx.erase(stream);
  s.kind.erase(stream);
  if (stream == s.session || stream == s.control) s.fail("the server closed the session");
  if (!ngtcp2_conn_is_local_stream(conn, stream) && (stream & 2)) ngtcp2_conn_extend_max_streams_uni(conn, 1);
  return 0;
}

int onHandshake(ngtcp2_conn*, void* user) {
  stateOf(user).handshake();
  return 0;
}

void onRand(uint8_t* dest, size_t len, const ngtcp2_rand_ctx*) { arc4random_buf(dest, len); }

int onNewCid(ngtcp2_conn*, ngtcp2_cid* cid, ngtcp2_stateless_reset_token* token, size_t len, void*) {
  arc4random_buf(cid->data, len);
  cid->datalen = len;
  arc4random_buf(token->data, sizeof token->data);
  return 0;
}

ngtcp2_conn* connOf(ngtcp2_crypto_conn_ref* ref) { return static_cast<Transport::State*>(ref->user_data)->conn; }

}  // namespace

void Transport::State::run(int port) {
  fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  remote = {.sin_len = sizeof remote, .sin_family = AF_INET, .sin_port = htons(uint16_t(port)), .sin_addr = {htonl(INADDR_LOOPBACK)}};
  socklen_t len = sizeof local;
  int size = 8 << 20;
  setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof size);
  if (fd < 0 || ::connect(fd, reinterpret_cast<sockaddr*>(&remote), sizeof remote) ||
      getsockname(fd, reinterpret_cast<sockaddr*>(&local), &len)) {
    if (owner->onClosed) owner->onClosed("cannot open a UDP socket");
    return;
  }
  fcntl(fd, F_SETFL, O_NONBLOCK);
  if (ngtcp2_crypto_ossl_init()) { fail("no TLS"); if (owner->onClosed) owner->onClosed(reason); return; }
  tls = SSL_CTX_new(TLS_client_method());
  SSL_CTX_set_min_proto_version(tls, TLS1_3_VERSION);
  SSL_CTX_set_verify(tls, SSL_VERIFY_NONE, nullptr);  // pinned by hash once the handshake is done
  ssl = SSL_new(tls);
  if (ngtcp2_crypto_ossl_ctx_new(&crypto, ssl) || ngtcp2_crypto_ossl_configure_client_session(ssl)) { fail("no TLS session"); if (owner->onClosed) owner->onClosed(reason); return; }
  ref = {connOf, this};
  SSL_set_app_data(ssl, &ref);
  SSL_set_connect_state(ssl);
  static const unsigned char alpn[] = {2, 'h', '3'};
  SSL_set_alpn_protos(ssl, alpn, sizeof alpn);
  SSL_set_tlsext_host_name(ssl, "localhost");

  static constexpr ngtcp2_callbacks callbacks{
      .client_initial = ngtcp2_crypto_client_initial_cb,
      .recv_crypto_data = ngtcp2_crypto_recv_crypto_data_cb,
      .handshake_completed = onHandshake,
      .encrypt = ngtcp2_crypto_encrypt_cb,
      .decrypt = ngtcp2_crypto_decrypt_cb,
      .hp_mask = ngtcp2_crypto_hp_mask_cb,
      .recv_stream_data = onStreamData,
      .recv_retry = ngtcp2_crypto_recv_retry_cb,
      .rand = onRand,
      .update_key = ngtcp2_crypto_update_key_cb,
      .delete_crypto_aead_ctx = ngtcp2_crypto_delete_crypto_aead_ctx_cb,
      .delete_crypto_cipher_ctx = ngtcp2_crypto_delete_crypto_cipher_ctx_cb,
      .version_negotiation = ngtcp2_crypto_version_negotiation_cb,
      .get_new_connection_id2 = onNewCid,
      .get_path_challenge_data2 = ngtcp2_crypto_get_path_challenge_data2_cb,
      .stream_close2 = onStreamClose,
  };
  ngtcp2_cid dcid{.datalen = 18}, scid{.datalen = 18};
  arc4random_buf(dcid.data, dcid.datalen);
  arc4random_buf(scid.data, scid.datalen);
  ngtcp2_settings settings;
  ngtcp2_settings_default(&settings);
  settings.initial_ts = now();
  ngtcp2_transport_params params;
  ngtcp2_transport_params_default(&params);
  params.initial_max_data = 64 << 20;
  params.initial_max_stream_data_bidi_local = 4 << 20;
  params.initial_max_stream_data_bidi_remote = 4 << 20;
  params.initial_max_stream_data_uni = 16 << 20;
  params.initial_max_streams_bidi = 4;
  params.initial_max_streams_uni = 64;
  params.max_idle_timeout = 30 * NGTCP2_SECONDS;
  ngtcp2_path path{{reinterpret_cast<sockaddr*>(&local), sizeof local}, {reinterpret_cast<sockaddr*>(&remote), sizeof remote}, nullptr};
  if (ngtcp2_conn_client_new(&conn, &dcid, &scid, &path, NGTCP2_PROTO_VER_V1, &callbacks, &settings, &params, nullptr, this))
    { fail("no QUIC connection"); if (owner->onClosed) owner->onClosed(reason); return; }
  ngtcp2_conn_set_tls_native_handle(conn, crypto);
  if (!flush()) fail("cannot start the handshake");

  while (!closed && !stopping) {
    const auto expiry = ngtcp2_conn_get_expiry(conn), t = now();
    const int timeout = expiry <= t ? 0 : int(std::min<uint64_t>((expiry - t) / 1000000 + 1, 1000));
    pollfd fds[2] = {{fd, POLLIN, 0}, {wake[0], POLLIN, 0}};
    ::poll(fds, 2, timeout);
    if (fds[1].revents & POLLIN) {
      char drain[64];
      while (::read(wake[0], drain, sizeof drain) > 0) {
      }
    }
    if (fds[0].revents & POLLIN) {
      uint8_t buf[65536];
      for (;;) {
        ssize_t n = ::recv(fd, buf, sizeof buf, 0);
        if (n <= 0) break;
        ngtcp2_pkt_info pi{};
        int rv = ngtcp2_conn_read_pkt(conn, &path, &pi, buf, size_t(n), now());
        if (rv) {
          fail(rv == NGTCP2_ERR_DRAINING ? "the server closed the connection" : ngtcp2_strerror(rv));
          break;
        }
      }
    }
    if (ngtcp2_conn_get_expiry(conn) <= now() && ngtcp2_conn_handle_expiry(conn, now())) fail("idle timeout");
    if (!closed && !flush()) fail("the connection failed");
  }
  if (owner->onClosed) owner->onClosed(stopping ? "closed" : reason);
}

Transport::Transport() : s_(std::make_unique<State>()) { s_->owner = this; }

Transport::~Transport() { stop(); }

void Transport::start(int port, const std::string& certSha256, const std::string& ticket) {
  s_->hash = certSha256;
  s_->ticket = ticket;
  if (::pipe(s_->wake) == 0)
    for (int fd : s_->wake) fcntl(fd, F_SETFL, O_NONBLOCK);
  s_->thread = std::thread([this, port] { s_->run(port); });
}

void Transport::send(uint8_t type, const std::vector<uint8_t>& payload) {
  {
    std::lock_guard<std::mutex> g(s_->lock);
    s_->pending.push_back(packet(type, payload));
  }
  char c = 0;
  (void)!::write(s_->wake[1], &c, 1);
}

void Transport::stop() {
  if (!s_->thread.joinable()) return;
  s_->stopping = true;
  char c = 0;
  (void)!::write(s_->wake[1], &c, 1);
  s_->thread.join();
}

}  // namespace viewer
