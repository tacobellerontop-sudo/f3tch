"""Encryption of provider credentials at rest (Fernet: AES-128-CBC + HMAC)."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, secret_key: str) -> None:
        if not secret_key:
            raise ValueError("A secret key is required to store credentials.")
        try:
            self._fernet = Fernet(secret_key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("secret_key must be a urlsafe base64-encoded 32-byte key.") from exc

    def encrypt(self, data: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as exc:
            raise ValueError(
                "Stored credentials could not be decrypted (was the secret key changed?)."
            ) from exc
