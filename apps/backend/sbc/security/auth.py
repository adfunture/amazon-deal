"""Passwort-Hashing (scrypt, stdlib) und JWT-Zugriffstoken."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

import jwt

from sbc.config import get_settings

_N, _R, _P = 2**15, 8, 1


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Passwort muss mindestens 10 Zeichen haben")
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r),
                            p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, org_id: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": user_id, "org": org_id, "role": role, "iat": now,
         "exp": now + timedelta(minutes=s.access_token_minutes), "typ": "access"},
        s.app_secret, algorithm="HS256",
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().app_secret, algorithms=["HS256"],
                      options={"require": ["exp", "sub", "org"]})
