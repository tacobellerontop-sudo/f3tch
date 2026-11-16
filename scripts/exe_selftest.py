"""End-to-end check of the single-file executable, using only the standard library.

    python scripts/exe_selftest.py dist/FETCH.exe

Copies the executable into an empty folder and then, like a person would:
starts it, opens the dashboard, creates the administrator with the setup
token, asks for updates, installs one, and stops FETCH. The "update" is
served by a local stand-in for GitHub's releases API that offers the same
executable as version 99.0.0, so the whole self-replacement runs for real:
download, checksum, swap, restart.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASSWORD = "selftest-Passw0rd-42"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fake_github(exe_bytes: bytes) -> tuple[ThreadingHTTPServer, str]:
    digest = hashlib.sha256(exe_bytes).hexdigest()
    files = {"FETCH.exe": exe_bytes, "FETCH.exe.sha256": f"{digest}  FETCH.exe\n".encode()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path == "/repos/example/fetch/releases/latest":
                body = json.dumps({
                    "tag_name": "v99.0.0", "name": "FETCH 99.0.0", "body": "Self-test release.",
                    "published_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://github.com/example/fetch/releases/tag/v99.0.0",
                    "assets": [{"name": name, "browser_download_url": f"{base}/download/{name}"} for name in files],
                }).encode()
                kind = "application/json"
            elif self.path.startswith("/download/") and self.path[10:] in files:
                body, kind = files[self.path[10:]], "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class Browser:
    """A cookie-keeping client that sends the CSRF token like the dashboard does."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.csrf = ""
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body=None, timeout: float = 300):
        data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
        headers = {"Content-Type": "application/json", "Origin": self.base}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise AssertionError(f"{method} {path} -> {error.code}: {error.read().decode(errors='replace')}") from None
        return json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode(errors="replace")


def health(base: str):
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=3) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


def wait_for(what: str, check, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {what}.")


def main() -> int:
    source = Path(sys.argv[1]).resolve()
    home = Path(tempfile.mkdtemp(prefix="fetch-selftest-"))
    exe = home / source.name
    shutil.copy2(source, exe)
    github, github_url = fake_github(source.read_bytes())
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("FETCH_"):
            del env[name]
    env.update({
        "FETCH_PORT": str(port), "FETCH_UPDATE_REPO": "example/fetch", "FETCH_UPDATE_API": github_url,
        "FETCH_SSRF_ALLOWLIST": "127.0.0.1",
    })

    def cli(*args: str) -> str:
        result = subprocess.run([str(exe), *args], env=env, cwd=home, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise AssertionError(f"FETCH {' '.join(args)} exited with {result.returncode}:\n{result.stdout}\n{result.stderr}")
        return result.stdout

    log = open(home / "selftest-console.log", "wb")
    process = subprocess.Popen([str(exe), "run", "--no-browser"], env=env, cwd=home, stdout=log, stderr=subprocess.STDOUT)
    try:
        print("1. Starting from an empty folder")
        first = wait_for("FETCH to start", lambda: health(base), 120)
        assert first["status"] == "ok", first
        for name in ("config/fetch.toml", "data/fetch.db", "storage", "logs"):
            assert (home / name).exists(), f"{name} was not created next to the executable"
        page = Browser(base).call("GET", "/")
        assert '<div id="root"' in page, "the dashboard was not served from the executable"

        print("2. First-run setup with the token")
        status = json.loads(cli("status"))
        assert status["running"] and status["setup_required"] and status["setup_token"], status
        browser = Browser(base)
        me = browser.call("POST", "/api/setup", {"setup_token": status["setup_token"], "username": "admin", "password": PASSWORD})
        browser.csrf = me["csrf_token"]

        print("3. Checking for updates")
        update = browser.call("GET", "/api/admin/update?refresh=true")
        assert update["source"] == "exe", update
        assert update["update_available"] and update["latest_version"] == "99.0.0", update
        assert update["package_available"] and update["can_apply"], update
        before = json.loads((home / "data" / "control.json").read_text())["pid"]

        print("4. Installing the update")
        assert browser.call("POST", "/api/admin/update/apply") == {"ok": True}

        def handed_over():
            try:
                control = json.loads((home / "data" / "control.json").read_text())
            except (OSError, ValueError):
                return None
            return control["pid"] != before and health(base)

        wait_for("the new executable to take over", handed_over, 180)
        process.wait(timeout=60)
        run = wait_for(
            "the update to be recorded",
            lambda: (lambda r: r if r.get("state") != "running" else None)(
                json.loads((home / "data" / "update-status.json").read_text())),
            60,
        )
        assert run["state"] == "succeeded", run
        assert any((home / "data" / "backups").iterdir()), "no database backup was made"
        assert not exe.with_name(exe.name + ".new").exists()
        again = Browser(base)
        me = again.call("POST", "/api/auth/login", {"username": "admin", "password": PASSWORD})
        again.csrf = me["csrf_token"]
        assert again.call("GET", "/api/admin/update")["last_run"]["state"] == "succeeded"

        print("5. Stopping")
        cli("stop")
        wait_for("FETCH to stop", lambda: health(base) is None, 60)
        print("Self-test passed.")
        return 0
    except BaseException:
        log.flush()
        for name in ("selftest-console.log", "logs/update.log", "logs/fetch.log"):
            path = home / name
            if path.is_file():
                print(f"---- {name}")
                print(path.read_text(errors="replace")[-6000:])
        raise
    finally:
        if process.poll() is None:
            process.kill()
        try:
            subprocess.run([str(exe), "stop"], env=env, cwd=home, capture_output=True, timeout=60)
        except Exception:
            pass
        github.shutdown()
        log.close()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
