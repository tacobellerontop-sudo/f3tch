"""Bootstrap configuration.

This is the small amount of configuration FETCH needs *before* the database is
available: where things live on disk, the encryption key and the first-run
setup token. Everything else is a runtime setting stored in the database (see
``settings_service``).

Sources, in increasing precedence: built-in defaults, ``config/fetch.toml``,
``FETCH_*`` environment variables.
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet

DEFAULT_PORT = 8787
DEFAULT_UPDATE_REPOSITORY = "tacobellerontop-sudo/f3tch"


def default_home() -> Path:
    """Install root: ``FETCH_HOME`` or the directory that contains ``backend/``."""
    env = os.environ.get("FETCH_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


@dataclass
class BootConfig:
    home: Path
    config_file: Path
    data_dir: Path
    log_dir: Path
    storage_root: Path
    frontend_dist: Path
    bind_address: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    secret_key: str = ""
    setup_token: str = ""
    # Hosts / CIDR ranges exempt from the SSRF guard (e.g. a NAS on the LAN).
    ssrf_allowlist: list[str] = field(default_factory=list)
    # "auto" marks cookies Secure when the request arrived over HTTPS.
    cookie_secure: str = "auto"
    log_level: str = "INFO"
    # GitHub repository ("owner/name") whose releases are offered as updates.
    update_repository: str = DEFAULT_UPDATE_REPOSITORY
    update_api_url: str = "https://api.github.com"

    @property
    def update_status_file(self) -> Path:
        return self.data_dir / "update-status.json"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "fetch.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "fetch.pid"

    @property
    def control_file(self) -> Path:
        return self.data_dir / "control.json"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.log_dir, self.storage_root, self.config_file.parent):
            path.mkdir(parents=True, exist_ok=True)


def _resolve(home: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (home / path).resolve()


def load_config(home: Path | None = None, config_file: Path | None = None) -> BootConfig:
    home = (home or default_home()).resolve()
    env = os.environ
    config_file = Path(
        config_file or env.get("FETCH_CONFIG") or home / "config" / "fetch.toml"
    ).expanduser()

    raw: dict = {}
    if config_file.is_file():
        with config_file.open("rb") as fh:
            raw = tomllib.load(fh)

    paths = raw.get("paths", {})
    server = raw.get("server", {})
    security = raw.get("security", {})
    updates = raw.get("updates", {})

    def pick(env_name: str, section: dict, key: str, default):
        if env.get(env_name) not in (None, ""):
            return env[env_name]
        return section.get(key, default)

    allow = pick("FETCH_SSRF_ALLOWLIST", security, "ssrf_allowlist", [])
    if isinstance(allow, str):
        allow = [item.strip() for item in allow.split(",") if item.strip()]

    return BootConfig(
        home=home,
        config_file=config_file,
        data_dir=_resolve(home, pick("FETCH_DATA_DIR", paths, "data_dir", "data")),
        log_dir=_resolve(home, pick("FETCH_LOG_DIR", paths, "log_dir", "logs")),
        storage_root=_resolve(home, pick("FETCH_STORAGE_ROOT", paths, "storage_root", "storage")),
        frontend_dist=_resolve(
            home, pick("FETCH_FRONTEND_DIST", paths, "frontend_dist", "frontend/dist")
        ),
        bind_address=str(pick("FETCH_BIND", server, "bind_address", "127.0.0.1")),
        port=int(pick("FETCH_PORT", server, "port", DEFAULT_PORT)),
        secret_key=str(pick("FETCH_SECRET_KEY", security, "secret_key", "")),
        setup_token=str(pick("FETCH_SETUP_TOKEN", security, "setup_token", "")),
        ssrf_allowlist=list(allow),
        cookie_secure=str(pick("FETCH_COOKIE_SECURE", security, "cookie_secure", "auto")).lower(),
        log_level=str(pick("FETCH_LOG_LEVEL", server, "log_level", "INFO")).upper(),
        update_repository=str(
            pick("FETCH_UPDATE_REPO", updates, "repository", DEFAULT_UPDATE_REPOSITORY)
        ).strip(),
        update_api_url=str(
            pick("FETCH_UPDATE_API", updates, "api_url", "https://api.github.com")
        ).rstrip("/"),
    )


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_initial_config(
    config: BootConfig, *, bind_address: str | None = None, port: int | None = None
) -> bool:
    """Create ``fetch.toml`` with freshly generated secrets. Never overwrites."""
    if config.config_file.exists():
        return False
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.secret_key = Fernet.generate_key().decode()
    config.setup_token = secrets.token_urlsafe(24)
    if bind_address:
        config.bind_address = bind_address
    if port:
        config.port = port

    def rel(path: Path) -> str:
        try:
            return path.relative_to(config.home).as_posix()
        except ValueError:
            return str(path)

    body = f"""# FETCH bootstrap configuration.
# Generated on first install. Keep this file private: it contains the key that
# encrypts stored provider credentials. Everything else is configured from the
# dashboard (Settings).

[paths]
data_dir = {_toml_str(rel(config.data_dir))}
log_dir = {_toml_str(rel(config.log_dir))}
storage_root = {_toml_str(rel(config.storage_root))}

[server]
# Used until overridden in Settings > Network.
bind_address = {_toml_str(config.bind_address)}
port = {config.port}
log_level = "INFO"

[security]
secret_key = {_toml_str(config.secret_key)}
# Required once, to create the first administrator from the browser.
setup_token = {_toml_str(config.setup_token)}
# Hosts or CIDR ranges that downloads may reach despite being private, e.g.
# ["192.168.1.20", "10.0.0.0/24"]. Leave empty unless you know you need it.
ssrf_allowlist = []
cookie_secure = "auto"

[updates]
# GitHub repository whose releases are offered under Settings > Updates.
# Set to "" to switch update checks off completely.
repository = {_toml_str(config.update_repository)}
"""
    config.config_file.write_text(body, encoding="utf-8")
    try:
        os.chmod(config.config_file, 0o600)
    except OSError:
        pass
    return True
