"""MEGA public file links.

Uses the same public API the official MEGA clients use for anonymous
downloads of *public* file links: the link carries the decryption key, the API
returns a temporary storage URL, and the content is decrypted locally
(AES-128-CTR) and authenticated with MEGA's chunked CBC-MAC.

MEGA enforces a transfer quota per IP address for anonymous downloads. When it
is exhausted FETCH reports it and waits for the period MEGA announces; it does
not try to evade the quota.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .base import (
    AuthMode,
    Capabilities,
    ErrorCode,
    HealthReport,
    LinkInfo,
    ProviderAdapter,
    ProviderContext,
    ProviderError,
    ResolvedDownload,
)

API = "https://g.api.mega.co.nz/cs"
_HOST = re.compile(r"^https?://(?:www\.)?mega(?:\.co)?\.nz(?:/|$)", re.I)
_FILE_NEW = re.compile(r"^https?://(?:www\.)?mega(?:\.co)?\.nz/(?:file|embed)/([\w-]{8})#([\w-]{43})", re.I)
_FILE_OLD = re.compile(r"^https?://(?:www\.)?mega(?:\.co)?\.nz/#!([\w-]{8})!([\w-]{43})", re.I)
_FOLDER = re.compile(r"^https?://(?:www\.)?mega(?:\.co)?\.nz/(?:folder/|#F!)", re.I)

_API_ERRORS = {
    -2: (ErrorCode.UNSUPPORTED, "MEGA rejected the link as malformed.", False),
    -3: (ErrorCode.UNAVAILABLE, "MEGA asked to retry shortly.", True),
    -4: (ErrorCode.RATE_LIMITED, "MEGA is rate limiting requests from this address.", True),
    -6: (ErrorCode.RATE_LIMITED, "Too many requests for this MEGA link.", True),
    -9: (ErrorCode.NOT_FOUND, "MEGA reports that this file does not exist or was removed.", False),
    -11: (ErrorCode.AUTH_REQUIRED, "MEGA denied access to this file.", False),
    -14: (ErrorCode.UNSUPPORTED, "The decryption key in this MEGA link is invalid.", False),
    -16: (ErrorCode.NOT_FOUND, "The account that shared this MEGA file is blocked.", False),
    -17: (ErrorCode.QUOTA_EXCEEDED, "MEGA's transfer quota for this address is exhausted.", False),
    -18: (ErrorCode.UNAVAILABLE, "This MEGA file is temporarily unavailable.", True),
    -19: (ErrorCode.NOT_FOUND, "This MEGA link was taken down (terms of service).", False),
}


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _aes_ecb(key: bytes):
    return Cipher(algorithms.AES(key), modes.ECB()).encryptor()


def chunk_boundaries(size: int):
    """MEGA MAC chunks: 128 KiB, 256 KiB … 1 MiB, then 1 MiB each."""
    position, step = 0, 0x20000
    while position < size:
        length = min(step, size - position)
        yield position, length
        position += length
        if step < 0x100000:
            step += 0x20000


class MegaKey:
    """The pieces packed into the key of a public file link."""

    def __init__(self, raw: bytes) -> None:
        if len(raw) != 32:
            raise ProviderError(ErrorCode.UNSUPPORTED, "The decryption key in this MEGA link is invalid.")
        words = struct.unpack(">8I", raw)
        self.key = struct.pack(
            ">4I", words[0] ^ words[4], words[1] ^ words[5], words[2] ^ words[6], words[3] ^ words[7]
        )
        self.nonce = raw[16:24]
        self.meta_mac = raw[24:32]

    def decrypt_attributes(self, data: bytes) -> dict:
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(b"\0" * 16)).decryptor()
        plain = decryptor.update(data + b"\0" * (-len(data) % 16)) + decryptor.finalize()
        if not plain.startswith(b'MEGA{"'):
            raise ProviderError(ErrorCode.UNSUPPORTED, "The decryption key does not match this MEGA file.")
        try:
            return json.loads(plain[4:].rstrip(b"\0").decode("utf-8", "replace"))
        except ValueError as exc:
            raise ProviderError(ErrorCode.PROTOCOL, "MEGA returned unreadable file attributes.") from exc

    def crypt(self, offset: int, data: bytes) -> bytes:
        """AES-CTR keystream applied at an absolute byte offset."""
        skip = offset % 16
        counter = self.nonce + struct.pack(">Q", offset // 16)
        cipher = Cipher(algorithms.AES(self.key), modes.CTR(counter)).encryptor()
        return cipher.update(b"\0" * skip + data)[skip:]

    def file_mac(self, path: Path) -> bytes:
        """Compute MEGA's condensed MAC over the plaintext file."""
        size = path.stat().st_size
        iv = self.nonce + self.nonce
        ecb = _aes_ecb(self.key)
        file_mac = b"\0" * 16
        with path.open("rb") as fh:
            for _start, length in chunk_boundaries(size):
                chunk = fh.read(length)
                chunk += b"\0" * (-len(chunk) % 16)
                cbc = Cipher(algorithms.AES(self.key), modes.CBC(iv)).encryptor()
                chunk_mac = cbc.update(chunk)[-16:]
                file_mac = ecb.update(bytes(a ^ b for a, b in zip(file_mac, chunk_mac, strict=True)))
        words = struct.unpack(">4I", file_mac)
        return struct.pack(">2I", words[0] ^ words[1], words[2] ^ words[3])


