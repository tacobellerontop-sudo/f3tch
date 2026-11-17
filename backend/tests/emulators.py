"""Local emulations of provider APIs, built from their public documentation.

These let the adapters' request construction, parsing, error mapping and
crypto be tested without any external network. They are NOT evidence that
the live services behave identically.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _dispatch(self, method: str) -> None:
        owner: Emulator = self.server.owner  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        owner.calls.append((method, parts.path, query, dict(self.headers), body))
        status, headers, payload = owner.handle(method, parts.path, query, self.headers, body)
        if isinstance(payload, (dict, list, int)):
            payload = json.dumps(payload).encode()
            headers = {"Content-Type": "application/json", **headers}
        # Range support for file payloads.
        if status == 200 and headers.pop("X-Ranges", None) and self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            headers["Content-Range"] = f"bytes {start}-{len(payload) - 1}/{len(payload)}"
            payload, status = payload[start:], 206
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")


class Emulator:
    def __init__(self) -> None:
        self.calls: list = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.owner = self  # type: ignore[attr-defined]
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def handle(self, method, path, query, headers, body):  # pragma: no cover - abstract
        raise NotImplementedError


# -- Rapidgator API v2 -----------------------------------------------------------


class RapidgatorEmulator(Emulator):
    def __init__(self) -> None:
        super().__init__()
        self.accounts = {"premium@example.com": ("premium-pass", True), "free@example.com": ("free-pass", False)}
        self.files: dict[str, tuple[str, bytes]] = {}
        self.tokens: dict[str, str] = {}
        self.delay = 0
        self.logins = 0
        self.traffic_exhausted = False
        # Free-download website state.
        self.free_seconds = 1
        self.captcha_mode = "image"  # or "widget"
        self.captcha_answer = "k7xw2"
        self.captcha_serial = 0
        self.captcha_posts: list[dict] = []
        self.premium_only: set[str] = set()
        self.display_sizes: dict[str, str] = {}
        self.site_notice = ""
        self.blocked = False
        self.site_down = False
        self.fids: dict[str, str] = {}
        self.free: dict = {}
        self.early_requests = 0
        self.free_transfers = 0

    @property
    def api_url(self) -> str:
        return f"{self.base}/api/v2"

    @staticmethod
    def _ok(response):
        return 200, {}, {"response": response, "status": 200, "details": None}

    @staticmethod
    def _fail(status, details):
        return 200, {}, {"response": None, "status": status, "details": details}

    # -- the public website: file page and free-download steps ------------------
    #
    # Modelled on Rapidgator's free flow as publicly documented: file page ->
    # AjaxStartTimer -> wait -> AjaxGetDownloadLink -> CAPTCHA -> address.
    # The emulator refuses every step that arrives early or out of order, so a
    # test passes only if FETCH really waited and really had a human answer.

    PNG = b"\x89PNG\r\n\x1a\n" + b"fake-captcha-picture"

    def _html(self, text, status=200, cookie=None):
        headers = {"Content-Type": "text/html; charset=utf-8"}
        if cookie:
            headers["Set-Cookie"] = cookie
        return status, headers, text.encode()

    def _file_page(self, file_id):
        if self.blocked:
            return self._html("<title>Just a moment...</title>", 403)
        if self.site_down:
            return self._html("<h1>503</h1>", 503)
        file = self.files.get(file_id)
        if file is None:
            return self._html("<h3>404 File not found</h3>", 404)
        name, data = file
        notice = self.site_notice
        if file_id in self.premium_only:
            notice = "This file can be downloaded by premium only"
        size = self.display_sizes.get(file_id, f"{len(data) / 1024:.2f} KB")
        page = f"""<html><head><title>Download file {name}</title></head><body>
            <div class="text-block file-descr"><p>Downloading:</p> <a href="/file/{file_id}">{name}</a>
            <div>File size: <strong>{size}</strong></div></div>
            <div class="error">{notice}</div>
            <script>var startTimer = 1; var fid = {abs(hash(file_id)) % 100000 + 1};
            var secs = {self.free_seconds}; var download_url = '/download/AjaxStartTimer';</script>
            </body></html>"""
        self.fids[str(abs(hash(file_id)) % 100000 + 1)] = file_id
        return self._html(page, cookie="PHPSESSID=visitor-1; path=/")

    def _captcha_page(self, wrong=False):
        if self.captcha_mode == "widget":
            return self._html('<form id="captchaform" method="post"><div class="g-recaptcha" '
                              'data-sitekey="site-key"></div></form>')
        self.captcha_serial += 1
        note = "The verification code is incorrect." if wrong else ""
        return self._html(f"""<p>{note}</p>
            <form id="captchaform" action="/download/captcha" method="post">
            <input type="hidden" name="DownloadCaptchaForm[captcha]" value="">
            <input type="hidden" name="serial" value="{self.captcha_serial}">
            <img src="/captcha/image?v={self.captcha_serial}" alt="captcha">
            <input type="text" name="DownloadCaptchaForm[verifyCode]">
            </form>""")

    def _site(self, method, path, query, headers, body):
        if path == "/":
            return self._html("<html>Rapidgator</html>", 503 if self.site_down else 200)
        if path.startswith("/file/"):
            return self._file_page(path.split("/")[2])
        if "PHPSESSID=visitor-1" not in (headers.get("Cookie") or ""):
            return self._html("no session", 403)
        if path == "/download/AjaxStartTimer":
            file_id = self.fids.get(query.get("fid", ""))
            if file_id is None:
                return 200, {}, {"state": "error", "code": "wrong fid"}
            self.free = {"file_id": file_id, "started": time.monotonic(), "stage": "timer"}
            return 200, {}, {"state": "started", "sid": "sid-1"}
        if path == "/download/AjaxGetDownloadLink":
            if query.get("sid") != "sid-1" or self.free.get("stage") != "timer":
                return 200, {}, {"state": "error", "code": "wrong sid"}
            if time.monotonic() - self.free["started"] < self.free_seconds:
                self.early_requests += 1
                return 200, {}, {"state": "error", "code": "timer is not finished"}
            self.free["stage"] = "captcha"
            return 200, {}, {"state": "done"}
        if path == "/captcha/image":
            return 200, {"Content-Type": "image/png"}, self.PNG
        if path == "/download/captcha":
            if self.free.get("stage") != "captcha":
                return self._html("out of order", 500)
            if method == "GET":
                return self._captcha_page()
            form = {k: v[0] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
            self.captcha_posts.append(form)
            if form.get("DownloadCaptchaForm[verifyCode]") != self.captcha_answer:
                return self._captcha_page(wrong=True)
            self.free["stage"] = "issued"
            return self._html(f"<script>function getUrl() {{ return '{self.free_link(self.free['file_id'])}'; }}</script>")
        return None

    def free_link(self, file_id):
        return f"{self.base}/download/free/{file_id}"

    def handle(self, method, path, query, headers, body):
        if path.startswith("/download/free/"):
            self.free_transfers += 1
            name, data = self.files[path.split("/")[3]]
            return 200, {}, data
        if not path.startswith(("/api/", "/dl/")):
            answer = self._site(method, path, query, headers, body)
            if answer is not None:
                return answer
        if path.startswith("/dl/"):
            name, data = self.files[path.split("/")[2]]
            return 200, {"X-Ranges": "1", "Accept-Ranges": "bytes"}, data
        if path == "/api/v2/user/login":
            account = self.accounts.get(query.get("login", ""))
            if not account or account[0] != query.get("password"):
                return self._fail(401, "Error: Wrong e-mail or password.")
            self.logins += 1
            token = f"session-{self.logins}"
            self.tokens[token] = query["login"]
            return self._ok({"token": token, "user": {"is_premium": account[1], "traffic": {"left": "1000000"}}})
        login = self.tokens.get(query.get("token", ""))
        if login is None:
            return self._fail(401, "Error: Session not exist.")
        file = self.files.get(query.get("file_id", ""))
        if file is None:
            return self._fail(404, "Error: File not found")
        name, data = file
        if path == "/api/v2/file/info":
            return self._ok({"file": {"name": name, "size": len(data), "hash": hashlib.md5(data).hexdigest()}})
        if path == "/api/v2/file/download":
            if not self.accounts[login][1]:
                return self._fail(403, "Error: This file can be downloaded by premium only.")
            if self.traffic_exhausted:
                return self._fail(403, "Error: Daily traffic limit exceeded.")
            return self._ok({"download_url": f"{self.base}/dl/{query['file_id']}", "delay": self.delay})
        return self._fail(404, "Error: Unknown method")


# -- MEGA ------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def reference_mega_mac(key: bytes, nonce: bytes, plain: bytes) -> bytes:
    """Straightforward block-by-block reference of MEGA's chunked CBC-MAC."""
    ecb = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    sizes = [0x20000 * i for i in range(1, 9)]
    file_mac = b"\0" * 16
    position, index = 0, 0
    while position < len(plain):
        size = sizes[index] if index < len(sizes) else 0x100000
        chunk = plain[position : position + size]
        position += size
        index += 1
        mac = nonce + nonce
        for offset in range(0, len(chunk), 16):
            block = chunk[offset : offset + 16].ljust(16, b"\0")
            mac = ecb.update(_xor(mac, block))
        file_mac = ecb.update(_xor(file_mac, mac))
    w = struct.unpack(">4I", file_mac)
    return struct.pack(">2I", w[0] ^ w[1], w[2] ^ w[3])


