from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import row
from sbc.db import get_db
from sbc.integrations.redis_client import get_redis
from sbc.models import Role, User
from sbc.security.auth import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_ATTEMPTS = 10


class LoginIn(BaseModel):
    # Bewusst keine EmailStr-Validierung: Login ist nur ein Lookup (auch interne Domains).
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


def _rate_limit(key: str) -> None:
    r = get_redis()
    if r is None:
        return
    n = r.incr(key)
    if n == 1:
        r.expire(key, 900)
    if n > MAX_ATTEMPTS:
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 15 Minuten warten")


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    _rate_limit(f"login:{ip}:{body.email.lower()}")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit(db, None, "login_failed", "user", None, {"email": body.email}, request)
        db.commit()
        raise HTTPException(401, "E-Mail oder Passwort falsch")
    user.last_login_at = datetime.now(UTC)
    audit(db, CurrentUser(user.id, user.org_id, user.role, user.email), "login", "user", user.id, request=request)
    db.commit()
    return {"access_token": create_access_token(str(user.id), str(user.org_id), user.role.value),
            "token_type": "bearer", "user": row(user, {"password_hash"})}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return row(db.get(User, user.id), {"password_hash"})


class UserIn(BaseModel):
    email: EmailStr
    name: str | None = None
    password: str = Field(min_length=10, max_length=200)
    role: Role = Role.viewer


@router.get("/users")
def list_users(user: CurrentUser = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    return [row(u, {"password_hash"}) for u in db.scalars(select(User).where(User.org_id == user.org_id))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, user: CurrentUser = Depends(require(Role.admin)),
                db: Session = Depends(get_db)):
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "E-Mail existiert bereits")
    u = User(org_id=user.org_id, email=body.email.lower(), name=body.name,
             password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    audit(db, user, "user_created", "user", u.id, {"email": u.email, "role": u.role.value}, request)
    db.commit()
    return row(u, {"password_hash"})


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    user_email: str | None = Field(default=None, description="Schlüssel gilt mit den Rechten dieses Nutzers")


@router.post("/api-keys", status_code=201)
def create_api_key(body: ApiKeyIn, request: Request, user: CurrentUser = Depends(require(Role.admin)),
                   db: Session = Depends(get_db)):
    """Erzeugt einen API-Schlüssel. Der Klartext wird nur EINMAL zurückgegeben."""
    import hashlib
    import secrets

    from sbc.models import ApiKey

    owner = user.id
    if body.user_email:
        u = db.scalar(select(User).where(func.lower(User.email) == body.user_email.lower(), User.org_id == user.org_id))
        if u is None:
            raise HTTPException(404, "Benutzer nicht gefunden")
        owner = u.id
    prefix = secrets.token_hex(6)
    raw = f"sbc_{prefix}_{secrets.token_urlsafe(32)}"
    k = ApiKey(org_id=user.org_id, user_id=owner, name=body.name, prefix=prefix,
               key_hash=hashlib.sha256(raw.encode()).hexdigest())
    db.add(k)
    db.flush()
    audit(db, user, "api_key_created", "api_key", k.id, {"name": body.name}, request)
    db.commit()
    return {"id": str(k.id), "name": k.name, "key": raw}


@router.get("/api-keys")
def list_api_keys(user: CurrentUser = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    from sbc.models import ApiKey

    return [row(k, {"key_hash"}) for k in db.scalars(select(ApiKey).where(ApiKey.org_id == user.org_id))]


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, request: Request, user: CurrentUser = Depends(require(Role.admin)),
                   db: Session = Depends(get_db)):
    import uuid

    from sbc.models import ApiKey

    k = db.get(ApiKey, uuid.UUID(key_id))
    if k is None or k.org_id != user.org_id:
        raise HTTPException(404, "Schlüssel nicht gefunden")
    k.revoked_at = datetime.now(UTC)
    audit(db, user, "api_key_revoked", "api_key", k.id, {}, request)
    db.commit()
    return {"revoked": True}
