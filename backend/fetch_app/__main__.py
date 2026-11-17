"""Command line entry point: ``python -m fetch_app <command>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from . import __version__
from .config import BootConfig, load_config, write_initial_config


def _init(boot: BootConfig, args: argparse.Namespace) -> int:
    from .db import run_migrations

    created = write_initial_config(boot, port=args.port)
    boot = load_config(boot.home, boot.config_file)
    boot.ensure_dirs()
    run_migrations(boot.database_url)
    from .storage import StorageManager

    StorageManager(boot.storage_root).ensure()
    print(f"Configuration: {boot.config_file} ({'created' if created else 'kept'})")
    print(f"Database:      {boot.database_path}")
    print(f"Storage root:  {boot.storage_root}")
    return 0


def _status(boot: BootConfig, _args: argparse.Namespace) -> int:
    from .db import Database, run_migrations
    from .desktop import read_state
    from .settings_service import SettingsService

    run_migrations(boot.database_url)
    db = Database(boot.database_url)
    host, port = SettingsService(db, boot).effective_bind()
    running = None
    try:
        control = json.loads(boot.control_file.read_text(encoding="utf-8"))
        url = f"http://{_local_host(control.get('host', ''))}:{control['port']}/api/health"
        with urllib.request.urlopen(url, timeout=3) as resp:
            running = resp.status == 200
        if running:  # report what the running server actually listens on
            host, port = control.get("host", host), control["port"]
    except (OSError, ValueError, KeyError):
        running = False
    print(json.dumps({
        "version": __version__, "running": running, "bind_address": host, "port": port,
        "window": read_state(boot.data_dir) if running else None,
        "update_repository": boot.update_repository,
    }))
    return 0


def _local_host(host: str) -> str:
    """Address on which this machine can reach a server bound to ``host``."""
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    return f"[{host}]" if ":" in host else host


def _stop(boot: BootConfig, _args: argparse.Namespace) -> int:
    try:
        control = json.loads(boot.control_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("FETCH does not appear to be running.")
        return 1
    request = urllib.request.Request(
        f"http://{_local_host(control.get('host', ''))}:{control['port']}/api/control/shutdown",
        method="POST", headers={"X-Fetch-Control": control["token"]}, data=b"",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except (urllib.error.URLError, OSError) as exc:
        print(f"Could not reach FETCH to stop it: {exc}")
        return 1
    print("Shutdown requested. Active downloads are saved and resume on the next start.")
    return 0


def _is_running(boot: BootConfig) -> str | None:
    """URL of the server that already runs from this folder, if any."""
    try:
        control = json.loads(boot.control_file.read_text(encoding="utf-8"))
        base = f"http://{_local_host(control.get('host', ''))}:{control['port']}"
        with urllib.request.urlopen(f"{base}/api/health", timeout=3) as resp:
            return base if resp.status == 200 else None
    except (OSError, ValueError, KeyError):
        return None


def _show_running(boot: BootConfig) -> bool:
    """Ask the FETCH that already runs from this folder to show its window."""
    try:
        control = json.loads(boot.control_file.read_text(encoding="utf-8"))
        request = urllib.request.Request(
            f"http://{_local_host(control.get('host', ''))}:{control['port']}/api/control/show",
            method="POST", headers={"X-Fetch-Control": control["token"]}, data=b"",
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            return bool(json.loads(resp.read()).get("window"))
    except (OSError, ValueError, KeyError):
        return False


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # no browser on this machine: the address is printed anyway
        pass


def _record_update(boot: BootConfig, state: str, message: str, from_version: str) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        previous = json.loads(boot.update_status_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        previous = {}
    try:
        boot.update_status_file.write_text(json.dumps({
            "state": state, "message": message, "from_version": from_version,
            "to_version": __version__, "started_at": previous.get("started_at") or now,
            "finished_at": now,
        }), encoding="utf-8")
    except OSError:
        pass


def _finish_update(boot: BootConfig, from_version: str) -> None:
    """Runs in the new FETCH.exe right after the old one handed over."""
    import shutil
    import time

    # The previous version is shutting down; wait until it has let go.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and _is_running(boot):
        time.sleep(0.5)
    time.sleep(1.0)
    # Keep a copy of the database from before any migration.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = boot.data_dir / "backups" / stamp
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for name in ("fetch.db", "fetch.db-wal", "fetch.db-shm"):
            if (boot.data_dir / name).is_file():
                shutil.copy2(boot.data_dir / name, backup / name)
        if boot.config_file.is_file():
            shutil.copy2(boot.config_file, backup / boot.config_file.name)
        old = sorted((p for p in backup.parent.iterdir() if p.is_dir()), reverse=True)[10:]
        for path in old:
            shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        print(f"The database could not be backed up before the update: {exc}", file=sys.stderr)


def _remove_old_executable() -> None:
    from .runtime import executable_path

    exe = executable_path()
    if exe is None:
        return
    try:
        exe.with_name(exe.name + ".old").unlink()
    except OSError:
        pass  # not there, or the previous version is still exiting


def _run(boot: BootConfig, args: argparse.Namespace) -> int:
    """What double-clicking FETCH.exe does: set up if needed, start the server, show the window."""
    from . import desktop
    from .db import run_migrations
    from .storage import StorageManager

    after_update = args.after_update or ""
    # `--no-browser` alone is the old way to ask for no interface. FETCH 1.x also
    # passes it when it hands over to a new version, which should show its window.
    headless = args.headless or (args.no_browser and not after_update)
    if after_update:
        _finish_update(boot, after_update)
    else:
        running = _is_running(boot)
        if running:
            print(f"FETCH is already running: {running}")
            if not headless and not _show_running(boot):
                _open_browser(running)
            return 0
    _remove_old_executable()
    try:
        write_initial_config(boot)
        boot = load_config(boot.home, boot.config_file)
        boot.ensure_dirs()
        run_migrations(boot.database_url)
        StorageManager(boot.storage_root).ensure()
    except Exception as exc:
        desktop.show_error("FETCH", f"FETCH could not be set up in {boot.home}: {exc}")
        if after_update:
            _record_update(boot, "failed", f"FETCH {__version__} could not start: {exc}", after_update)
        return 1

    args.host, args.port = None, None
    webview = None if headless else desktop.load_window_library()
    pystray = None if headless else desktop.load_tray_library()
    if webview is None and pystray is None:
        code = _run_in_console(boot, args, after_update, open_browser=not headless)
    else:
        code = _run_desktop(boot, args, after_update, webview, pystray)
    if code != 0 and after_update:
        _record_update(boot, "failed", f"FETCH {__version__} was installed but did not start.", after_update)
    return code


def _run_in_console(boot: BootConfig, args: argparse.Namespace, after_update: str, *, open_browser: bool) -> int:
    """No window: the server runs in the foreground until it is stopped."""
    if not open_browser:
        os.environ["FETCH_HEADLESS"] = "1"  # an update restarts FETCH the same way

    def started(url: str) -> None:
        if after_update:
            _record_update(boot, "succeeded", f"Updated from {after_update} to {__version__}.", after_update)
        print("  Press Ctrl+C, or run 'stop', to stop FETCH.\n")
        if open_browser:
            _open_browser(url)

    return _serve(boot, args, on_started=started)


def _run_desktop(boot: BootConfig, args: argparse.Namespace, after_update: str, webview, pystray) -> int:
    """The server on a background thread, the dashboard in a window of this same program."""
    from . import desktop

    backend = desktop.Backend(lambda **hooks: _serve(boot, args, **hooks))
    if not backend.start():
        backend.stop()
        desktop.show_error("FETCH", backend.error or "FETCH could not start. Details are in logs/fetch.log.")
        return backend.code or 1
    if after_update:
        _record_update(boot, "succeeded", f"Updated from {after_update} to {__version__}.", after_update)
    shell = desktop.DesktopShell(
        backend, webview=webview, pystray=pystray, data_dir=boot.data_dir,
        storage_dir=backend.ctx.storage.root, open_browser=_open_browser,
    )
    return shell.run(title=backend.setting("general", "app_name", "FETCH"))


def _serve(boot: BootConfig, args: argparse.Namespace, on_started=None, on_server=None, on_error=None) -> int:
    import uvicorn

    from .app import create_app
    from .logging_setup import setup_logging

    def fail(message: str) -> int:
        print(message, file=sys.stderr)
        if on_error is not None:
            on_error(message)
        return 1

    boot.ensure_dirs()
    setup_logging(boot.log_dir, boot.log_level)
    try:
        app = create_app(boot)
    except Exception as exc:
        return fail(f"FETCH could not start: {exc}")
    ctx = app.state.ctx
    host, port = ctx.settings.effective_bind()
    host, port = getattr(args, "host", None) or host, getattr(args, "port", None) or port
    config = uvicorn.Config(
        app, host=host, port=port, log_config=None, access_log=False,
        timeout_graceful_shutdown=20, ws_ping_interval=30, ws_ping_timeout=30,
        proxy_headers=False, server_header=False,
    )
    server = uvicorn.Server(config)
    ctx.server = server
    if on_server is not None:
        on_server(server, ctx)
    boot.pid_file.write_text(str(os.getpid()), encoding="utf-8")
    boot.control_file.write_text(json.dumps({"host": host, "port": port, "token": ctx.control_token, "pid": os.getpid()}),
                                 encoding="utf-8")
    try:
        os.chmod(boot.control_file, 0o600)
    except OSError:
        pass
    url = f"http://{_local_host(host)}:{port}"
    print(f"\n  FETCH {__version__} is running.\n  Dashboard: {url}\n")
    if on_started is not None:
        import threading
        import time

        def announce() -> None:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not server.started:
                if server.should_exit:
                    return
                time.sleep(0.1)
            if server.started:
                on_started(url)

        threading.Thread(target=announce, daemon=True).start()
    try:
        server.run()
    except OSError as exc:
        return fail(f"FETCH could not listen on {host}:{port}: {exc}")
    except SystemExit:
        # uvicorn exits this way when the port is taken.
        return fail(f"FETCH could not listen on {host}:{port}. Is another program using that port?")
    finally:
        for path in (boot.pid_file, boot.control_file):
            try:
                path.unlink()
            except OSError:
                pass
    return 0 if server.started else fail(f"FETCH could not listen on {host}:{port}.")


def main(argv: list[str] | None = None) -> int:
    # The windowed FETCH.exe and pythonw.exe have no console streams of their own.
    from .desktop import attach_console

    attach_console()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    parser = argparse.ArgumentParser(prog="fetch_app", description="FETCH download manager")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create configuration, folders and the database")
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=_init)

    p = sub.add_parser("serve", help="run the server")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=_serve)

    p = sub.add_parser("run", help="set up if needed, start FETCH and show its window")
    p.add_argument("--headless", action="store_true", help="no window, tray icon or browser")
    p.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--after-update", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=_run)

    p = sub.add_parser("status", help="print status as JSON")
    p.set_defaults(func=_status)

    p = sub.add_parser("stop", help="stop a running server gracefully")
    p.set_defaults(func=_stop)

    if argv is None:
        argv = sys.argv[1:]
    from .runtime import is_frozen

    if is_frozen() and not argv:
        argv = ["run"]  # FETCH.exe was double-clicked
    args = parser.parse_args(argv)
    return args.func(load_config(), args)


if __name__ == "__main__":
    sys.exit(main())
