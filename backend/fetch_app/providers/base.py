"""The provider plugin contract.

A provider adapter knows how to recognise links of one hosting service, look
up file metadata, and turn a link into something the download engine can
transfer. The engine never contains provider-specific logic: it talks to
adapters only through :class:`ProviderAdapter`.

Adapters must describe themselves truthfully through :class:`Capabilities`.
An operation a provider cannot perform raises :class:`ProviderError` with a
specific ``code``; it is never silently faked. Adapters must not attempt to
defeat a provider's access controls (Premium checks, CAPTCHAs, waiting
periods, quotas): those conditions are reported to the user instead.
"""

from __future__ import annotations

import enum
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..engine.http import HttpClient
    from ..engine.transfer import Transfer


class AuthMode(enum.StrEnum):
    """How FETCH identifies itself to a provider for one request."""

    #: No provider credentials at all.
    ANONYMOUS = "anonymous"
    #: The account stored for this provider on this FETCH installation.
    ACCOUNT = "account"


#: Preference value meaning "pick the best available mode for me".
AUTO_MODE = "auto"


class ErrorCode:
    UNSUPPORTED = "unsupported_link"
    #: The file is unavailable: deleted, never existed, or blocked.
    NOT_FOUND = "not_found"
    #: This provider (or the mode the user chose) needs an account and none is configured.
    AUTH_REQUIRED = "auth_required"
    #: The configured credentials were rejected.
    AUTH_FAILED = "auth_failed"
    #: The provider does not offer (or refused) access without an account.
    ANON_UNAVAILABLE = "anonymous_unavailable"
    #: The file can only be downloaded with a Premium account.
    PREMIUM_ONLY = "premium_only"
    CAPTCHA_REQUIRED = "captcha_required"
    QUOTA_EXCEEDED = "quota_exceeded"
    WAIT_REQUIRED = "wait_required"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "provider_unavailable"
    NETWORK = "network_error"
    UNSAFE_URL = "unsafe_url"
    DISABLED = "provider_disabled"
    VERIFY_FAILED = "verification_failed"
    PROTOCOL = "unexpected_response"


