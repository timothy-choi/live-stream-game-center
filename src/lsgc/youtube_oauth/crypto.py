"""Authenticated encryption for OAuth refresh/access tokens.

Uses Fernet (AES-CBC + HMAC) from the `cryptography` package.
Do not invent a homemade cipher. Never log plaintext tokens.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from lsgc.youtube_oauth.errors import OAuthNotConfiguredError

PLAIN_PREFIX = "plain:"


class TokenCipher:
    def __init__(self, *, key: str | None, allow_plaintext: bool) -> None:
        self._fernet: Fernet | None = None
        self._allow_plaintext = allow_plaintext
        if key:
            try:
                self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
            except Exception as exc:
                raise OAuthNotConfiguredError(
                    "LSGC_TOKEN_ENCRYPTION_KEY is not a valid Fernet key"
                ) from exc
        elif not allow_plaintext:
            raise OAuthNotConfiguredError(
                "Set LSGC_TOKEN_ENCRYPTION_KEY or explicitly "
                "LSGC_ALLOW_PLAINTEXT_OAUTH_TOKENS=true for local development"
            )

    @property
    def uses_plaintext(self) -> bool:
        return self._fernet is None

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._fernet is not None:
            return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{PLAIN_PREFIX}{value}"

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._fernet is not None:
            try:
                return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
            except InvalidToken as exc:
                raise OAuthNotConfiguredError("stored token could not be decrypted") from exc
        if value.startswith(PLAIN_PREFIX):
            return value[len(PLAIN_PREFIX) :]
        raise OAuthNotConfiguredError("plaintext token store is not enabled")