class MegaProvider(ProviderAdapter):
    id = "mega"
    name = "MEGA"
    description = "Public MEGA file links (mega.nz/file/…#key)."
    homepage = "https://mega.nz"
    priority = 20
    capabilities = Capabilities(checksum=True, waits=True)
    auth_modes = (AuthMode.ANONYMOUS,)
    notes = (
        "Public single-file links only. Folder links and logging in to a MEGA account "
        "are not supported.",
        "The link must include its decryption key (the part after '#').",
        "MEGA limits anonymous transfer per IP address. When the limit is hit FETCH "
        "waits for the time MEGA announces and then continues.",
    )
    verification = (
        "Protocol implementation (key handling, AES-CTR decryption, MAC verification) is "
        "covered by automated tests against a local emulation of the MEGA API. Not "
        "exercised against the live service by the automated tests."
    )

    def __init__(self, api_url: str = API) -> None:
        self.api_url = api_url
        self._sequence = secrets.randbelow(0xFFFFFFF)

    def can_handle(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def _parse(self, url: str) -> tuple[str, MegaKey]:
        if _FOLDER.match(url):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "MEGA folder links are not supported. Add links to the individual files instead.",
            )
        match = _FILE_NEW.match(url) or _FILE_OLD.match(url)
        if not match:
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "This MEGA link is not a public file link with a decryption key "
                "(expected mega.nz/file/<id>#<key>).",
            )
        try:
            raw = _b64decode(match.group(2))
        except ValueError as exc:
            raise ProviderError(ErrorCode.UNSUPPORTED, "The decryption key in this MEGA link is invalid.") from exc
        return match.group(1), MegaKey(raw)

    @staticmethod
    def _api_error(code: int) -> ProviderError:
        err_code, message, retryable = _API_ERRORS.get(
            code, (ErrorCode.PROTOCOL, f"MEGA returned error {code}.", False)
        )
        return ProviderError(err_code, message, retryable=retryable)

    async def _query(self, ctx: ProviderContext, handle: str, *, want_url: bool) -> dict:
        self._sequence += 1
        command: dict = {"a": "g", "p": handle}
        if want_url:
            command.update({"g": 1, "ssl": 2})
        status, payload = await ctx.http.get_json(
            "POST", f"{self.api_url}?id={self._sequence}", json=[command]
        )
        if status != 200:
            raise ProviderError(
                ErrorCode.UNAVAILABLE, f"MEGA's API is unavailable ({status}).", retryable=True
            )
        if isinstance(payload, int):
            raise self._api_error(payload)
        if isinstance(payload, list) and payload:
            payload = payload[0]
        if isinstance(payload, int):
            raise self._api_error(payload)
        if isinstance(payload, dict) and isinstance(payload.get("e"), int) and payload["e"] < 0:
            raise self._api_error(payload["e"])
        if not isinstance(payload, dict) or "s" not in payload or "at" not in payload:
            raise ProviderError(ErrorCode.PROTOCOL, "MEGA returned an unexpected response.")
        return payload

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        handle, key = self._parse(url)
        data = await self._query(ctx, handle, want_url=False)
        attributes = key.decrypt_attributes(_b64decode(data["at"]))
        return LinkInfo(
            url=url,
            provider=self.id,
            filename=str(attributes.get("n") or ""),
            size=int(data["s"]),
            resumable=True,
        )

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        handle, key = self._parse(url)
        data = await self._query(ctx, handle, want_url=True)
        attributes = key.decrypt_attributes(_b64decode(data["at"]))
        storage_url = data.get("g")
        if not isinstance(storage_url, str) or not storage_url:
            raise ProviderError(
                ErrorCode.QUOTA_EXCEEDED,
                "MEGA did not provide a download address for this file (the transfer "
                "quota may be exhausted).",
            )

        async def verify(path: Path) -> None:
            mac = await asyncio.to_thread(key.file_mac, path)
            if mac != key.meta_mac:
                raise ProviderError(
                    ErrorCode.VERIFY_FAILED,
                    "The downloaded MEGA file failed its integrity check.",
                    retryable=True,
                )

        def map_error(status: int, headers: dict, _body: str) -> ProviderError | None:
            if status == 509:
                left = next(
                    (v for k, v in headers.items() if k.lower() == "x-mega-time-left"), ""
                )
                seconds = int(left) if str(left).isdigit() else 3600
                return ProviderError(
                    ErrorCode.WAIT_REQUIRED,
                    "MEGA's transfer quota for this address is exhausted. FETCH will "
                    "continue when MEGA allows it.",
                    wait_seconds=min(seconds + 30, 24 * 3600),
                )
            return None

        return ResolvedDownload(
            url=storage_url,
            filename=str(attributes.get("n") or ""),
            size=int(data["s"]),
            identity=f"mega:{handle}:{data['s']}",
            allow_aria2=False,
            block_align=16,
            transform=key.crypt,
            verifier=verify,
            error_mapper=map_error,
        )

    async def test(self, ctx: ProviderContext) -> HealthReport:
        self._sequence += 1
        status, payload = await ctx.http.get_json("POST", f"{self.api_url}?id={self._sequence}", json=[])
        if status == 200 and payload is not None:
            return HealthReport(ok=True, message="MEGA API is reachable (anonymous access).")
        return HealthReport(ok=False, message=f"MEGA API did not answer as expected ({status}).")
