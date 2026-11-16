"""Password hashing (Argon2id) and policy checks."""

from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=64 * 1024, parallelism=2)
# Verified against when the account does not exist, so timing does not reveal it.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def check_policy(password: str, *, min_length: int, require_mixed: bool) -> str | None:
    """Return a human-readable problem, or ``None`` when the password is fine."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters long."
    if len(password) > 256:
        return "Password must be at most 256 characters long."
    if require_mixed and not (
        any(c.isalpha() for c in password) and any(c.isdigit() for c in password)
    ):
        return "Password must contain both letters and digits."
    return None


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate):
            return candidate
