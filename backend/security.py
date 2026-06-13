"""
security.py
===========
Password hashing (Argon2) and JWT creation / verification.

Token type enforcement
----------------------
Every token carries a "typ" claim:
  access  tokens → {"typ": "access",  "sub": username}
  refresh tokens → {"typ": "refresh", "sub": username}

The /refresh endpoint and get_current_username() both verify the typ claim
so that a refresh token cannot be used as a bearer token and vice-versa.

verify_password returns bool rather than raising HTTPException — this is a
utility module, not a router, and it must not depend on FastAPI internals.
The caller is responsible for converting a False return into the appropriate
HTTP response.
"""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2 import exceptions as argon_exceptions
from fastapi import HTTPException

from config import get_settings
from models.schemas import Token

_ph = PasswordHasher()


# ── Password helpers ──────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Returns True if the password matches; False on any mismatch or error."""
    try:
        return _ph.verify(hashed_password, plain_password)
    except (
        argon_exceptions.VerifyMismatchError,
        argon_exceptions.VerificationError,
        argon_exceptions.InvalidHashError,
    ):
        return False


# ── JWT helpers ───────────────────────────────────────────────────────────────


def _encode(payload: dict, expire_minutes: int) -> str:
    to_encode = payload.copy()
    to_encode["exp"] = datetime.now(UTC) + timedelta(minutes=expire_minutes)
    return jwt.encode(to_encode, get_settings().SECRET_KEY, algorithm=get_settings().ALGORITHM)


def create_access_token(data: dict) -> str:
    return _encode({**data, "typ": "access"}, get_settings().ACCESS_TOKEN_EXPIRE_MINUTES)


def create_refresh_token(data: dict) -> str:
    return _encode({**data, "typ": "refresh"}, get_settings().REFRESH_TOKEN_EXPIRE_MINUTES)


def create_tokens(data: dict) -> Token:
    return Token(
        access_token=create_access_token(data),
        refresh_token=create_refresh_token(data),
    )


def verify_exists_and_owns(username: str, obj: object) -> None:
    """Raises HTTPException if obj is missing or belongs to a different user."""
    if not obj:
        raise HTTPException(status_code=404, detail="The resource does not exist")
    if getattr(obj, "user", None) != username:
        raise HTTPException(status_code=403, detail="Forbidden")
