"""Migrationen gegen eine Datei-DB mit Bestandsdaten: nichts geht verloren, Übernahmen sind korrekt."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parents[1]


def _alembic(url: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]


def _filler(col: sa.Column):
    t = col.type
    if isinstance(t, sa.Uuid) or "uuid" in str(t).lower() or col.name.endswith("_id") or col.name == "id":
        return uuid.uuid4().hex
    if isinstance(t, (sa.Integer, sa.Numeric)):
        return 1
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, sa.DateTime):
        return datetime.now(UTC)
    if isinstance(t, sa.JSON):
        return "{}"
    return "x"


def _insert(conn, meta, table: str, **values):
    tbl = meta.tables[table]
    row = {}
    for c in tbl.columns:
        if c.name in values:
            row[c.name] = values[c.name]
        elif not c.nullable and c.server_default is None:
            row[c.name] = _filler(c)
    conn.execute(tbl.insert().values(**row))
    return row


def test_0003_keeps_data_and_backfills_decisions(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    _alembic(url, "upgrade", "0002")
    eng = sa.create_engine(url)
    meta = sa.MetaData()
    meta.reflect(eng)
    ids = {k: uuid.uuid4().hex for k in ("org", "sup", "prod", "offer", "lst", "mr1", "mr2", "lst2")}
    with eng.begin() as c:
        _insert(c, meta, "organizations", id=ids["org"], name="T")
        _insert(c, meta, "suppliers", id=ids["sup"], org_id=ids["org"], code="S", name="S")
        _insert(c, meta, "products", id=ids["prod"], org_id=ids["org"], title="P")
        _insert(c, meta, "supplier_offers", id=ids["offer"], org_id=ids["org"], supplier_id=ids["sup"],
                product_id=ids["prod"], offer_key="K")
        for lst in ("lst", "lst2"):
            _insert(c, meta, "marketplace_listings", id=ids[lst], org_id=ids["org"], marketplace="amazon",
                    country="DE", external_id=lst, source="fixture")
        _insert(c, meta, "matching_results", id=ids["mr1"], org_id=ids["org"], supplier_offer_id=ids["offer"],
                listing_id=ids["lst"], classification="MULTIPACK", review_status="confirmed", units_required=2,
                score=0.8, confidence=0.8)
        _insert(c, meta, "matching_results", id=ids["mr2"], org_id=ids["org"], supplier_offer_id=ids["offer"],
                listing_id=ids["lst2"], classification="MULTIPACK", review_status="auto_accepted",
                score=0.8, confidence=0.8)

    _alembic(url, "upgrade", "head")
    with eng.connect() as c:
        dec = dict(c.execute(sa.text("SELECT review_status, decision_status FROM matching_results")).fetchall())
        assert dec == {"confirmed": "VERIFIED_MATCH", "auto_accepted": "MANUAL_REVIEW"}
        maps = c.execute(sa.text("SELECT status, relation, units_required, source FROM asin_mappings")).fetchall()
        assert [tuple(m) for m in maps] == [("VERIFIED", "MULTIPACK", 2, "manual")]
        assert c.execute(sa.text("SELECT count(*) FROM supplier_offers")).scalar() == 1

    # Erneutes Upgrade ist ein No-op; Downgrade und Upgrade funktionieren
    _alembic(url, "downgrade", "0002")
    _alembic(url, "upgrade", "head")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM asin_mappings")).scalar() == 1
