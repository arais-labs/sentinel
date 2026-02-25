from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, status

from config import (
    ACCESS_TOKEN_TTL_SECONDS,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    REFRESH_TOKEN_TTL_SECONDS,
)


@dataclass
class PlatformIdentity:
    sub: str
    role: str
    agent_id: str | None = None


def _hash_api_key(api_key: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", api_key.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return f"{salt}${digest.hex()}"


def hash_api_key(api_key: str) -> str:
    return _hash_api_key(api_key)


def verify_api_key(api_key: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = _hash_api_key(api_key, salt=salt)
    return secrets.compare_digest(candidate, stored_hash)


def _encode_token(*, identity: PlatformIdentity, token_type: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": identity.sub,
        "role": identity.role,
        "agent_id": identity.agent_id,
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": str(uuid.uuid4()),
        "token_type": token_type,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_access_token(identity: PlatformIdentity) -> str:
    return _encode_token(identity=identity, token_type="access", ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)


def create_refresh_token(identity: PlatformIdentity) -> str:
    return _encode_token(identity=identity, token_type="refresh", ttl_seconds=REFRESH_TOKEN_TTL_SECONDS)


def decode_token(token: str, *, expected_type: str | None = None) -> dict:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized

    token_type = payload.get("token_type")
    if expected_type and token_type != expected_type:
        raise unauthorized

    if not payload.get("sub") or not payload.get("role"):
        raise unauthorized

    return payload

