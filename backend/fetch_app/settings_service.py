"""Runtime settings: validated with Pydantic, persisted in ``app_settings``."""

from __future__ import annotations

import ipaddress
import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import BootConfig
from .db import Database
from .models import AppSetting

GB = 1024**3

# Fields that only take effect after FETCH is restarted.
RESTART_REQUIRED = {
    "network.bind_address",
    "network.port",
    "network.cors_origins",
    "storage.storage_root",
    "downloads.use_aria2",
    "downloads.aria2_path",
}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GeneralSettings(_Section):
    app_name: str = Field("FETCH", min_length=1, max_length=40)
    theme: Literal["dark", "light", "system"] = "dark"
    language: Literal["en"] = "en"
    default_destination: Literal["private", "shared"] = "private"


class DownloadSettings(_Section):
    max_global_concurrent: int = Field(3, ge=1, le=32)
    max_per_user_concurrent: int = Field(2, ge=1, le=32)
    retry_count: int = Field(3, ge=0, le=20)
    retry_delay_seconds: int = Field(30, ge=1, le=3600)
    default_priority: int = Field(1, ge=0, le=3)
    use_aria2: bool = True
    aria2_path: str = Field("", max_length=500)
    aria2_min_size_mb: int = Field(16, ge=1, le=100_000)
    aria2_connections: int = Field(4, ge=1, le=16)


class StorageSettings(_Section):
    # Empty means "use the path from config/fetch.toml".
    storage_root: str = Field("", max_length=500)
    max_storage_gb: float = Field(0, ge=0, le=1_000_000)
    temp_cleanup_hours: int = Field(72, ge=1, le=24 * 365)
    auto_delete_days: int = Field(0, ge=0, le=3650)
    history_retention_days: int = Field(0, ge=0, le=3650)
    shared_enabled: bool = True
    users_can_write_shared: bool = True

    @field_validator("storage_root")
    @classmethod
    def _root(cls, value: str) -> str:
        value = value.strip()
        if value and any(ch in value for ch in '<>"|?*\x00'):
            raise ValueError("Storage root contains invalid characters.")
        return value


class NetworkSettings(_Section):
    # Empty / 0 mean "use the value from config/fetch.toml".
    bind_address: str = Field("", max_length=64)
    port: int = Field(0, ge=0, le=65535)
    trusted_proxies: list[str] = Field(default_factory=list, max_length=32)
    cors_origins: list[str] = Field(default_factory=list, max_length=32)
    public_url: str = Field("", max_length=300)

    @field_validator("bind_address")
    @classmethod
    def _bind(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("Bind address must be an IP address, e.g. 127.0.0.1.") from exc
        return value

    @field_validator("port")
    @classmethod
    def _port(cls, value: int) -> int:
        if value and value < 1024:
            raise ValueError("Port must be 0 (default) or between 1024 and 65535.")
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _proxies(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            item = item.strip()
            if not item:
                continue
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"'{item}' is not an IP address or CIDR range.") from exc
            cleaned.append(item)
        return cleaned

    @field_validator("cors_origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            item = item.strip().rstrip("/")
            if not item:
                continue
            if item == "*" or not item.startswith(("http://", "https://")):
                raise ValueError("CORS origins must be explicit http(s) origins; '*' is not allowed.")
            cleaned.append(item)
        return cleaned

    @field_validator("public_url")
    @classmethod
    def _public(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("Public URL must start with http:// or https://.")
        return value


class SecuritySettings(_Section):
    session_hours: int = Field(168, ge=1, le=24 * 365)
    invitations_required: bool = True
    invitation_expiry_hours: int = Field(72, ge=1, le=24 * 90)
    password_min_length: int = Field(10, ge=8, le=128)
    password_require_mixed: bool = False
    login_max_attempts: int = Field(5, ge=2, le=100)
    login_window_minutes: int = Field(15, ge=1, le=1440)
    login_lockout_minutes: int = Field(15, ge=1, le=1440)


class QuotaDefaults(_Section):
    """Limits applied to newly created regular users (0 = unlimited)."""

    max_concurrent: int = Field(2, ge=0, le=32)
    max_storage_gb: float = Field(50, ge=0, le=1_000_000)
    max_file_gb: float = Field(0, ge=0, le=1_000_000)
    daily_downloads: int = Field(0, ge=0, le=100_000)
    bandwidth_kbps: int = Field(0, ge=0, le=100_000_000)
    max_queued: int = Field(100, ge=0, le=100_000)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general: GeneralSettings = Field(default_factory=GeneralSettings)
    downloads: DownloadSettings = Field(default_factory=DownloadSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    quota_defaults: QuotaDefaults = Field(default_factory=QuotaDefaults)


class SettingsService:
    """Cached, thread-safe access to :class:`AppSettings`."""

    def __init__(self, db: Database, boot: BootConfig) -> None:
        self._db = db
        self._boot = boot
        self._lock = threading.Lock()
        self._cache: AppSettings | None = None

    def get(self) -> AppSettings:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            return self._cache

    def _load(self) -> AppSettings:
        with self._db.session() as session:
            stored = {row.key: row.value for row in session.query(AppSetting).all()}
        data: dict[str, Any] = {}
        for name in AppSettings.model_fields:
            if isinstance(stored.get(name), dict):
                data[name] = stored[name]
        try:
            return AppSettings.model_validate(data)
        except ValueError:
            # A section written by another version failed validation: keep the
            # sections that are still valid and fall back to defaults elsewhere.
            good = {}
            for name, value in data.items():
                try:
                    AppSettings.model_validate({name: value})
                    good[name] = value
                except ValueError:
                    continue
            return AppSettings.model_validate(good)

    def update(self, patch: dict[str, Any]) -> tuple[AppSettings, list[str]]:
        """Merge ``patch`` (section -> fields), validate, persist.

        Returns the new settings and the dotted names of the fields that changed.
        """
        current = self.get()
        merged = current.model_dump()
        for section, values in patch.items():
            if section not in merged:
                raise ValueError(f"Unknown settings section '{section}'.")
            if not isinstance(values, dict):
                raise ValueError(f"Section '{section}' must be an object.")
            merged[section] = {**merged[section], **values}
        updated = AppSettings.model_validate(merged)

        before, after = current.model_dump(), updated.model_dump()
        changed = [
            f"{section}.{key}"
            for section, values in after.items()
            for key, value in values.items()
            if before[section].get(key) != value
        ]
        if changed:
            with self._db.session() as session:
                for section in {name.split(".", 1)[0] for name in changed}:
                    row = session.get(AppSetting, section)
                    if row is None:
                        session.add(AppSetting(key=section, value=after[section]))
                    else:
                        row.value = after[section]
            with self._lock:
                self._cache = updated
        return updated, changed

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    # -- effective values that combine boot config and settings ---------------

    def effective_bind(self) -> tuple[str, int]:
        net = self.get().network
        return net.bind_address or self._boot.bind_address, net.port or self._boot.port

    def effective_storage_root(self) -> str:
        return self.get().storage.storage_root or str(self._boot.storage_root)
