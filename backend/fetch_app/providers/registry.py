"""Provider manager: discovery, selection, credentials and health."""

from __future__ import annotations

import logging
from typing import Any

from ..db import Database, utcnow
from ..engine.http import HttpClient
from ..models import Provider, ProviderCredential
from ..security.crypto import SecretBox
from .base import (
    ErrorCode,
    HealthReport,
    LinkInfo,
    ProviderAdapter,
    ProviderContext,
    ProviderError,
)
from .direct import DirectHttpProvider
from .gdrive import GoogleDriveProvider
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
                ErrorCode.DISABLED, f"The {provider.name} provider is disabled by the administrator."
            )
        return provider

    # -- credentials -----------------------------------------------------------

    def credentials(self, provider_id: str) -> dict[str, str] | None:
        with self._db.session() as session:
            row = session.get(ProviderCredential, provider_id)
            secret = row.secret if row else None
        if not secret:
            return None
        try:
            return self._box.decrypt(secret)
        except ValueError as exc:
            log.error("Credentials for %s cannot be decrypted", provider_id)
            raise ProviderError(ErrorCode.AUTH_FAILED, str(exc)) from exc

    def set_credentials(self, provider_id: str, values: dict[str, str], actor_id: int | None) -> list[str]:
        provider = self._providers[provider_id]
        if not provider.capabilities.credentials:
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
        return sorted(cleaned)

    def clear_credentials(self, provider_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(ProviderCredential, provider_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def context(self, provider: ProviderAdapter) -> ProviderContext:
        creds = self.credentials(provider.id) if provider.capabilities.credentials else None
        return ProviderContext(
            http=self._http, credentials=creds, log=logging.getLogger(f"fetch.provider.{provider.id}")
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
                message=f"The {provider.name} provider is disabled by the administrator.",
            )
        try:
            return await provider.inspect(url, self.context(provider))
        except ProviderError as exc:
            # Temporary conditions do not make a link unsupported: the queue can
            # still retry it. Everything else is reported as it is.
            temporary = exc.retryable or exc.code == ErrorCode.WAIT_REQUIRED
            return LinkInfo(
                url=url, provider=provider.id, supported=temporary,
                restriction=exc.code, message=exc.message,
            )
        except Exception:
            log.exception("Provider %s failed while inspecting a link", provider.id)
            return LinkInfo(
                url=url, provider=provider.id, supported=False, restriction=ErrorCode.PROTOCOL,
                message=f"{provider.name} could not inspect this link (internal error).",
            )

    async def test(self, provider_id: str) -> HealthReport:
        provider = self._providers[provider_id]
        try:
            report = await provider.test(self.context(provider))
        except ProviderError as exc:
            report = HealthReport(ok=False, message=exc.message)
        except Exception:
            log.exception("Provider %s failed its connectivity test", provider_id)
            report = HealthReport(ok=False, message="The connectivity test crashed; see the server log.")
        self.record_health(provider_id, report.ok, report.message)
        return report

    def record_health(self, provider_id: str, ok: bool, message: str) -> None:
        with self._db.session() as session:
            row = session.get(Provider, provider_id)
            if row is not None:
                row.health = "ok" if ok else "error"
                row.health_message = message[:1000]
                row.checked_at = utcnow()
