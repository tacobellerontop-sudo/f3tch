"""Where FETCH is running from: a source tree, or the single-file FETCH.exe."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True inside the single-file executable built with PyInstaller."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def resource_dir() -> Path:
    """Folder that holds bundled program files (alembic, the web interface)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def executable_path() -> Path | None:
    """The FETCH executable itself, or None when running from source."""
    return Path(sys.executable).resolve() if is_frozen() else None
