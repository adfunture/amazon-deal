from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from sbc.db import get_db
from sbc.models import AuditLog, Role, User
from sbc.security.auth import decode_token

bearer = HTTPBearer(auto_error=False)

ROLE_ORDER = {Role.viewer: 0, Role.reviewer: 1, Role.buyer: 2, Role.admin: 3}


@dataclass
class CurrentUser:
    id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    email: str


def _from_api_key(raw: str, db: Session) -> CurrentUser:
    import hashlib
    from datetime import UTC, datetime

    from sqlalchemy import select

    from sbc.models import ApiKey

    try:
        _, prefix, _secret = raw.split("_", 2)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API-Schlüssel ungültig") from e
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    import hmac

    if key is None or key.revoked_at is not None or not hmac.compare_digest(
            key.key_hash, hashlib.sha256(raw.encode()).hexdigest()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API-Schlüssel ungültig oder widerrufen")
    user = db.get(User, key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer inaktiv")
    key.last_used_at = datetime.now(UTC)
    db.commit()
    return CurrentUser(user.id, user.org_id, user.role, user.email)


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> CurrentUser:
    api_key = request.headers.get("x-api-key")
    if api_key:
        return _from_api_key(api_key, db)
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    try:
        claims = decode_token(creds.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ungültig oder abgelaufen") from e
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer inaktiv")
    return CurrentUser(user.id, user.org_id, user.role, user.email)


def require(min_role: Role) -> Callable[[CurrentUser], CurrentUser]:
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if ROLE_ORDER[user.role] < ROLE_ORDER[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Rolle '{min_role.value}' erforderlich")
        return user
    return dep


def audit(db: Session, user: CurrentUser | None, action: str, entity_type: str | None = None,
          entity_id: object = None, data: dict | None = None, request: Request | None = None) -> None:
    db.add(AuditLog(org_id=user.org_id if user else None, user_id=user.id if user else None,
                    action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None,
                    data=data or {}, ip=request.client.host if request and request.client else None))
