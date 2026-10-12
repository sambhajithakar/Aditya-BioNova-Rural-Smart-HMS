"""Password hashing and token primitives.

Passwords: scrypt (stdlib, OWASP-approved KDF) with a per-password random
salt. Format: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``.
Access tokens: short-lived HS256 JWTs carrying the session id (``sid``) so
that revoking the server-side session invalidates them immediately.
Refresh tokens: opaque random strings; only SHA-256 hashes are persisted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any

import jwt

from .config import get_settings

# --- scrypt password hashing ---------------------------------------------
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
# scrypt needs memory > 128 * N * r; give it headroom (128*16384*8 = 16 MiB)
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


# A dummy hash used when a login email does not exist, so that response
# timing does not reveal whether the account is registered.
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-equalisation-000")


# --- Access tokens (JWT) ---------------------------------------------------
def create_access_token(*, user_id: str, session_id: str, org_id: str) -> str:
    settings = get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "sid": session_id,
        "org": org_id,
        "iss": settings.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + settings.access_token_ttl_seconds,
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "sid", "org", "typ"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# --- Refresh tokens (opaque) ----------------------------------------------
def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext_token, sha256_hex_hash). Plaintext is shown once."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
