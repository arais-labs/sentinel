// e2e-viewer: a headless viewer for the end-to-end tests (DESIGN.md "End-to-end tests"). It joins a
// workspace's desktop exactly as the desktop app's viewer does (§6: WebTransport over HTTP/3 on
// sentineld-media's loopback port, the certificate pinned by hash, the ticket in a hello), sends the
// viewer's input packets, and reports what a viewer receives as JSON lines on stdout:
//   {"type":"ready"}                                   the session is up and the hello sent
//   {"type":"config","width","height"}                 a decoder config (on every key frame)
//   {"type":"frame","key","bytes"}                     one video frame
//   {"type":"cursor","hidden","width","height","hot_x","hot_y","opaque","rgba"}   a cursor packet
//   {"type":"closed","reason"}                         the session ended
// stdin takes one command per line: "input <hex payload>" (a §6 input packet's payload), "key" (a
// key frame request), "refresh <hz>" (the viewer's screen refresh rate), "quit". Times are milliseconds since start ("t").
//
//   e2e-viewer PORT CERT_SHA256 TICKET
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

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <string>
#include <vector>

namespace {

constexpr uint64_t kWtBidi = 0x41, kWtUni = 0x54;
constexpr size_t kHeader = 16;

ngtcp2_tstamp now() { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
uint64_t startMs = now() / 1000000;
uint64_t ms() { return now() / 1000000 - startMs; }

void emit(const std::string& json) {
  std::printf("{\"t\":%llu,%s}\n", (unsigned long long)ms(), json.c_str());
  std::fflush(stdout);
}

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

std::string base64(const uint8_t* p, size_t n) {
  static const char* k = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  size_t i = 0;
  for (; i + 2 < n; i += 3) {
    uint32_t v = uint32_t(p[i]) << 16 | uint32_t(p[i + 1]) << 8 | p[i + 2];
    out += {k[v >> 18], k[v >> 12 & 63], k[v >> 6 & 63], k[v & 63]};
  }
  if (i + 1 == n) {
    uint32_t v = uint32_t(p[i]) << 16;
    out += {k[v >> 18], k[v >> 12 & 63], '=', '='};
  } else if (i + 2 == n) {
    uint32_t v = uint32_t(p[i]) << 16 | uint32_t(p[i + 1]) << 8;
    out += {k[v >> 18], k[v >> 12 & 63], k[v >> 6 & 63], '='};
  }
  return out;
}

uint32_t be32(const uint8_t* p) { return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3]; }
uint16_t be16(const uint8_t* p) { return uint16_t(p[0] << 8 | p[1]); }

struct Viewer {
  int fd = -1;
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
  std::map<int64_t, std::string> rx;  // per stream, bytes not yet parsed
  std::map<int64_t, int> kind;        // 1 = WebTransport packets, 2 = ignored
  int64_t session = -1, control = -1;
  bool connected = false, closed = false;
  std::string reason;

  void send(int64_t stream, std::vector<uint8_t> bytes) { sendq.push_back({stream, std::move(bytes), 0}); }

  void packet(const uint8_t* h, size_t len) {
    const uint8_t type = h[0];
    const uint8_t* p = h + kHeader;
    if (type == 1 && len >= 8) {
      emit("\"type\":\"config\",\"width\":" + std::to_string(be32(p)) + ",\"height\":" + std::to_string(be32(p + 4)));
    } else if (type == 2) {
      emit("\"type\":\"frame\",\"key\":" + std::string(h[1] & 1 ? "true" : "false") + ",\"bytes\":" + std::to_string(len));
    } else if (type == 6) {
      if (len < 8) return emit("\"type\":\"cursor\",\"hidden\":true");
      const unsigned hx = be16(p), hy = be16(p + 2), w = be16(p + 4), hgt = be16(p + 6);
      size_t opaque = 0;
      for (size_t i = 0; i < size_t(w) * hgt && 8 + i * 4 + 3 < len; ++i) opaque += p[8 + i * 4 + 3] > 32;
      emit("\"type\":\"cursor\",\"hidden\":false,\"width\":" + std::to_string(w) + ",\"height\":" + std::to_string(hgt) +
           ",\"hot_x\":" + std::to_string(hx) + ",\"hot_y\":" + std::to_string(hy) + ",\"opaque\":" + std::to_string(opaque) +
           ",\"rgba\":\"" + base64(p + 8, len - 8) + "\"");
    }
  }

  // §6 packets, back to back, from `at`.
  void packets(std::string& buf, size_t at) {
    while (buf.size() - at >= kHeader) {
      auto* h = reinterpret_cast<const uint8_t*>(buf.data() + at);
      const uint32_t n = be32(h + 4);
      if (buf.size() - at < kHeader + n) break;
      packet(h, n);
      at += kHeader + n;
    }
    buf.erase(0, at);
  }

