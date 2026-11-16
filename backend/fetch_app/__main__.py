"""Command line entry point: ``python -m fetch_app <command>``."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request

from . import __version__
from .config import BootConfig, load_config, write_initial_config


def _init(boot: BootConfig, args: argparse.Namespace) -> int:
    from .db import run_migrations

    created = write_initial_config(boot, bind_address=args.bind, port=args.port)
    boot = load_config(boot.home, boot.config_file)
    boot.ensure_dirs()
    run_migrations(boot.database_url)
    from .storage import StorageManager

    StorageManager(boot.storage_root).ensure()
    print(f"Configuration: {boot.config_file} ({'created' if created else 'kept'})")
    print(f"Database:      {boot.database_path}")
    print(f"Storage root:  {boot.storage_root}")
    return 0


def _create_admin(boot: BootConfig, args: argparse.Namespace) -> int:
    from .db import Database, run_migrations
    from .models import Role, User
    from .quotas import get_quota
    from .schemas import USERNAME_PATTERN
    from .security.passwords import check_policy, hash_password
    from .settings_service import SettingsService

    run_migrations(boot.database_url)
    db = Database(boot.database_url)
    settings = SettingsService(db, boot).get()
    username = args.username or input("Administrator username: ").strip()
    if not re.match(USERNAME_PATTERN, username):
        print("Usernames are 3-32 characters: letters, digits, '.', '_' and '-'.", file=sys.stderr)
        return 2
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("The passwords do not match.", file=sys.stderr)
            return 2
    problem = check_policy(
        password, min_length=settings.security.password_min_length,
        require_mixed=settings.security.password_require_mixed,
    )
    if problem:
        print(problem, file=sys.stderr)
        return 2
    with db.session() as session:
        if session.query(User).filter(User.username == username).first():
            print(f"A user named '{username}' already exists.", file=sys.stderr)
            return 3
        user = User(username=username, display_name=username,
                    password_hash=hash_password(password), role=Role.ADMIN.value)
        session.add(user)
        session.flush()
        get_quota(session, user, settings)
    print(f"Administrator '{username}' created.")
    return 0


def _status(boot: BootConfig, _args: argparse.Namespace) -> int:
    from .db import Database, run_migrations
    from .models import User
    from .settings_service import SettingsService

    run_migrations(boot.database_url)
    db = Database(boot.database_url)
    host, port = SettingsService(db, boot).effective_bind()
    with db.session() as session:
        users = session.query(User).count()
    running = None
    try:
        control = json.loads(boot.control_file.read_text(encoding="utf-8"))
        with urllib.request.urlopen(f"http://127.0.0.1:{control['port']}/api/health", timeout=3) as resp:
            running = resp.status == 200
    except (OSError, ValueError, KeyError):
        running = False
    print(json.dumps({
        "version": __version__, "running": running, "bind_address": host, "port": port,
        "users": users, "setup_required": users == 0,
        "setup_token": boot.setup_token if users == 0 else None,
    }))
    return 0


def _stop(boot: BootConfig, _args: argparse.Namespace) -> int:
    try:
        control = json.loads(boot.control_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("FETCH does not appear to be running.")
        return 1
    request = urllib.request.Request(
        f"http://127.0.0.1:{control['port']}/api/control/shutdown",
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


def _serve(boot: BootConfig, args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app
    from .logging_setup import setup_logging

    boot.ensure_dirs()
    setup_logging(boot.log_dir, boot.log_level)
    try:
        app = create_app(boot)
    except Exception as exc:
        print(f"FETCH could not start: {exc}", file=sys.stderr)
        return 1
    ctx = app.state.ctx
    host, port = ctx.settings.effective_bind()
    host, port = args.host or host, args.port or port
    config = uvicorn.Config(
        app, host=host, port=port, log_config=None, access_log=False,
        timeout_graceful_shutdown=20, ws_ping_interval=30, ws_ping_timeout=30,
        proxy_headers=False, server_header=False,
    )
    server = uvicorn.Server(config)
    ctx.server = server
    boot.pid_file.write_text(str(os.getpid()), encoding="utf-8")
    boot.control_file.write_text(json.dumps({"port": port, "token": ctx.control_token, "pid": os.getpid()}),
                                 encoding="utf-8")
    try:
        os.chmod(boot.control_file, 0o600)
    except OSError:
        pass
    shown = "localhost" if host in ("0.0.0.0", "::", "127.0.0.1") else host
    print(f"\n  FETCH {__version__} is running.\n  Dashboard: http://{shown}:{port}\n")
    with ctx.db.session() as session:
        from .models import User

        if session.query(User.id).first() is None:
            print(f"  First-time setup token: {boot.setup_token}\n")
    try:
        server.run()
    except OSError as exc:
        print(f"FETCH could not listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    finally:
        for path in (boot.pid_file, boot.control_file):
            try:
                path.unlink()
            except OSError:
                pass
    return 0 if server.started else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_app", description="FETCH download manager")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create configuration, folders and the database")
    p.add_argument("--bind", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=_init)

    p = sub.add_parser("create-admin", help="create an administrator account")
    p.add_argument("--username", default=None)
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=_create_admin)

    p = sub.add_parser("serve", help="run the server")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=_serve)

    p = sub.add_parser("status", help="print status as JSON")
    p.set_defaults(func=_status)

    p = sub.add_parser("stop", help="stop a running server gracefully")
    p.set_defaults(func=_stop)

    args = parser.parse_args(argv)
    return args.func(load_config(), args)


if __name__ == "__main__":
    sys.exit(main())
