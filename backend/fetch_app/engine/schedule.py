"""Download schedule: only download during chosen hours (for example at night).

Times are this computer's local time. A window whose stop time is earlier
than its start time runs overnight and belongs to the day it starts on.
Equal start and stop times mean the whole day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


def parse_hhmm(value: str) -> time:
    hours, _, minutes = value.partition(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class ScheduleState:
    enabled: bool
    #: Whether downloads may run right now.
    open: bool
    #: When ``open`` next changes (local time), if the schedule is on.
    next_change: datetime | None

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "open": self.open,
            "next_change": self.next_change.isoformat() if self.next_change else None,
        }


def _windows(start: time, stop: time, days: list[int], around: datetime) -> list[tuple[datetime, datetime]]:
    """The download windows that start between two days before and eight days after ``around``."""
    windows = []
    base = around.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(-2, 9):
        day = base + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        begin = day.replace(hour=start.hour, minute=start.minute)
        end = day.replace(hour=stop.hour, minute=stop.minute)
        if end <= begin:
            end += timedelta(days=1)
        windows.append((begin, end))
    return windows


def evaluate(enabled: bool, start: str, stop: str, days: list[int], now: datetime | None = None) -> ScheduleState:
    now = now or datetime.now().astimezone()
    if not enabled:
        return ScheduleState(False, True, None)
    if not days:
        return ScheduleState(True, False, None)
    windows = _windows(parse_hhmm(start), parse_hhmm(stop), days, now)
    for begin, end in windows:
        if begin <= now < end:
            # Back-to-back windows (every day, all day) merge into one.
            while any(b == end for b, _ in windows):
                end = next(e for b, e in windows if b == end)
            return ScheduleState(True, True, end)
    upcoming = [begin for begin, _ in windows if begin > now]
    return ScheduleState(True, False, min(upcoming) if upcoming else None)


def from_settings(downloads, now: datetime | None = None) -> ScheduleState:
    return evaluate(
        downloads.schedule_enabled,
        downloads.schedule_start,
        downloads.schedule_stop,
        list(downloads.schedule_days),
        now,
    )