  void receive(int64_t stream, const uint8_t* data, size_t len) {
    auto& buf = rx[stream];
    buf.append(reinterpret_cast<const char*>(data), len);
    if (stream == control) return packets(buf, 0);
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
    if (!kind.count(stream)) {  // a server unidirectional stream: classify by its first varints
      size_t at = 0;
      uint64_t first, sid;
      if (!readVarint(buf, at, first)) return;
      if (first != kWtUni) {
        kind[stream] = 2;  // HTTP/3 control, QPACK
        buf.clear();
        return;
      }
      if (!readVarint(buf, at, sid)) return;
      kind[stream] = 1;
      buf.erase(0, at);
    }
    if (kind[stream] == 1) packets(buf, 0);
    else buf.clear();
  }

  void fail(const std::string& why) {
    if (closed) return;
    closed = true;
    reason = why;
  }

  // The viewer's control stream: the WebTransport bidirectional signal, then the hello.
  void openControl() {
    if (ngtcp2_conn_open_bidi_stream(conn, &control, nullptr)) return fail("no stream for the control channel");
    std::vector<uint8_t> out;
    varint(out, kWtBidi);
    varint(out, uint64_t(session));
    uint8_t header[kHeader] = {16};
    const uint32_t n = uint32_t(ticket.size());
    header[4] = uint8_t(n >> 24), header[5] = uint8_t(n >> 16), header[6] = uint8_t(n >> 8), header[7] = uint8_t(n);
    out.insert(out.end(), header, header + kHeader);
    out.insert(out.end(), ticket.begin(), ticket.end());
    send(control, std::move(out));
    emit("\"type\":\"ready\"");
  }

  void controlPacket(uint8_t type, const std::vector<uint8_t>& payload) {
    if (control < 0) return;
    std::vector<uint8_t> out(kHeader, 0);
    out[0] = type;
    const uint32_t n = uint32_t(payload.size());
    out[4] = uint8_t(n >> 24), out[5] = uint8_t(n >> 16), out[6] = uint8_t(n >> 8), out[7] = uint8_t(n);
    const uint64_t us = now() / 1000;
    for (int i = 0; i < 8; ++i) out[8 + i] = uint8_t(us >> (56 - 8 * i));
    out.insert(out.end(), payload.begin(), payload.end());
    send(control, std::move(out));
  }

  // The HTTP/3 control stream and the extended CONNECT, once the handshake is done.
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
      send(uni, std::move(out));
    }
    if (ngtcp2_conn_open_bidi_stream(conn, &session, nullptr)) return fail("no stream for the CONNECT");
    // QPACK, static table only: :method CONNECT (15), :scheme https (23), :path / (1), then
    // :authority (name 0) and :protocol (a literal name) as literals.
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
    send(session, std::move(out));
  }

  bool flush() {
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
      if (n == NGTCP2_ERR_STREAM_DATA_BLOCKED) { ++i; continue; }
      if (n == NGTCP2_ERR_STREAM_SHUT_WR) { sendq.erase(sendq.begin() + ptrdiff_t(i)); continue; }
      if (n < 0) return false;
      if (n == 0) break;
      ::send(fd, buf, size_t(n), 0);
    }
    ngtcp2_conn_update_pkt_tx_time(conn, ts);
    return true;
  }
};

Viewer& owner(void* user) { return *static_cast<Viewer*>(user); }

int onStreamData(ngtcp2_conn* conn, uint32_t, int64_t stream, uint64_t, const uint8_t* data, size_t len, void* user, void*) {
  owner(user).receive(stream, data, len);
  ngtcp2_conn_extend_max_stream_offset(conn, stream, len);
  ngtcp2_conn_extend_max_offset(conn, len);
  return 0;
}

int onStreamClose(ngtcp2_conn* conn, uint32_t, int64_t stream, uint64_t, uint64_t, void* user, void*) {
  auto& v = owner(user);
  v.rx.erase(stream);
  v.kind.erase(stream);
  if (stream == v.session || stream == v.control) v.fail("the server closed the session");
  if (!ngtcp2_conn_is_local_stream(conn, stream) && (stream & 2)) ngtcp2_conn_extend_max_streams_uni(conn, 1);
  return 0;
}

int onHandshake(ngtcp2_conn*, void* user) {
  owner(user).handshake();
  return 0;
}

void onRand(uint8_t* dest, size_t len, const ngtcp2_rand_ctx*) { arc4random_buf(dest, len); }

int onNewCid(ngtcp2_conn*, ngtcp2_cid* cid, ngtcp2_stateless_reset_token* token, size_t len, void*) {
  arc4random_buf(cid->data, len);
  cid->datalen = len;
  arc4random_buf(token->data, sizeof token->data);
  return 0;
}

ngtcp2_conn* connOf(ngtcp2_crypto_conn_ref* ref) { return static_cast<Viewer*>(ref->user_data)->conn; }

