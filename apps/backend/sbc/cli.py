"""Verwaltungsbefehle.

  python -m sbc.cli bootstrap        # Org + Admin (aus INITIAL_ADMIN_*), Gebührenprofile
  python -m sbc.cli create-user EMAIL ROLE   (Passwort über SBC_NEW_PASSWORD)
  python -m sbc.cli load-demo        # klar gekennzeichnete TESTDATEN (nicht in Produktion)
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

from sbc.config import get_settings
from sbc.db import session_scope
from sbc.models import Organization, Role, User
from sbc.profit.fees import seed_fee_profiles
from sbc.security.auth import hash_password


def bootstrap() -> None:
    s = get_settings()
    with session_scope() as db:
        org = db.scalar(select(Organization).order_by(Organization.created_at))
        if org is None:
            org = Organization(name=s.initial_org_name)
            db.add(org)
            db.flush()
            print(f"Organisation angelegt: {org.name}")
        created = seed_fee_profiles(db, org.id)
        if created:
            print(f"{created} Gebührenprofile (Annahmen) angelegt")
        users = db.scalar(select(func.count()).select_from(User)) or 0
        email = (s.initial_admin_email or "").strip().lower()
        if not (email and s.initial_admin_password):
            if users == 0:
                print("WARNUNG: Kein Benutzer vorhanden. INITIAL_ADMIN_EMAIL/INITIAL_ADMIN_PASSWORD setzen.")
            else:
                print(f"Bootstrap: {users} Benutzer vorhanden, kein INITIAL_ADMIN_* gesetzt.")
            return
        if db.scalar(select(User).where(func.lower(User.email) == email)) is not None:
            # Bestehende Konten werden nie verändert (kein Passwort-Reset über Umgebungsvariablen)
            print(f"Bootstrap: Admin {email} existiert bereits ({users} Benutzer). INITIAL_ADMIN_PASSWORD kann entfernt werden.")
            return
        try:
            pw_hash = hash_password(s.initial_admin_password)
        except ValueError as e:
            print(f"FEHLER: Admin {email} nicht angelegt – {e}")
            return
        db.add(User(org_id=org.id, email=email, name="Admin", password_hash=pw_hash, role=Role.admin))
        print(f"Admin angelegt: {email}")


def create_user(email: str, role: str) -> None:
    pw = os.environ.get("SBC_NEW_PASSWORD")
    if not pw:
        sys.exit("SBC_NEW_PASSWORD setzen")
    with session_scope() as db:
        org = db.scalar(select(Organization).order_by(Organization.created_at))
        db.add(User(org_id=org.id, email=email.lower(), password_hash=hash_password(pw), role=Role(role)))
    print("ok")


def load_demo() -> None:
    if get_settings().app_env == "production":
        sys.exit("Demo-Daten sind in Produktion gesperrt")
    from sbc.demo import load

    with session_scope() as db:
        print(load(db))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "bootstrap":
        bootstrap()
    elif cmd == "create-user":
        create_user(sys.argv[2], sys.argv[3])
    elif cmd == "load-demo":
        load_demo()
    else:
        print(__doc__)
