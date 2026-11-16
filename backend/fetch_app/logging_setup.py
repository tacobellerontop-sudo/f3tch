"""Structured (JSON lines) logging with secret redaction."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import UTC, datetime
from pathlib import Path

LOG_FILE_NAME = "fetch.log"
_SENSITIVE_KEYS = re.compile(
    r"(pass(word|wd)?|secret|token|cookie|authorization|api[_-]?key|credential|session)", re.I
)
_URL_USERINFO = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def redact(value):
    """Recursively mask values whose key looks sensitive; scrub URL credentials."""
    if isinstance(value, dict):
        return {
            k: ("***" if _SENSITIVE_KEYS.search(str(k)) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _URL_USERINFO.sub(r"\1***@", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(log_dir: Path, level: str = "INFO", *, console: bool = True) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_fetch", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(getattr(logging, level, logging.INFO))

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler._fetch = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        stream._fetch = True  # type: ignore[attr-defined]
        root.addHandler(stream)

    # Access logs would duplicate our own request-level audit trail.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    return log_file


def tail_log(log_file: Path, *, limit: int = 200, level: str | None = None, query: str = ""):
    """Return the newest ``limit`` parsed log entries (newest first)."""
    if not log_file.is_file():
        return []
    wanted = level.upper() if level else None
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    min_rank = order.index(wanted) if wanted in order else 0
    needle = query.lower()
    entries: list[dict] = []
    chunk = 256 * 1024
    with log_file.open("rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        buffer = b""
        while end > 0 and len(entries) < limit:
            start = max(0, end - chunk)
            fh.seek(start)
            buffer = fh.read(end - start) + buffer
            end = start
            lines = buffer.split(b"\n")
            buffer = lines[0] if start > 0 else b""
            for raw in reversed(lines[1:] if start > 0 else lines):
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                rank = order.index(entry.get("level")) if entry.get("level") in order else 1
                if rank < min_rank:
                    continue
                if needle and needle not in raw.decode("utf-8", "replace").lower():
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    break
    return entries
