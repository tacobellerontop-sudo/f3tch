"""A controllable local HTTP file server for integration tests."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, method: str) -> None:
        server: FileServer = self.server.owner  # type: ignore[attr-defined]
        path = urlsplit(self.path).path
        parts = [unquote(p) for p in path.strip("/").split("/")]
        kind, name = parts[0], "/".join(parts[1:])
        server.hits[f"{method} {kind}"] += 1
        server.requests.append((method, path, dict(self.headers)))

        if kind == "status":
            code = int(name)
            self.send_response(code)
            if code in (429, 503) and server.retry_after:
                self.send_header("Retry-After", str(server.retry_after))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if kind == "redirect":
            self.send_response(302)
            self.send_header("Location", f"/file/{name}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if kind == "redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://10.255.255.1/secret.txt")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = server.files.get(name)
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if kind == "nohead" and method == "HEAD":
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        etag = '"' + hashlib.md5(data).hexdigest() + '"'
        start, end = 0, len(data) - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header and kind != "norange":
            if_range = self.headers.get("If-Range")
            if if_range is None or if_range == etag:
                spec = range_header.split("=", 1)[1]
                first, _, last = spec.partition("-")
                start = int(first)
                end = int(last) if last else len(data) - 1
                if start >= len(data):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        body = data[start : end + 1]
        self.send_response(status)
        if kind != "norange":
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        if kind == "cd":
            self.send_header("Content-Disposition", f'attachment; filename="{server.disposition}"')
        if kind == "html":
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method == "HEAD":
            return
        try:
            if kind == "slow":
                step = 16 * 1024
                for offset in range(0, len(body), step):
                    self.wfile.write(body[offset : offset + step])
                    self.wfile.flush()
                    time.sleep(server.slow_delay)
            elif kind == "flaky" and server.flaky_budget.get(name, 1) > 0:
                server.flaky_budget[name] = server.flaky_budget.get(name, 1) - 1
                half = body[: len(body) // 2]
                for offset in range(0, len(half), 16 * 1024):
                    self.wfile.write(half[offset : offset + 16 * 1024])
                    self.wfile.flush()
                    time.sleep(0.01)
                time.sleep(0.3)  # let the client consume what was sent
                self.close_connection = True
                self.connection.shutdown(2)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def do_GET(self) -> None:
        self._send("GET")

    def do_HEAD(self) -> None:
        self._send("HEAD")


class FileServer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.hits: Counter = Counter()
        self.requests: list[tuple[str, str, dict]] = []
        self.flaky_budget: dict[str, int] = {}
        self.slow_delay = 0.02
        self.retry_after = 0
        self.disposition = "named.bin"
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.owner = self  # type: ignore[attr-defined]
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> "FileServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def url(self, kind: str, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{kind}/{name}"

    def add(self, name: str, data: bytes) -> bytes:
        self.files[name] = data
        return data
