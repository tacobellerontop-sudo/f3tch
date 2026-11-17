"""Pydantic request/response models shared by the API and the event stream."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from .models import Download

UTCDateTime = Annotated[
    datetime,
    PlainSerializer(
        lambda value: value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        return_type=str,
    ),
]

class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- downloads -----------------------------------------------------------------


def _clean_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for url in urls:
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            cleaned.append(url)
    if not cleaned:
        raise ValueError("Provide at least one URL.")
    return cleaned


class InspectIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)

    _clean = field_validator("urls")(_clean_urls)


class SubmitIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    destination: Literal["private", "shared"] | None = None
    folder: str = Field("", max_length=512)
    priority: int | None = Field(None, ge=0, le=3)

    _clean = field_validator("urls")(_clean_urls)


class DownloadOut(_Out):
    id: int
    url: str
    provider: str
    filename: str
    state: str
    priority: int
    position: int
    destination: str
    folder: str
    total_bytes: int | None
    downloaded_bytes: int
    speed_bps: float
    avg_speed_bps: float
    eta_seconds: float | None = None
    engine: str
    attempts: int
    max_attempts: int
    error_code: str
    error_message: str
    wait_until: UTCDateTime | None
    wait_reason: str
    final_path: str
    created_at: UTCDateTime
    started_at: UTCDateTime | None
    finished_at: UTCDateTime | None


def serialize_download(row: Download) -> dict[str, Any]:
    out = DownloadOut.model_validate(row)
    if row.state != "downloading":
        out.speed_bps = 0.0
    elif row.total_bytes and row.speed_bps > 0:
        out.eta_seconds = max(0.0, (row.total_bytes - row.downloaded_bytes) / row.speed_bps)
    return out.model_dump(mode="json")


class DownloadPatchIn(BaseModel):
    priority: int = Field(ge=0, le=3)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["pause", "resume", "cancel", "retry", "remove"]
    delete_files: bool = False


class ReorderIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


# -- files ---------------------------------------------------------------------


class RevealIn(BaseModel):
    scope: Literal["private", "shared"] = "private"
    path: str = Field("", max_length=1024)


class FolderIn(BaseModel):
    scope: Literal["private", "shared"] = "private"
    path: str = Field("", max_length=1024)
    name: str = Field(min_length=1, max_length=200)


# -- providers -----------------------------------------------------------------


class ProviderPatchIn(BaseModel):
    enabled: bool


class CredentialsIn(BaseModel):
    values: dict[str, str]