class MegaEmulator(Emulator):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, dict] = {}
        self.quota_seconds = 0
        self.corrupt = False

    @property
    def api_url(self) -> str:
        return f"{self.base}/cs"

    def publish(self, handle: str, name: str, plain: bytes) -> str:
        """Encrypt ``plain`` the way MEGA does and return the public link."""
        import os

        key, nonce = os.urandom(16), os.urandom(8)
        meta_mac = reference_mega_mac(key, nonce, plain)
        ctr = Cipher(algorithms.AES(key), modes.CTR(nonce + b"\0" * 8)).encryptor()
        encrypted = ctr.update(plain)
        attributes = b"MEGA" + json.dumps({"n": name}).encode()
        attributes += b"\0" * (-len(attributes) % 16)
        cbc = Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor()
        link_key = _xor(key, nonce + meta_mac) + nonce + meta_mac
        self.files[handle] = {"at": _b64(cbc.update(attributes)), "data": encrypted, "size": len(plain)}
        return f"https://mega.nz/file/{handle}#{_b64(link_key)}"

    def handle(self, method, path, query, headers, body):
        if path.startswith("/dl/"):
            if self.quota_seconds:
                return 509, {"X-MEGA-Time-Left": str(self.quota_seconds)}, b""
            data = self.files[path.split("/")[2]]["data"]
            if self.corrupt:
                data = data[:-1] + bytes([data[-1] ^ 0xFF])
            return 200, {"X-Ranges": "1"}, data
        commands = json.loads(body or b"[]")
        if not commands:
            return 200, {}, []
        file = self.files.get(commands[0].get("p"))
        if file is None:
            return 200, {}, [-9]
        reply = {"s": file["size"], "at": file["at"]}
        if commands[0].get("g"):
            reply["g"] = f"{self.base}/dl/{commands[0]['p']}"
        return 200, {}, [reply]