std::vector<uint8_t> unhex(const std::string& s) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < s.size(); i += 2) out.push_back(uint8_t(std::strtoul(s.substr(i, 2).c_str(), nullptr, 16)));
  return out;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::fprintf(stderr, "usage: %s PORT CERT_SHA256 TICKET\n", argv[0]);
    return 2;
  }
  Viewer v;
  v.hash = argv[2];
  v.ticket = argv[3];
  v.fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  v.remote = {.sin_len = sizeof v.remote, .sin_family = AF_INET, .sin_port = htons(uint16_t(std::atoi(argv[1]))),
              .sin_addr = {htonl(INADDR_LOOPBACK)}};
  socklen_t len = sizeof v.local;
  int size = 8 << 20;
  setsockopt(v.fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof size);
  if (v.fd < 0 || ::connect(v.fd, reinterpret_cast<sockaddr*>(&v.remote), sizeof v.remote) ||
      getsockname(v.fd, reinterpret_cast<sockaddr*>(&v.local), &len)) {
    emit("\"type\":\"closed\",\"reason\":\"cannot open a UDP socket\"");
    return 1;
  }
  fcntl(v.fd, F_SETFL, O_NONBLOCK);

  if (ngtcp2_crypto_ossl_init()) return 1;
  v.tls = SSL_CTX_new(TLS_client_method());
  SSL_CTX_set_min_proto_version(v.tls, TLS1_3_VERSION);
  SSL_CTX_set_verify(v.tls, SSL_VERIFY_NONE, nullptr);  // pinned by hash once the handshake is done
  v.ssl = SSL_new(v.tls);
  if (ngtcp2_crypto_ossl_ctx_new(&v.crypto, v.ssl) || ngtcp2_crypto_ossl_configure_client_session(v.ssl)) return 1;
  v.ref = {connOf, &v};
  SSL_set_app_data(v.ssl, &v.ref);
  SSL_set_connect_state(v.ssl);
  static const unsigned char alpn[] = {2, 'h', '3'};
  SSL_set_alpn_protos(v.ssl, alpn, sizeof alpn);
  SSL_set_tlsext_host_name(v.ssl, "localhost");

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
  ngtcp2_path path{{reinterpret_cast<sockaddr*>(&v.local), sizeof v.local},
                   {reinterpret_cast<sockaddr*>(&v.remote), sizeof v.remote}, nullptr};
  if (ngtcp2_conn_client_new(&v.conn, &dcid, &scid, &path, NGTCP2_PROTO_VER_V1, &callbacks, &settings, &params, nullptr, &v))
    return 1;
  ngtcp2_conn_set_tls_native_handle(v.conn, v.crypto);
  if (!v.flush()) v.fail("cannot start the handshake");

  std::string line;
  bool stdinOpen = true;
  while (!v.closed) {
    const auto expiry = ngtcp2_conn_get_expiry(v.conn), t = now();
    const int timeout = expiry <= t ? 0 : int(std::min<uint64_t>((expiry - t) / 1000000 + 1, 1000));
    pollfd fds[2] = {{v.fd, POLLIN, 0}, {STDIN_FILENO, short(stdinOpen ? POLLIN : 0), 0}};
    ::poll(fds, 2, timeout);
    if (fds[0].revents & POLLIN) {
      uint8_t buf[65536];
      for (;;) {
        ssize_t n = ::recv(v.fd, buf, sizeof buf, 0);
        if (n <= 0) break;
        ngtcp2_pkt_info pi{};
        int rv = ngtcp2_conn_read_pkt(v.conn, &path, &pi, buf, size_t(n), now());
        if (rv) {
          v.fail(rv == NGTCP2_ERR_DRAINING ? "the server closed the connection" : ngtcp2_strerror(rv));
          break;
        }
      }
    }
    if (fds[1].revents & (POLLIN | POLLHUP)) {
      char buf[4096];
      ssize_t n = ::read(STDIN_FILENO, buf, sizeof buf);
      if (n <= 0) stdinOpen = false;
      else line.append(buf, size_t(n));
      for (size_t nl; (nl = line.find('\n')) != std::string::npos;) {
        const std::string cmd = line.substr(0, nl);
        line.erase(0, nl + 1);
        if (cmd.rfind("input ", 0) == 0) v.controlPacket(3, unhex(cmd.substr(6)));
        else if (cmd == "key") v.controlPacket(4, {});
        else if (cmd.rfind("refresh ", 0) == 0) {
          const unsigned hz = unsigned(std::atoi(cmd.c_str() + 8));
          v.controlPacket(7, {uint8_t(hz >> 8), uint8_t(hz)});
        }
        else if (cmd == "quit") v.fail("quit");
      }
      if (!stdinOpen) v.fail("quit");
    }
    if (ngtcp2_conn_get_expiry(v.conn) <= now() && ngtcp2_conn_handle_expiry(v.conn, now())) v.fail("idle timeout");
    if (!v.closed && !v.flush()) v.fail("the connection failed");
  }
  emit("\"type\":\"closed\",\"reason\":\"" + v.reason + "\"");
  return v.reason == "quit" ? 0 : 1;
}
