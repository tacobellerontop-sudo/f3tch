"""The application context: one object wiring all services together."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .activity import ActivityService
from .config import BootConfig
from .db import Database
from .engine.aria2 import Aria2Service
from .engine.guardproxy import GuardProxy
from .engine.http import HttpClient
from .engine.manager import DownloadManager
from .events import EventBus
from .providers.registry import ProviderRegistry
from .security.crypto import SecretBox
from .security.urlguard import UrlGuard
from .settings_service import SettingsService
from .storage import StorageManager
from .updates import UpdateService


@dataclass
class AppContext:
    boot: BootConfig
    db: Database
    settings: SettingsService
    bus: EventBus
    activity: ActivityService
    storage: StorageManager
    guard: UrlGuard
    http: HttpClient
    box: SecretBox
    registry: ProviderRegistry
    proxy: GuardProxy
    aria2: Aria2Service
    manager: DownloadManager
    updates: UpdateService
    #: Link grabbing from the clipboard (see clipboard.py).
    clipboard: Any = None
    started_at: float = field(default_factory=time.time)
    control_token: str = ""
    # Id of the built-in local profile, filled in on first use.
    local_user_id: int = 0
    # Set by the desktop shell: brings its window to the front.
    show_window: Any = None
    # Set by the CLI so the local control endpoint can stop the server.
    server: Any = None
    # Settings that changed since start and need a restart to apply.
    pending_restart: set[str] = field(default_factory=set)
