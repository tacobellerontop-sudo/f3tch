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

USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$"


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- auth / users --------------------------------------------------------------


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupIn(BaseModel):
    setup_token: str = Field(min_length=1, max_length=200)
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field("", max_length=128)


class RegisterIn(BaseModel):
    token: str = Field("", max_length=200)
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field("", max_length=128)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, max_length=128)
    theme: Literal["", "dark", "light", "system"] | None = None


class UserOut(_Out):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    theme: str
    must_change_password: bool
    created_at: UTCDateTime
    last_login_at: UTCDateTime | None


class SessionOut(_Out):
    id: int
    user_id: int
    username: str = ""
    created_at: UTCDateTime
    last_seen_at: UTCDateTime
    expires_at: UTCDateTime
    ip_address: str
    user_agent: str
    current: bool = False


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(None, max_length=128)
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class QuotaIn(BaseModel):
    max_concurrent: int = Field(ge=0, le=32)
    max_storage_bytes: int = Field(ge=0)
    max_file_bytes: int = Field(ge=0)
    daily_downloads: int = Field(ge=0, le=100_000)
    bandwidth_bps: int = Field(ge=0)
    max_queued: int = Field(ge=0, le=100_000)


class QuotaOut(_Out, QuotaIn):
    pass


class ResetPasswordIn(BaseModel):
    new_password: str | None = Field(None, max_length=256)


class InvitationIn(BaseModel):
    role: Literal["admin", "user"] = "user"
    note: str = Field("", max_length=200)
    expires_hours: int | None = Field(None, ge=1, le=24 * 90)


class InvitationOut(_Out):
    id: int
    role: str
    note: str
    created_at: UTCDateTime
    expires_at: UTCDateTime
    used_at: UTCDateTime | None
    revoked: bool
    status: str = ""


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
    user_id: int
    owner: str = ""
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


def serialize_download(row: Download, owner: str | None = None) -> dict[str, Any]:
    out = DownloadOut.model_validate(row)
    out.owner = owner if owner is not None else (row.user.username if row.user else "")
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


class FolderIn(BaseModel):
    scope: Literal["private", "shared"] = "private"
    path: str = Field("", max_length=1024)
    name: str = Field(min_length=1, max_length=200)
    owner_id: int | None = None


# -- providers -----------------------------------------------------------------


class ProviderPatchIn(BaseModel):
    enabled: bool


class CredentialsIn(BaseModel):
    values: dict[str, str]