# -- Pixeldrain ------------------------------------------------------------------


class PixeldrainEmulator(Emulator):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, tuple[str, bytes]] = {}
        self.captcha = False
        self.api_key = "pd-key"

    @property
    def api_url(self) -> str:
        return f"{self.base}/api"

    def handle(self, method, path, query, headers, body):
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "user"]:
            expected = "Basic " + base64.b64encode(f":{self.api_key}".encode()).decode()
            if headers.get("Authorization") == expected:
                return 200, {}, {"username": "tester"}
            return 401, {}, {"success": False, "value": "authentication_required"}
        file = self.files.get(parts[2]) if len(parts) > 2 else None
        if file is None:
            return 404, {}, {"success": False, "value": "not_found"}
        name, data = file
        if parts[-1] == "info":
            return 200, {}, {
                "id": parts[2], "name": name, "size": len(data),
                "hash_sha256": hashlib.sha256(data).hexdigest(),
                "availability": "file_rate_limited_captcha_required" if self.captcha else "",
            }
        return 200, {"X-Ranges": "1"}, data


# -- Google Drive ----------------------------------------------------------------


class DriveEmulator(Emulator):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, dict] = {}
        self.api_key = "drive-key"
        self.public_key = None
        self.access_token = "ya29.emulated"
        self.quota_exceeded = False

    @property
    def api_url(self) -> str:
        return f"{self.base}/drive/v3"

    @property
    def token_url(self) -> str:
        return f"{self.base}/token"

    def add(self, file_id: str, name: str, data: bytes, mime: str = "application/zip") -> None:
        self.files[file_id] = {"name": name, "data": data, "mime": mime}

    def _authorized(self, headers) -> bool:
        return headers.get("X-Goog-Api-Key") == self.api_key or headers.get(
            "Authorization"
        ) == f"Bearer {self.access_token}"

    def handle(self, method, path, query, headers, body):
        if path == "/token":
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            head, claims, signature = form["assertion"].split(".")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
            try:
                self.public_key.verify(pad(signature), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            except Exception:
                return 400, {}, {"error": "invalid_grant"}
            self.claims = json.loads(pad(claims))
            return 200, {}, {"access_token": self.access_token, "expires_in": 3600}
        if not self._authorized(headers):
            return 400, {}, {"error": {"code": 400, "errors": [{"reason": "keyInvalid"}]}}
        if path == "/drive/v3/about":
            return 200, {}, {"user": {"emailAddress": "svc@project.iam.gserviceaccount.com"}}
        file = self.files.get(path.rsplit("/", 1)[-1])
        if file is None:
            return 404, {}, {"error": {"code": 404, "errors": [{"reason": "notFound"}]}}
        if query.get("alt") == "media":
            if self.quota_exceeded:
                return 403, {}, {"error": {"code": 403, "errors": [{"reason": "downloadQuotaExceeded"}]}}
            return 200, {"X-Ranges": "1"}, file["data"]
        meta = {"id": path.rsplit("/", 1)[-1], "name": file["name"], "mimeType": file["mime"]}
        if not file["mime"].startswith("application/vnd.google-apps"):
            meta["size"] = str(len(file["data"]))
            meta["md5Checksum"] = hashlib.md5(file["data"]).hexdigest()
        return 200, {}, meta
