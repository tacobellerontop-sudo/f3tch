"""End-to-end check of the single-file executable, using only the standard library.

    python scripts/exe_selftest.py dist/FETCH.exe
    python scripts/exe_selftest.py dist/FETCH.exe --desktop

Copies the executable into an empty folder and then, like a person would:
starts it, opens the dashboard, checks that only this computer is answered,
asks for updates, installs one, and stops FETCH. It runs with `--headless`
(no window or tray icon), since a CI runner has no desktop. The "update" is
served by a local stand-in for GitHub's releases API that offers the same
executable as version 99.0.0, so the whole self-replacement runs for real:
download, checksum, swap, restart.
"""

from __future__ import annotations

import hashlib
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
    """Calls the API the way the dashboard does (same origin, no sign-in)."""

    def __init__(self, base: str) -> None:
        self.base = base

    def call(self, method: str, path: str, body=None, timeout: float = 300, headers=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
        headers = {"Content-Type": "application/json", "Origin": self.base, **(headers or {})}
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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


def clean_env(port: int) -> dict:
    env = {name: value for name, value in os.environ.items() if not name.startswith("FETCH_")}
    env["FETCH_PORT"] = str(port)
    return env


def desktop_check(source: Path) -> int:
    home = Path(tempfile.mkdtemp(prefix="fetch-desktop-"))
    exe = home / source.name
    shutil.copy2(source, exe)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = clean_env(port)
    state_file = home / "data" / "window.json"

    def state():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    process = subprocess.Popen([str(exe)], env=env, cwd=home)
    try:
        print("1. Double-click: the server starts inside the app")
        wait_for("FETCH to start", lambda: health(base), 120)
        print("2. The dashboard opens in FETCH's own window")
        shown = wait_for("the window to load the dashboard", lambda: (lambda s: s if s and s.get("visible") else None)(state()), 120)
        assert shown["mode"] == "window" and shown["renderer"] == "edgechromium", shown
        print("3. Starting it again brings the same window forward")
        second = subprocess.run([str(exe), "run"], env=env, cwd=home, capture_output=True, text=True, timeout=60)
        assert second.returncode == 0, second
        assert process.poll() is None, "the first FETCH stopped"
        print("4. Stopping closes the window and ends the program")
        subprocess.run([str(exe), "stop"], env=env, cwd=home, capture_output=True, timeout=60)
        process.wait(timeout=60)
        assert health(base) is None
        print("Desktop check passed.")
        return 0
    except BaseException:
        for name in ("logs/fetch.log",):
            path = home / name
            if path.is_file():
                print(f"---- {name}")
                print(path.read_text(errors="replace")[-6000:])
        raise
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    source = Path(sys.argv[1]).resolve()
    if "--desktop" in sys.argv[2:]:
        return desktop_check(source)
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
    process = subprocess.Popen([str(exe), "run", "--headless"], env=env, cwd=home, stdout=log, stderr=subprocess.STDOUT)
    try:
        print("1. Starting from an empty folder")
        first = wait_for("FETCH to start", lambda: health(base), 120)
        assert first["status"] == "ok", first
        for name in ("config/fetch.toml", "data/fetch.db", "storage", "logs"):
            assert (home / name).exists(), f"{name} was not created next to the executable"
        page = Browser(base).call("GET", "/")
        assert '<div id="root"' in page, "the dashboard was not served from the executable"

        print("2. Opening the dashboard without an account")
        status = json.loads(cli("status"))
        assert status["running"] and status["bind_address"] == "127.0.0.1", status
        browser = Browser(base)
        assert browser.call("GET", "/api/bootstrap")["version"], "the dashboard could not load"
        for refused in (
            lambda: browser.call("GET", "/api/bootstrap", headers={"Host": "evil.example"}),
            lambda: browser.call("POST", "/api/files/folder", {"name": "x"}, headers={"Origin": "http://evil.example"}),
        ):
            try:
                refused()
            except AssertionError as error:
                assert "-> 403" in str(error), error
            else:
                raise AssertionError("a request from another site or host name was answered")
        media = browser.call("POST", "/api/providers/media/test")
        assert "yt-dlp" in media["message"] and "Deno was not found" not in media["message"], media

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
