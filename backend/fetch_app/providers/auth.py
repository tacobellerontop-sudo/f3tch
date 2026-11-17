"""Provider authentication: the modes and how one is chosen.

A provider can be used in up to two ways:

* ``anonymous`` - no provider credentials at all;
* ``account`` - the account stored for that provider on this FETCH
  installation (encrypted, never shown again once saved).

Every adapter declares the modes it supports (``ProviderAdapter.auth_modes``).
For each provider the mode can be chosen explicitly or left on ``auto``, which
uses the stored account when there is one and anonymous access otherwise.
This module holds the pure decision logic; storage lives in the registry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .base import AUTO_MODE, AuthMode, ErrorCode, ProviderAdapter, ProviderError

#: Order in which ``auto`` tries the modes.
AUTO_ORDER = (AuthMode.ACCOUNT, AuthMode.ANONYMOUS)
PREFERENCES = (AUTO_MODE, *(mode.value for mode in AuthMode))


@dataclass
class AuthStatus:
    """A provider's authentication state. Holds no secrets."""

    provider: str
    #: Modes the adapter supports at all.
    modes: list[str]
    authentication_required: bool
    anonymous_available: bool
    account_configured: bool = False
    #: Outcome of the last check of the account (``None`` = never checked).
    account_valid: bool | None = None
    account_message: str = ""
    #: The stored preference: "auto" or one of the modes.
    preference: str = AUTO_MODE
    #: The mode that would be used right now, or ``None`` when none is usable.
    effective_mode: str | None = None
    #: False when the provider is disabled or was last seen unreachable.
    provider_available: bool = True
    #: "anonymous" | "account" | "auth_required" | "unavailable"
    status: str = "auth_required"
    #: Why no mode is usable, when that is the case.
    problem_code: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def choose_mode(provider: ProviderAdapter, preference: str, *, account_configured: bool) -> AuthMode:
    """The mode to use, or a :class:`ProviderError` saying precisely why none is.

    An explicit preference is never silently replaced by another mode.
    """
    supported = provider.supported_auth_modes()
    usable = {
        AuthMode.ANONYMOUS: AuthMode.ANONYMOUS in supported,
        AuthMode.ACCOUNT: AuthMode.ACCOUNT in supported and account_configured,
    }
    if preference in (AUTO_MODE, "", None):
        for mode in AUTO_ORDER:
            if usable[mode]:
                return mode
        raise ProviderError(
            ErrorCode.AUTH_REQUIRED,
            f"{provider.name} needs an account and none is configured. Add one under Providers.",
        )
    try:
        mode = AuthMode(preference)
    except ValueError as exc:
        raise ProviderError(ErrorCode.AUTH_REQUIRED, "Unknown authentication mode.") from exc
    if usable[mode]:
        return mode
    if mode is AuthMode.ANONYMOUS:
        raise ProviderError(
            ErrorCode.ANON_UNAVAILABLE,
            f"{provider.name} cannot be used without an account. Choose another "
            "authentication mode under Providers.",
        )
    if mode not in supported:
        raise ProviderError(
            ErrorCode.AUTH_REQUIRED,
            f"{provider.name} does not support accounts. Choose another authentication "
            "mode under Providers.",
        )
    raise ProviderError(
        ErrorCode.AUTH_REQUIRED,
        f"{provider.name} is set to use an account, but none is stored. Add it under "
        "Providers, or choose another authentication mode.",
    )
