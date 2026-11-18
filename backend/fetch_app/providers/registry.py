"""Provider manager: discovery, selection, credentials and health."""

from __future__ import annotations

import logging
from typing import Any

from ..db import Database, utcnow
from ..engine.http import HttpClient
from ..models import Provider, ProviderCredential
from ..security.crypto import SecretBox
from .auth import PREFERENCES, AuthStatus, choose_mode
from .base import (
    AUTO_MODE,
    AuthMode,
    ErrorCode,
    HealthReport,
    LinkInfo,
    ProviderAdapter,
    ProviderContext,
    ProviderError,
)
from .direct import DirectHttpProvider
from .filehosts import DropboxProvider, MediaFireProvider, WeTransferProvider
from .gdrive import GoogleDriveProvider
from .media import MediaProvider
from .mega import MegaProvider
from .pixeldrain import PixeldrainProvider
from .rapidgator import RapidgatorProvider

log = logging.getLogger("fetch.providers")


def builtin_providers() -> list[ProviderAdapter]:
    return [
        RapidgatorProvider(),
        MegaProvider(),
        PixeldrainProvider(),
        GoogleDriveProvider(),
        DropboxProvider(),
        MediaFireProvider(),
        WeTransferProvider(),
        MediaProvider(),
        DirectHttpProvider(),
    ]


