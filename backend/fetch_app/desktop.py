"""The desktop shell: FETCH in a window of its own, with an icon in the notification area.

``FETCH.exe`` is one program. It starts the download server on a background
thread and shows the dashboard in a native window (Microsoft Edge WebView2,
which Windows 10 and 11 ship). Closing the window keeps FETCH downloading in
the notification area (unless that is switched off in Settings); "Quit FETCH"
in the icon's menu stops everything. If the server stops on its own, to hand
over to an update or because ``FETCH.exe stop`` was run, the window goes with
it.

The window and tray libraries are imported lazily and passed in, so the logic
here runs (and is tested) on systems that have neither.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("fetch.desktop")

WINDOW_SIZE = (1280, 820)
WINDOW_MIN_SIZE = (940, 600)
BACKGROUND = "#16181d"
ACCENT = "#7c93ff"
LIGHT = "#e7e9ee"
#: Internet Explorer's engine, which pywebview falls back to, cannot run the dashboard.
UNUSABLE_RENDERERS = ("mshtml",)
#: Registry entry of the WebView2 runtime.
WEBVIEW2_CLIENTS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
)


# -- what this computer can show ---------------------------------------------------


def webview2_version() -> str:
    """Version of the installed WebView2 runtime, or '' if there is none."""
    if os.name != "nt":
        return ""
    import winreg

    locations = []
    for client in WEBVIEW2_CLIENTS:
        locations += [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client}"),
        ]
    for root, path in locations:
        try:
            with winreg.OpenKey(root, path) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0])
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return version
    return ""


def load_window_library() -> Any | None:
    """pywebview, if a window can be shown with it on this computer."""
    wanted = os.name == "nt" or os.environ.get("FETCH_DESKTOP") == "1"
    if not wanted or os.environ.get("FETCH_DESKTOP") == "0":
        return None
    if os.name == "nt" and not webview2_version():
        log.warning("The WebView2 runtime is not installed; the dashboard opens in the browser instead")
        return None
    try:
        import webview
    except Exception:  # not installed (running from source), or its .NET bridge failed to load
        log.warning("The window library could not be loaded", exc_info=True)
        return None
    return webview


def load_tray_library() -> Any | None:
    """pystray, where a notification area exists."""
    if os.name != "nt" and os.environ.get("FETCH_DESKTOP") != "1":
        return None
    try:
        import pystray
    except Exception:
        log.warning("The tray icon library could not be loaded", exc_info=True)
        return None
    return pystray


def make_icon(size: int = 64) -> Any:
    """The FETCH icon (the same drawing as the dashboard's favicon) as a PIL image."""
    from PIL import Image, ImageDraw

    scale = 8  # draw large, then shrink: smooth edges without an SVG renderer
    unit = size * scale / 32
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def line(points: list[tuple[float, float]], colour: str) -> None:
        width = round(2.4 * unit)
        scaled = [(x * unit, y * unit) for x, y in points]
        draw.line(scaled, fill=colour, width=width, joint="curve")
        for x, y in scaled:  # round caps
            draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=colour)

    draw.rounded_rectangle((0, 0, size * scale - 1, size * scale - 1), radius=round(8 * unit), fill=BACKGROUND)
    line([(16, 7), (16, 19)], ACCENT)
    line([(11, 14), (16, 19), (21, 14)], ACCENT)
    line([(9.5, 24.5), (22.5, 24.5)], LIGHT)
    return image.resize((size, size), Image.LANCZOS)


def write_icon_file(path: Path) -> Path:
    """Write the icon as a Windows .ico with the usual sizes (used by the build)."""
    make_icon(256).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


