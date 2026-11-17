"""The desktop shell: one program that runs the server and shows the dashboard in its own window.

The window (pywebview) and tray (pystray) libraries are replaced by stand-ins
that record what FETCH asks of them; the server underneath is the real one.
"""

import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from fetch_app import __main__ as cli
from fetch_app import desktop

from .conftest import make_boot


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [handler(*args) for handler in self.handlers]


class FakeWindow:
    def __init__(self, title, url, **options):
        self.title, self.url, self.options = title, url, options
        self.calls = []
        self.destroyed = threading.Event()
        self.events = type("Events", (), {})()
        for name in ("initialized", "closing", "loaded"):
            setattr(self.events, name, FakeEvent())

    def show(self):
        self.calls.append("show")

    def restore(self):
        self.calls.append("restore")

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")
        self.destroyed.set()

    def click_close(self):
        """The title bar's X: the window closes unless a handler answers False."""
        if False not in self.events.closing.fire():
            self.destroy()


class FakeWebview:
    def __init__(self, renderer="edgechromium"):
        self.settings = {}
        self.renderer = renderer
        self.window = None
        self.started = threading.Event()
        self.start_options = {}

    def create_window(self, title, url, **options):
        self.window = FakeWindow(title, url, **options)
        return self.window

    def start(self, **options):
        self.start_options = options
        if False in self.window.events.initialized.fire(self.renderer):
            return  # the window was refused before it was shown
        self.window.events.loaded.fire()
        self.started.set()
        self.window.destroyed.wait(60)


class FakeTray:
    SEPARATOR = object()

    class Menu:
        SEPARATOR = None

        def __init__(self, *items):
            self.items = items

    class MenuItem:
        def __init__(self, text, action, default=False):
            self.text, self.action, self.default = text, action, default

    def __init__(self):
        self.icon = None
        self.Menu.SEPARATOR = self.SEPARATOR

    def Icon(self, name, image, title, menu):  # noqa: N802 - mirrors pystray
        tray = self

        class Icon:
            def __init__(self):
                self.menu, self.notifications, self.detached = menu, [], False
                self.stopped = threading.Event()

            def run_detached(self):
                self.detached = True

            def run(self):
                self.stopped.wait(60)

            def stop(self):
                self.stopped.set()

            def notify(self, message, title=None):
                self.notifications.append((title, message))

            def click(self, text):
                next(i for i in self.menu.items if i is not tray.SEPARATOR and i.text == text).action(self, None)

        self.icon = Icon()
        return self.icon


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


class App:
    """FETCH started the way double-clicking FETCH.exe starts it, on a thread of the test."""

    def __init__(self, boot, webview, tray, opened):
        self.boot, self.webview, self.tray, self.opened = boot, webview, tray, opened
        self.code = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        args = argparse.Namespace(host=None, port=None)
        self.code = cli._run_desktop(self.boot, args, "", self.webview, self.tray)

    def start(self):
        self.thread.start()
        ready = self.webview.started if self.webview else threading.Event()
        assert desktop.wait_until(lambda: ready.is_set() or (self.tray and self.tray.icon and self.opened), 30)
        return self

    def control(self, path):
        control = json.loads(self.boot.control_file.read_text())
        request = urllib.request.Request(f"http://127.0.0.1:{self.boot.port}/api/control/{path}", method="POST",
                                         headers={"X-Fetch-Control": control["token"]}, data=b"")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def finish(self, timeout=40):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "FETCH did not exit"
        return self.code


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setattr("fetch_app.app.find_aria2", lambda *a, **k: None)
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    apps: list[App] = []

    def start(webview="default", tray="default", **boot_options):
        boot = make_boot(tmp_path, port=free_port(), **boot_options)
        app = App(boot, FakeWebview() if webview == "default" else webview,
                  FakeTray() if tray == "default" else tray, opened)
        apps.append(app)
        return app

    yield start
    for app in apps:
        if app.thread.is_alive():
            try:
                app.control("shutdown")
            except (OSError, ValueError):
                pass
            if app.webview and app.webview.window:
                app.webview.window.destroy()
            if app.tray and app.tray.icon:
                app.tray.icon.stop()
            app.thread.join(40)


def test_the_window_shows_the_dashboard_of_the_server_it_started(launch):
    app = launch().start()
    window = app.webview.window
    assert window.url == f"http://127.0.0.1:{app.boot.port}" and window.title == "FETCH"
    assert health(app.boot.port)["status"] == "ok", "the same program serves the dashboard"
    assert app.opened == [], "no browser"
    assert app.webview.settings["ALLOW_DOWNLOADS"] is True
    # Window data (zoom, theme) is kept between starts, next to the other data.
    assert app.webview.start_options == {
        "private_mode": False, "storage_path": str(app.boot.data_dir / "webview")}
    state = desktop.read_state(app.boot.data_dir)
    assert state["mode"] == "window" and state["visible"] is True and state["renderer"] == "edgechromium"
    assert app.tray.icon.detached
    assert [i.text for i in app.tray.icon.menu.items if i is not FakeTray.SEPARATOR] == [
        "Open FETCH", "Open downloads folder", "Quit FETCH"]

    app.tray.icon.click("Quit FETCH")
    assert app.finish() == 0
    assert "destroy" in window.calls
    assert health(app.boot.port) is None, "quitting stops the server too"
    assert app.tray.icon.stopped.is_set()
    assert desktop.read_state(app.boot.data_dir) is None
    assert not app.boot.control_file.exists()