class ProviderRegistry:
    def __init__(
        self,
        db: Database,
        http: HttpClient,
        box: SecretBox,
        providers: list[ProviderAdapter] | None = None,
    ) -> None:
        self._db = db
        self._http = http
        self._box = box
        self._providers: dict[str, ProviderAdapter] = {}
        #: FETCH's guard proxy (set by the application once it exists).
        self.proxy: Any = None
        for provider in providers if providers is not None else builtin_providers():
            self.register(provider)

    # -- registration ----------------------------------------------------------

    def register(self, provider: ProviderAdapter) -> None:
        if not provider.id:
            raise ValueError("Provider adapters need an id.")
        self._providers[provider.id] = provider

    def sync(self) -> None:
        """Make sure every installed provider has a row in the database."""
        with self._db.session() as session:
            known = {row.id for row in session.query(Provider.id)}
            for provider_id in self._providers:
                if provider_id not in known:
                    session.add(Provider(id=provider_id, enabled=True))

    def all(self) -> list[ProviderAdapter]:
        return sorted(self._providers.values(), key=lambda p: p.priority)

    def get(self, provider_id: str) -> ProviderAdapter | None:
        return self._providers.get(provider_id)

    def bind_runtime(self, runtime: Any) -> None:
        for provider in self._providers.values():
            provider.runtime = runtime  # type: ignore[attr-defined]

    # -- selection -------------------------------------------------------------

    def detect(self, url: str) -> ProviderAdapter | None:
        """The most specific installed provider that recognises ``url``."""
        for provider in self.all():
            try:
                if provider.can_handle(url):
                    return provider
            except Exception:  # a broken plugin must not break detection
                log.exception("Provider %s failed in can_handle", provider.id)
        return None

    def is_enabled(self, provider_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(Provider, provider_id)
            return bool(row and row.enabled)

    def select(self, url: str) -> ProviderAdapter:
        """Like :meth:`detect` but raises a :class:`ProviderError` when unusable."""
        provider = self.detect(url)
        if provider is None:
            raise ProviderError(ErrorCode.UNSUPPORTED, "No installed provider supports this link.")
        if not self.is_enabled(provider.id):
            raise ProviderError(
                ErrorCode.DISABLED, f"The {provider.name} provider is switched off on the Providers page."
            )
        return provider

    # -- credentials -----------------------------------------------------------

    def _decrypt(self, provider_id: str, secret: str | None) -> dict[str, str] | None:
        if not secret:
            return None
        try:
            return self._box.decrypt(secret)
        except ValueError as exc:
            log.error("Credentials for %s cannot be decrypted", provider_id)
            raise ProviderError(ErrorCode.AUTH_FAILED, str(exc)) from exc

    def credentials(self, provider_id: str) -> dict[str, str] | None:
        """The stored account for this provider, decrypted."""
        with self._db.session() as session:
            row = session.get(ProviderCredential, provider_id)
            secret = row.secret if row else None
        return self._decrypt(provider_id, secret)

    def set_credentials(self, provider_id: str, values: dict[str, str], actor_id: int | None) -> list[str]:
        provider = self._providers[provider_id]
        if AuthMode.ACCOUNT not in provider.supported_auth_modes():
            raise ValueError(f"{provider.name} does not accept credentials.")
        cleaned = provider.validate_credentials(values)
        if not cleaned:
            raise ValueError("No credentials were provided.")
        with self._db.session() as session:
            row = session.get(ProviderCredential, provider_id)
            secret = self._box.encrypt(cleaned)
            fields = ", ".join(sorted(cleaned))
            if row is None:
                session.add(
                    ProviderCredential(
                        provider_id=provider_id, secret=secret, fields=fields, updated_by_id=actor_id
                    )
                )
            else:
                row.secret, row.fields, row.updated_by_id = secret, fields, actor_id
                row.updated_at = utcnow()
                row.valid, row.validation_message, row.validated_at = None, "", None
        return sorted(cleaned)

    def clear_credentials(self, provider_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(ProviderCredential, provider_id)
            if row is None:
                return False
            session.delete(row)
            return True

    # -- mode preference and account validity ------------------------------------

    def set_preference(self, provider_id: str, mode: str) -> None:
        provider = self._providers[provider_id]
        if mode not in PREFERENCES:
            raise ValueError("Unknown authentication mode.")
        if mode != AUTO_MODE and AuthMode(mode) not in provider.supported_auth_modes():
            raise ValueError(f"{provider.name} does not support that authentication mode.")
        with self._db.session() as session:
            session.get(Provider, provider_id).auth_mode = mode

    def mark_account(self, provider_id: str, ok: bool, message: str) -> None:
        """Remember the outcome of the last check of the stored account."""
        with self._db.session() as session:
            row = session.get(ProviderCredential, provider_id)
            if row is not None:
                row.valid, row.validation_message, row.validated_at = ok, message[:1000], utcnow()

    # -- mode selection ----------------------------------------------------------

    def auth_status(self, provider: ProviderAdapter) -> AuthStatus:
        """Everything about ``provider``'s authentication. Contains no secrets."""
        with self._db.session() as session:
            row = session.get(Provider, provider.id)
            account = session.get(ProviderCredential, provider.id)
            supported = provider.supported_auth_modes()
            configured = account is not None and AuthMode.ACCOUNT in supported
            status = AuthStatus(
                provider=provider.id,
                modes=[mode.value for mode in supported],
                authentication_required=provider.authentication_required(),
                anonymous_available=provider.anonymous_available(),
                account_configured=configured,
                account_valid=account.valid if configured else None,
                account_message=account.validation_message if configured else "",
                preference=(row.auth_mode if row else "") or AUTO_MODE,
                provider_available=bool(row and row.enabled) and (row.health != "unavailable"),
            )
            enabled = bool(row and row.enabled)
        try:
            mode = choose_mode(provider, status.preference, account_configured=configured)
        except ProviderError as exc:
            status.problem_code, status.message = exc.code, exc.message
        else:
            status.effective_mode = mode.value
        if not enabled:
            status.status = "unavailable"
            status.message = f"The {provider.name} provider is disabled."
        elif status.effective_mode is None:
            status.status = "auth_required"
        elif not status.provider_available:
            status.status = "unavailable"
            status.message = f"{provider.name} was unreachable the last time FETCH contacted it."
        else:
            status.status = status.effective_mode
        return status

    def proxy_url(self) -> str | None:
        proxy = self.proxy
        if proxy is None or not getattr(proxy, "port", 0):
            return None
        return f"http://{proxy.username}:{proxy.password}@127.0.0.1:{proxy.port}"

    def context(
        self,
        provider: ProviderAdapter,
        *,
        job_id: int | None = None,
        user_input: dict[str, str] | None = None,
    ) -> ProviderContext:
        """The context for a call, in the provider's effective authentication mode.

        Raises a :class:`ProviderError` when no mode is usable.
        """
        status = self.auth_status(provider)
        if status.effective_mode is None:
            raise ProviderError(status.problem_code or ErrorCode.AUTH_REQUIRED, status.message)
        mode = AuthMode(status.effective_mode)
        credentials = self.credentials(provider.id) if mode is AuthMode.ACCOUNT else None
        return ProviderContext(
            http=self._http,
            credentials=credentials,
            log=logging.getLogger(f"fetch.provider.{provider.id}"),
            auth_mode=mode.value,
            job_id=job_id,
            user_input=user_input,
            proxy_url=self.proxy_url(),
        )

    # -- operations ------------------------------------------------------------

    async def inspect(self, url: str) -> LinkInfo:
        """Inspect a link; failures become an unsupported :class:`LinkInfo`."""
        provider = self.detect(url)
        if provider is None:
            return LinkInfo(
                url=url, provider="", supported=False, restriction=ErrorCode.UNSUPPORTED,
                message="No installed provider supports this link.",
            )
        if not self.is_enabled(provider.id):
            return LinkInfo(
                url=url, provider=provider.id, supported=False, restriction=ErrorCode.DISABLED,
                message=f"The {provider.name} provider is switched off on the Providers page.",
            )
        mode = ""
        try:
            ctx = self.context(provider)
            mode = ctx.auth_mode
            info = await provider.inspect(url, ctx)
            info.auth_mode = mode
            return info
        except ProviderError as exc:
            # Temporary conditions do not make a link unsupported: the queue can
            # still retry it. Everything else is reported as it is.
            temporary = exc.retryable or exc.code == ErrorCode.WAIT_REQUIRED
            return LinkInfo(
                url=url, provider=provider.id, supported=temporary,
                restriction=exc.code, message=exc.message,
                premium_only=exc.code == ErrorCode.PREMIUM_ONLY, auth_mode=mode,
            )
        except Exception:
            log.exception("Provider %s failed while inspecting a link", provider.id)
            return LinkInfo(
                url=url, provider=provider.id, supported=False, restriction=ErrorCode.PROTOCOL,
                message=f"{provider.name} could not inspect this link (internal error).",
            )

    async def test(self, provider_id: str) -> HealthReport:
        """Check the stored account if there is one, otherwise plain connectivity."""
        provider = self._providers[provider_id]
        has_account = False
        try:
            credentials = (
                self.credentials(provider_id)
                if AuthMode.ACCOUNT in provider.supported_auth_modes() else None
            )
            has_account = bool(credentials)
            ctx = ProviderContext(
                http=self._http, credentials=credentials,
                log=logging.getLogger(f"fetch.provider.{provider_id}"),
                auth_mode=(AuthMode.ACCOUNT if has_account else AuthMode.ANONYMOUS).value,
                proxy_url=self.proxy_url(),
            )
            report = await provider.test(ctx)
        except ProviderError as exc:
            report = HealthReport(ok=False, message=exc.message)
        except Exception:
            log.exception("Provider %s failed its connectivity test", provider_id)
            report = HealthReport(ok=False, message="The connectivity test crashed; see the server log.")
        self.record_health(provider_id, report.ok, report.message)
        if has_account:
            self.mark_account(provider_id, report.ok, report.message)
        return report

    def record_health(self, provider_id: str, ok: bool, message: str, *, unavailable: bool = False) -> None:
        with self._db.session() as session:
            row = session.get(Provider, provider_id)
            if row is not None:
                row.health = "ok" if ok else ("unavailable" if unavailable else "error")
                row.health_message = message[:1000]
                row.checked_at = utcnow()

    def mark_reachable(self, provider_id: str) -> None:
        """A download succeeded: clear an earlier "unreachable" observation."""
        with self._db.session() as session:
            row = session.get(Provider, provider_id)
            if row is not None and row.health == "unavailable":
                row.health, row.health_message, row.checked_at = "ok", "", utcnow()