def show_error(title: str, message: str) -> None:
    """Tell the person something went wrong when there is no console to print to."""
    print(f"{title}: {message}", file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
        except Exception:
            pass


# -- the shell -----------------------------------------------------------------------


class Backend:
    """The download server as the shell sees it."""

    def __init__(self, serve: Callable[..., int]) -> None:
        self._serve = serve
        self.server: Any = None
        self.ctx: Any = None
        self.url = ""
        self.code: int | None = None
        self.error = ""
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fetch-server", daemon=True)

    def _run(self) -> None:
        try:
            self.code = self._serve(on_server=self._on_server, on_started=self._on_started, on_error=self._on_error)
        except BaseException as exc:  # never leave the window without its server silently
            log.exception("The server thread crashed")
            self.code, self.error = 1, self.error or str(exc)
        finally:
            self.stopped.set()
            self.ready.set()

    def _on_server(self, server: Any, ctx: Any) -> None:
        self.server, self.ctx = server, ctx

    def _on_started(self, url: str) -> None:
        self.url = url
        self.ready.set()

    def _on_error(self, message: str) -> None:
        self.error = message

    def start(self, timeout: float = 90.0) -> bool:
        """Start the server; True once it accepts connections."""
        self._thread.start()
        self.ready.wait(timeout)
        return bool(self.url) and not self.stopped.is_set()

    def stop(self, timeout: float = 40.0) -> None:
        """Ask the server to stop and wait: running downloads are saved first."""
        if self.server is not None:
            self.server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)

    def setting(self, section: str, name: str, default: Any) -> Any:
        try:
            return getattr(getattr(self.ctx.settings.get(), section), name)
        except Exception:
            return default


class DesktopShell:
    def __init__(
        self,
        backend: Backend,
        *,
        webview: Any | None,
        pystray: Any | None,
        data_dir: Path,
        storage_dir: Path | None = None,
        open_browser: Callable[[str], None] | None = None,
        open_folder: Callable[[Path], None] | None = None,
        icon_image: Callable[[], Any] = make_icon,
    ) -> None:
        self.backend = backend
        self.webview = webview
        self.pystray = pystray
        self.data_dir = data_dir
        self.storage_dir = storage_dir
        self.open_browser = open_browser or (lambda url: None)
        self.open_folder = open_folder or _open_folder
        self.icon_image = icon_image
        self.window: Any = None
        self.tray: Any = None
        self.renderer = ""
        self._quitting = threading.Event()
        self._hint_shown = False
        self._lock = threading.Lock()

    # -- state file: lets `FETCH.exe status` and the self-test see the window ---------

    @property
    def state_file(self) -> Path:
        return self.data_dir / "window.json"

    def _write_state(self, **values: Any) -> None:
        try:
            self.state_file.write_text(json.dumps({"pid": os.getpid(), **values}), encoding="utf-8")
        except OSError:
            pass

    def _clear_state(self) -> None:
        try:
            self.state_file.unlink()
        except OSError:
            pass

    # -- actions (called from the tray, the window, the server; any thread) -----------

    def show(self) -> None:
        """Bring the dashboard to the front: the window, or the browser without one."""
        window = self.window
        if window is None:
            self.open_browser(self.backend.url)
            return
        try:
            window.show()
            window.restore()
        except Exception:
            log.warning("The window could not be shown", exc_info=True)

    def quit(self) -> None:
        """Stop FETCH completely."""
        with self._lock:
            if self._quitting.is_set():
                return
            self._quitting.set()
        window = self.window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                log.warning("The window could not be closed", exc_info=True)
        else:
            self._stop_tray()

    def _on_closing(self) -> bool | None:
        """The window's close button. Returning False keeps the window (hidden)."""
        if self._quitting.is_set() or self.tray is None:
            return None
        if not self.backend.setting("general", "close_to_tray", True):
            return None
        # Hide from another thread: this handler runs inside the window's own loop.
        threading.Thread(target=self._hide, name="fetch-hide", daemon=True).start()
        return False

    def _hide(self) -> None:
        try:
            self.window.hide()
        except Exception:
            log.warning("The window could not be hidden", exc_info=True)
            return
        self._write_state(mode="window", renderer=self.renderer, visible=False)
        if not self._hint_shown and self.tray is not None:
            self._hint_shown = True
            try:
                self.tray.notify(
                    "Downloads continue in the background. Use the FETCH icon near the clock to "
                    "open the window again or to quit.",
                    "FETCH is still running",
                )
            except Exception:
                pass

    def _watch_backend(self) -> None:
        """When the server stops on its own (update, `stop` command), close everything."""
        self.backend.stopped.wait()
        if not self._quitting.is_set():
            log.info("The server stopped; closing the window")
            self.quit()

    # -- tray -------------------------------------------------------------------------

    def _start_tray(self) -> None:
        if self.pystray is None:
            return
        tray = self.pystray
        try:
            items = [tray.MenuItem("Open FETCH", lambda *_: self.show(), default=True)]
            if self.storage_dir is not None:
                items.append(tray.MenuItem("Open downloads folder", lambda *_: self.open_folder(self.storage_dir)))
            items += [tray.Menu.SEPARATOR, tray.MenuItem("Quit FETCH", lambda *_: self.quit())]
            self.tray = tray.Icon("FETCH", self.icon_image(), "FETCH", tray.Menu(*items))
            if self.window is not None:
                self.tray.run_detached()
        except Exception:
            log.warning("The tray icon could not be created", exc_info=True)
            self.tray = None

    def _stop_tray(self) -> None:
        tray, self.tray = self.tray, None
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass

    # -- running ----------------------------------------------------------------------

    def run(self, title: str = "FETCH", start_path: str = "") -> int:
        """Show the dashboard and block until FETCH is quit. Returns the exit code."""
        backend = self.backend
        backend.ctx.show_window = self.show
        threading.Thread(target=self._watch_backend, name="fetch-watch", daemon=True).start()
        try:
            if self.webview is not None and self._run_window(title, backend.url + start_path):
                pass
            else:
                self._run_without_window(backend.url + start_path)
        finally:
            self._quitting.set()
            self.window = None
            self._stop_tray()
            self._clear_state()
            backend.stop()
        return backend.code if backend.code is not None else 0

    def _run_window(self, title: str, url: str) -> bool:
        """The normal case. False if no usable window could be made."""
        webview = self.webview
        usable = True

        def on_initialized(renderer: str) -> bool | None:
            nonlocal usable
            self.renderer = str(renderer)
            if self.renderer in UNUSABLE_RENDERERS:
                usable = False
                return False
            return None

        try:
            webview.settings["ALLOW_DOWNLOADS"] = True  # saving a file from the Files page
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            self.window = webview.create_window(
                title, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
                background_color=BACKGROUND, text_select=True, zoomable=True,
            )
            self.window.events.initialized += on_initialized
            self.window.events.closing += self._on_closing
            self.window.events.loaded += lambda: self._write_state(
                mode="window", renderer=self.renderer, visible=True)
            self._start_tray()
            storage = self.data_dir / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            # Blocks until the window is destroyed. Its data (theme, zoom) is kept.
            webview.start(private_mode=False, storage_path=str(storage))
        except Exception:
            log.exception("The window could not be opened")
            usable = self._quitting.is_set()
        finally:
            self.window = None
        if not usable:
            self._stop_tray()
        return usable

    def _run_without_window(self, url: str) -> None:
        """No WebView2: the dashboard opens in the browser, the tray icon keeps FETCH reachable."""
        if self._quitting.is_set():
            return
        self._start_tray()
        self._write_state(mode="browser", renderer="", visible=False)
        self.open_browser(url)
        if self.tray is not None:
            self.tray.run()  # blocks until "Quit FETCH"
        else:
            try:
                while not self._quitting.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass


def _open_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
    except OSError:
        log.warning("The folder could not be opened", exc_info=True)


def read_state(data_dir: Path) -> dict | None:
    """What the running FETCH wrote about its window, if it has one."""
    try:
        state = json.loads((data_dir / "window.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def attach_console() -> None:
    """Give the windowed FETCH.exe somewhere to print when it was started from a terminal.

    A windowed program has no console of its own. For commands such as
    ``FETCH.exe status`` the output goes to a pipe or file if one was given,
    else to the terminal that started it.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        attached = False
        for name, number in (("stdout", -11), ("stderr", -12)):
            if getattr(sys, name) is not None:
                continue
            handle = kernel32.GetStdHandle(number)
            stream = None
            if handle and handle != ctypes.c_void_p(-1).value and kernel32.GetFileType(ctypes.c_void_p(handle)):
                try:
                    stream = open(msvcrt.open_osfhandle(handle, 0), "w", encoding="utf-8",  # noqa: SIM115
                                  errors="replace", buffering=1, closefd=False)
                except OSError:
                    stream = None
            if stream is None:
                if not attached:
                    attached = bool(kernel32.AttachConsole(-1))  # the parent's console
                if attached:
                    try:
                        stream = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
                    except OSError:
                        stream = None
            if stream is not None:
                setattr(sys, name, stream)
    except Exception:
        pass


def wait_until(check: Callable[[], bool], seconds: float, step: float = 0.05) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(step)
    return check()
