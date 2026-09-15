"""Executed inside the guest. Contains no managed credentials.

A process-local HTTPS proxy relays requests over the private runtime process
stream. Only the host broker can authenticate upstream requests.
"""

import traceback

import base64
import http.server
import json
import os
import queue
import signal
import ssl
import subprocess
import sys
import tempfile
import threading

write_lock = threading.Lock()
responses = {}
sequence = 0
process = None


def stop(signum, frame):
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass
    raise SystemExit(1)


signal.signal(signal.SIGTERM, stop)


def emit(event):
    with write_lock:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()


def receive():
    for line in sys.stdin:
        event = json.loads(line)
        if event.get("type") == "cancel":
            os.kill(os.getpid(), signal.SIGTERM)
            return
        responses[event["id"]].put(event)
    os.kill(os.getpid(), signal.SIGTERM)


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        if self.path not in spec["authorities"]:
            self.send_error(403)
            return
        self.send_response(200)
        self.end_headers()
        self.connection = tls.wrap_socket(self.connection, server_side=True)
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")
        self.close_connection = False

    def request(self):
        global sequence
        with write_lock:
            sequence += 1
            key = sequence
            responses[key] = queue.Queue()
        try:
            emit(
                {
                    "type": "request",
                    "id": key,
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                }
            )
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                while True:
                    size = int(self.rfile.readline().split(b";")[0], 16)
                    if not size:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        break
                    remaining = size
                    while remaining:
                        data = self.rfile.read(min(remaining, 128 * 1024))
                        if not data:
                            raise EOFError()
                        remaining -= len(data)
                        emit({"type": "body", "id": key, "data": base64.b64encode(data).decode()})
                    self.rfile.read(2)
            else:
                remaining = int(self.headers.get("Content-Length", 0))
                while remaining:
                    data = self.rfile.read(min(remaining, 128 * 1024))
                    if not data:
                        raise EOFError()
                    remaining -= len(data)
                    emit({"type": "body", "id": key, "data": base64.b64encode(data).decode()})
            emit({"type": "end", "id": key})
            event = responses[key].get(timeout=600)
            self.send_response(event["status"])
            for name, value in event.get("headers", {}).items():
                if name.lower() not in {
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "content-encoding",
                }:
                    self.send_header(name, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                event = responses[key].get(timeout=600)
                if event["type"] == "end":
                    break
                data = base64.b64decode(event["data"])
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            self.close_connection = True
        finally:
            responses.pop(key, None)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = request


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, address):

        emit({"type": "stderr", "data": base64.b64encode(traceback.format_exc().encode()).decode()})


spec = json.loads(base64.b64decode(sys.argv[1]))
with tempfile.TemporaryDirectory(prefix="sentinel-git-broker-") as directory:
    cert = os.path.join(directory, "ca.pem")
    key = os.path.join(directory, "key.pem")
    with open(cert, "w") as f:
        f.write(spec["cert"])
    with open(key, "w") as f:
        f.write(spec["key"])
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server = Server(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=receive, daemon=True).start()
    proxy = f"http://127.0.0.1:{server.server_port}"
    env = os.environ.copy()
    # Remove inherited authentication/config; this subprocess only sees a dummy marker.
    for name in list(env):
        if name.startswith(("GIT_CONFIG", "GH_", "GITHUB_")):
            env.pop(name)
    env.update(spec.get("env", {}))
    env.update(
        {
            "HTTPS_PROXY": proxy,
            "HTTP_PROXY": proxy,
            "https_proxy": proxy,
            "http_proxy": proxy,
            "NO_PROXY": "",
            "no_proxy": "",
            "SSL_CERT_FILE": cert,
            "CURL_CA_BUNDLE": cert,
            "GIT_SSL_CAINFO": cert,
            "GIT_TERMINAL_PROMPT": "0",
            "GH_TOKEN": "sentinel-broker-placeholder",
            "GH_PROMPT_DISABLED": "1",
            "GH_HOST": spec["host"],
            "GH_ENTERPRISE_TOKEN": "sentinel-broker-placeholder",
            "GIT_CONFIG_COUNT": "5",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": "/dev/null",
            "GIT_CONFIG_KEY_2": "http.proxy",
            "GIT_CONFIG_VALUE_2": proxy,
            "GIT_CONFIG_KEY_3": f"url.https://{spec['host']}/.insteadOf",
            "GIT_CONFIG_VALUE_3": f"git@{spec['host']}:",
            "GIT_CONFIG_KEY_4": f"url.https://{spec['host']}/.insteadOf",
            "GIT_CONFIG_VALUE_4": f"ssh://git@{spec['host']}/",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GH_CONFIG_DIR": directory,
        }
    )
    if identity := spec.get("git_identity"):
        # Per-process config supplies defaults without overwriting cherry-picked authors.
        for field in ("name", "email"):
            index = int(env["GIT_CONFIG_COUNT"])
            env[f"GIT_CONFIG_KEY_{index}"] = f"user.{field}"
            env[f"GIT_CONFIG_VALUE_{index}"] = identity[field]
            env["GIT_CONFIG_COUNT"] = str(index + 1)
    try:
        process = subprocess.Popen(
            spec["argv"],
            cwd=spec["cwd"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        def output(stream, name):
            while data := stream.read1(64 * 1024):
                emit({"type": name, "data": base64.b64encode(data).decode()})

        threads = [
            threading.Thread(target=output, args=(process.stdout, "stdout")),
            threading.Thread(target=output, args=(process.stderr, "stderr")),
        ]
        for thread in threads:
            thread.start()
        code = process.wait()
        for thread in threads:
            thread.join()
    except OSError as error:
        code = 127 if isinstance(error, FileNotFoundError) else 1
        emit({"type": "stderr", "data": base64.b64encode(str(error).encode()).decode()})
    server.shutdown()
emit({"type": "exit", "code": code})