class ProviderError(Exception):
    """A provider-level failure with a stable code and a user-safe message."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        wait_seconds: float | None = None,
        challenge: CaptchaChallenge | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        # Set when the provider imposes a waiting period that FETCH will honour.
        self.wait_seconds = wait_seconds
        # Set with ``captcha_required`` when the owner of the download can complete
        # the challenge themselves. FETCH only relays it; it never solves it.
        self.challenge = challenge


@dataclass
class CaptchaChallenge:
    """A CAPTCHA the provider demands, to be completed by the person downloading.

    ``kind`` is ``"image"`` when the provider served a picture whose text the
    user can type into FETCH, or ``"external"`` when the challenge only works
    on the provider's own website (for example a widget bound to its domain).
    FETCH never solves, forwards to a solving service, or skips a challenge.
    """

    kind: str
    prompt: str
    #: Seconds the user has before the provider's challenge goes stale.
    expires_in: float = 300.0
    image: bytes = b""
    image_type: str = "image/png"
    #: Page on the provider's site where the user can complete it themselves.
    page_url: str = ""
    #: Whether the user may hand FETCH the download address the provider gave
    #: them after they completed the challenge in their own browser.
    accepts_download_url: bool = False
    attempt: int = 1


@dataclass(frozen=True)
class Capabilities:
    inspect: bool = True
    download: bool = True
    resume: bool = True
    #: Works without any stored credentials.
    anonymous: bool = True
    #: Accepts administrator-supplied credentials.
    credentials: bool = False
    #: Cannot download anything until credentials are configured.
    requires_credentials: bool = False
    folders: bool = False
    checksum: bool = False
    #: May impose waiting periods that FETCH waits out (never skips).
    waits: bool = False
    #: May demand a CAPTCHA. FETCH reports it and never solves it; where the
    #: provider's challenge allows, the user can complete it themselves.
    captcha: bool = False


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    kind: str = "password"  # "text" | "password" | "textarea"
    required: bool = False
    help: str = ""


@dataclass
class LinkInfo:
    url: str
    provider: str
    supported: bool = True
    filename: str = ""
    size: int | None = None
    resumable: bool | None = None
    #: Machine-readable reason when ``supported`` is false or a restriction applies.
    restriction: str = ""
    message: str = ""
    #: The provider only serves this file to Premium accounts.
    premium_only: bool = False
    #: Authentication mode the link was inspected with ("" when none applied).
    auth_mode: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedDownload:
    """Everything the engine needs to transfer one file."""

    url: str
    filename: str = ""
    size: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    resumable: bool = True
    #: ("sha256" | "md5" | "sha1", hex digest) checked in the Verifying state.
    checksum: tuple[str, str] | None = None
    #: Stable identity of the content, allowing resume across fresh URLs.
    identity: str | None = None
    #: Whether the multi-connection aria2 engine may be used.
    allow_aria2: bool = True
    #: Resume offsets are rounded down to a multiple of this (stream ciphers).
    block_align: int = 1
    #: Optional ``(offset, data) -> data`` applied to every received chunk.
    transform: Callable[[int, bytes], bytes] | None = None
    #: Optional extra integrity check run on the finished file.
    verifier: Callable[[Path], Awaitable[None]] | None = None
    #: Maps an HTTP error from the file server to a :class:`ProviderError`.
    error_mapper: Callable[[int, dict[str, str], str], ProviderError | None] | None = None


@dataclass
class HealthReport:
    ok: bool
    message: str
    account: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderContext:
    """Per-call services handed to an adapter."""

    http: HttpClient
    #: Decrypted credentials of the mode in use; ``None`` in anonymous mode.
    credentials: dict[str, str] | None = None
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("fetch.provider"))
    auth_mode: str = AuthMode.ANONYMOUS.value
    #: The download job, when the call belongs to one (lets adapters keep the
    #: provider's own session across a waiting period or a CAPTCHA).
    job_id: int | None = None
    #: What the job's owner supplied for a pending challenge, if anything:
    #: ``{"answer": ...}`` or ``{"download_url": ...}``.
    user_input: dict[str, str] | None = None


class ProviderAdapter(ABC):
    """Base class every provider plugin implements."""

    #: Stable identifier (also the primary key in the ``providers`` table).
    id: str = ""
    name: str = ""
    description: str = ""
    homepage: str = ""
    #: Lower numbers are asked first by the registry.
    priority: int = 100
    capabilities: Capabilities = Capabilities()
    #: Authentication modes this provider supports. Every adapter declares them.
    auth_modes: tuple[AuthMode, ...] = (AuthMode.ANONYMOUS,)
    credential_fields: tuple[CredentialField, ...] = ()
    #: Plain-language notes about limits, shown on the Providers page.
    notes: tuple[str, ...] = ()
    #: How far this integration was verified; reported as-is, never embellished.
    verification: str = ""

    # -- recognition -----------------------------------------------------------

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Whether ``url`` belongs to this provider (pure, no I/O)."""

    # -- metadata --------------------------------------------------------------

    @abstractmethod
    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        """Look up the file behind ``url`` without downloading it."""

    # -- authentication --------------------------------------------------------

    def supported_auth_modes(self) -> tuple[AuthMode, ...]:
        return tuple(self.auth_modes)

    def anonymous_available(self) -> bool:
        """Whether the provider can be used with no credentials at all."""
        return AuthMode.ANONYMOUS in self.auth_modes

    def authentication_required(self) -> bool:
        """Whether nothing can be downloaded until some account is configured."""
        return not self.anonymous_available()

    def accepts_accounts(self) -> bool:
        return AuthMode.ACCOUNT in self.auth_modes

    async def validate_account(self, ctx: ProviderContext) -> HealthReport:
        """Whether the account in ``ctx.credentials`` is accepted by the provider."""
        if not ctx.credentials:
            return HealthReport(ok=False, message="No account is configured.")
        return await self.authenticate(ctx)

    async def link_requires_premium(self, url: str, ctx: ProviderContext) -> bool:
        """Whether the provider only serves ``url`` to Premium accounts."""
        try:
            info = await self.inspect(url, ctx)
        except ProviderError as exc:
            if exc.code == ErrorCode.PREMIUM_ONLY:
                return True
            raise
        return info.premium_only or info.restriction == ErrorCode.PREMIUM_ONLY

    async def authenticate(self, ctx: ProviderContext) -> HealthReport:
        """Validate the stored credentials. Default: provider takes none."""
        return HealthReport(ok=True, message="This provider does not use credentials.")

    async def test(self, ctx: ProviderContext) -> HealthReport:
        """Connectivity / account check used by the Providers page."""
        return await self.authenticate(ctx)

    def validate_credentials(self, values: dict[str, str]) -> dict[str, str]:
        """Check a credentials document before it is stored."""
        allowed = {f.name: f for f in self.credential_fields}
        cleaned: dict[str, str] = {}
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"Unknown credential field '{key}'.")
            if not isinstance(value, str):
                raise ValueError(f"Credential field '{key}' must be text.")
            if value.strip():
                cleaned[key] = value.strip()
        for spec in self.credential_fields:
            if spec.required and spec.name not in cleaned:
                raise ValueError(f"{spec.label} is required.")
        return cleaned

    # -- downloading -----------------------------------------------------------

    @abstractmethod
    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        """Turn ``url`` into a transferable download (the provider's own workflow)."""

    async def download(self, transfer: Transfer, resolved: ResolvedDownload) -> None:
        """Transfer the file to ``transfer.destination``.

        The default hands the resolved request to the shared transfer engines
        (native async HTTP or aria2). Adapters with a custom protocol override it.
        """
        await transfer.fetch(resolved)

    def check_download_address(self, url: str) -> str | None:
        """Why ``url`` cannot be a download address of this provider, or ``None``.

        Used when a user hands over the address the provider issued to them
        after they completed its CAPTCHA in their own browser.
        """
        return "This provider does not accept a download address."

    def release_job(self, job_id: int) -> None:
        """Forget any provider session kept for ``job_id`` (cancelled, removed, failed)."""

    # -- job control (delegated to the download manager) -----------------------

    def get_status(self, job_id: int) -> dict[str, Any] | None:
        """Live status of one of this provider's jobs, if it is running."""
        runtime = getattr(self, "runtime", None)
        return runtime.job_status(job_id) if runtime else None

    async def cancel(self, job_id: int) -> bool:
        runtime = getattr(self, "runtime", None)
        return await runtime.cancel_job(job_id) if runtime else False

    # -- description -----------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "homepage": self.homepage,
            "capabilities": asdict(self.capabilities),
            "auth_modes": [mode.value for mode in self.auth_modes],
            "notes": list(self.notes),
            "verification": self.verification,
            "credential_fields": [asdict(f) for f in self.credential_fields],
        }
