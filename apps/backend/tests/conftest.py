import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="sbc-test-")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": "sqlite://",
    "STORAGE_BACKEND": "local",
    "LOCAL_STORAGE_DIR": str(Path(_TMP) / "storage"),
    "APP_SECRET": "test-secret-0123456789abcdef-0123456789",
    "KEEPA_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "REDIS_URL": "",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from sbc import db as dbmod  # noqa: E402
from sbc.models import Base, Organization, Role, User  # noqa: E402
from sbc.profit.fees import seed_fee_profiles  # noqa: E402
from sbc.security.auth import hash_password  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")  # z. B. Postgres in CI


@pytest.fixture()
def engine():
    if TEST_DATABASE_URL:
        # Gleiche Tests gegen echtes PostgreSQL (strengeres SQL als SQLite, z. B. GROUP BY)
        url = TEST_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
    else:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(eng, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(eng)
    dbmod.set_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    s = dbmod.SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def org(db):
    o = Organization(name="Test GmbH")
    db.add(o)
    db.flush()
    seed_fee_profiles(db, o.id)
    db.commit()
    return o


def _user(db, org, email, role):
    u = User(org_id=org.id, email=email, password_hash=hash_password("Sicher-Passwort-1"), role=role)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def admin(db, org):
    return _user(db, org, "admin@test.de", Role.admin)


@pytest.fixture()
def client(engine, admin):
    from sbc.api.main import app

    c = TestClient(app)
    tok = c.post("/api/v1/auth/login", json={"email": "admin@test.de", "password": "Sicher-Passwort-1"}).json()
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    return c
