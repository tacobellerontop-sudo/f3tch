"""Runtime settings: validated with Pydantic, persisted in ``app_settings``."""

from __future__ import annotations

import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import BootConfig
from .db import Database
from .models import AppSetting

GB = 1024**3
#: FETCH is a local application: it only ever listens on this computer.
LOCAL_ADDRESS = "127.0.0.1"

# Fields that only take effect after FETCH is restarted.
RESTART_REQUIRED = {
    "network.port",
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
    # Look for a newer release on GitHub now and then (one anonymous request).
    check_for_updates: bool = True
    # Closing the window keeps FETCH downloading, with an icon in the notification area.
    close_to_tray: bool = True
    # Offer to add download links copied anywhere on this computer.
    watch_clipboard: bool = True


class DownloadSettings(_Section):
    max_global_concurrent: int = Field(3, ge=1, le=32)
    # Combined speed limit of all downloads in KiB/s; 0 means unlimited.
    speed_limit_kbps: int = Field(0, ge=0, le=100_000_000)
    retry_count: int = Field(3, ge=0, le=20)
    retry_delay_seconds: int = Field(30, ge=1, le=3600)
    default_priority: int = Field(1, ge=0, le=3)
    use_aria2: bool = True
    aria2_path: str = Field("", max_length=500)
    aria2_min_size_mb: int = Field(16, ge=1, le=100_000)
    aria2_connections: int = Field(8, ge=1, le=16)
    # Parallel connections per file for FETCH's own segmented engine
    # (1 turns segmented downloading off).
    connections_per_download: int = Field(8, ge=1, le=32)
    # What YouTube and other media sites are saved as.
    media_quality: Literal["best", "2160", "1440", "1080", "720", "480", "audio"] = "best"


class StorageSettings(_Section):
    # Empty means "use the path from config/fetch.toml".
    storage_root: str = Field("", max_length=500)
    max_storage_gb: float = Field(0, ge=0, le=1_000_000)
    temp_cleanup_hours: int = Field(72, ge=1, le=24 * 365)
    auto_delete_days: int = Field(0, ge=0, le=3650)
    history_retention_days: int = Field(0, ge=0, le=3650)

    @field_validator("storage_root")
    @classmethod
    def _root(cls, value: str) -> str:
        value = value.strip()
        if value and any(ch in value for ch in '<>"|?*\x00'):
            raise ValueError("Storage root contains invalid characters.")
        return value


class NetworkSettings(_Section):
    # 0 means "use the port from config/fetch.toml". FETCH always listens on
    # this computer only (127.0.0.1); there is no address setting.
    port: int = Field(0, ge=0, le=65535)

    @field_validator("port")
    @classmethod
    def _port(cls, value: int) -> int:
        if value and value < 1024:
            raise ValueError("Port must be 0 (default) or between 1024 and 65535.")
        return value


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general: GeneralSettings = Field(default_factory=GeneralSettings)
    downloads: DownloadSettings = Field(default_factory=DownloadSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)


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
                # Fields saved by earlier versions (accounts, quotas, network
                # exposure) no longer exist and are dropped.
                known = AppSettings.model_fields[name].annotation.model_fields
                data[name] = {key: value for key, value in stored[name].items() if key in known}
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
        return LOCAL_ADDRESS, self.get().network.port or self._boot.port

    def effective_storage_root(self) -> str:
        return self.get().storage.storage_root or str(self._boot.storage_root)