def test_closing_the_window_keeps_fetch_running_in_the_tray(launch):
    app = launch().start()
    window, icon = app.webview.window, app.tray.icon
    window.click_close()
    assert desktop.wait_until(lambda: "hide" in window.calls, 5)
    assert "destroy" not in window.calls
    assert health(app.boot.port)["status"] == "ok", "downloads continue"
    assert desktop.wait_until(lambda: len(icon.notifications) == 1, 5)
    assert icon.notifications[0][0] == "FETCH is still running"
    assert desktop.read_state(app.boot.data_dir)["visible"] is False

    icon.click("Open FETCH")
    assert window.calls[-2:] == ["show", "restore"]
    window.click_close()
    assert desktop.wait_until(lambda: window.calls.count("hide") == 2, 5)
    time.sleep(0.2)
    assert len(icon.notifications) == 1, "the hint is shown once"
    icon.click("Quit FETCH")
    assert app.finish() == 0


def test_closing_the_window_quits_when_the_setting_is_off(launch):
    app = launch().start()
    put = urllib.request.Request(
        f"http://127.0.0.1:{app.boot.port}/api/settings", method="PUT",
        data=json.dumps({"general": {"close_to_tray": False}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(put, timeout=10) as response:
        assert response.status == 200
    app.webview.window.click_close()
    assert app.finish() == 0
    assert health(app.boot.port) is None


def test_without_a_tray_closing_the_window_quits(launch):
    app = launch(tray=None).start()
    app.webview.window.click_close()
    assert app.finish() == 0
    assert health(app.boot.port) is None


def test_starting_fetch_twice_brings_the_window_forward(launch, monkeypatch):
    app = launch().start()
    window = app.webview.window
    window.click_close()
    assert desktop.wait_until(lambda: "hide" in window.calls, 5)
    # The second start (another process in reality) finds the first and asks it.
    again = argparse.Namespace(after_update=None, headless=False, no_browser=False)
    assert cli._run(app.boot, again) == 0
    assert window.calls[-2:] == ["show", "restore"]
    assert app.opened == []
    app.tray.icon.click("Quit FETCH")
    assert app.finish() == 0


def test_window_closes_when_the_server_stops_itself(launch):
    """`FETCH.exe stop`, or an update handing over to the new version."""
    app = launch().start()
    assert app.control("shutdown") == {"ok": True}
    assert app.finish() == 0
    assert "destroy" in app.webview.window.calls
    assert app.tray.icon.stopped.is_set()


def test_without_webview2_the_dashboard_opens_in_the_browser_with_a_tray_icon(launch):
    app = launch(webview=None).start()
    assert app.opened == [f"http://127.0.0.1:{app.boot.port}"]
    assert desktop.read_state(app.boot.data_dir)["mode"] == "browser"
    assert app.control("show") == {"ok": True, "window": True}
    assert len(app.opened) == 2, "starting FETCH again opens the dashboard again"
    app.tray.icon.click("Quit FETCH")
    assert app.finish() == 0
    assert health(app.boot.port) is None


def test_an_unusable_renderer_falls_back_to_the_browser(launch, monkeypatch):
    app = launch(webview=FakeWebview(renderer="mshtml"))
    app.thread.start()
    assert desktop.wait_until(lambda: bool(app.opened), 30)
    assert desktop.wait_until(lambda: (desktop.read_state(app.boot.data_dir) or {}).get("mode") == "browser", 10)
    app.tray.icon.click("Quit FETCH")
    assert app.finish() == 0


def test_a_port_in_use_is_reported_and_nothing_is_left_running(launch, monkeypatch):
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(desktop, "show_error", lambda title, message: errors.append((title, message)))
    app = launch()
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", app.boot.port))
        blocker.listen()
        app.thread.start()
        assert app.finish() == 1
    assert app.webview.window is None, "no window without a server"
    assert len(errors) == 1 and str(app.boot.port) in errors[0][1]


def test_run_falls_back_to_the_console_when_headless(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_run_in_console", lambda boot, args, after, open_browser: calls.append(open_browser) or 0)
    monkeypatch.setattr(cli, "_run_desktop", lambda *a: calls.append("desktop") or 0)
    monkeypatch.setattr(desktop, "load_window_library", lambda: FakeWebview())
    monkeypatch.setattr(desktop, "load_tray_library", lambda: FakeTray())
    monkeypatch.setattr(cli, "_finish_update", lambda boot, version: None)
    monkeypatch.setattr(cli, "_record_update", lambda *a: None)
    boot = make_boot(tmp_path, port=free_port())

    def run(**flags):
        options = {"after_update": None, "headless": False, "no_browser": False, **flags}
        assert cli._run(boot, argparse.Namespace(**options)) == 0
        return calls.pop()

    assert run() == "desktop"
    assert run(headless=True) is False
    assert run(no_browser=True) is False, "the 1.x way to ask for no interface"
    # FETCH 1.x hands over to a new version with --no-browser: show the window.
    assert run(no_browser=True, after_update="1.1.0") == "desktop"
    assert run(headless=True, after_update="2.0.0") is False


def test_icon_is_drawn_in_every_size(tmp_path):
    from PIL import Image

    image = desktop.make_icon(64)
    assert image.size == (64, 64) and image.getpixel((32, 32))[3] == 255
    assert image.getpixel((0, 0))[3] == 0, "rounded corners are transparent"
    path = desktop.write_icon_file(tmp_path / "fetch.ico")
    with Image.open(path) as ico:
        assert {(16, 16), (32, 32), (256, 256)} <= ico.info["sizes"]
