"""End-to-End über die API: Lieferant → Import → Matching-Review → Opportunity → Einkaufsliste."""

import io
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import openpyxl

from sbc.importer.service import process_job
from sbc.models import (
    ImportRow,
    ImportStatus,
    MatchClass,
    MatchingFeedback,
    MatchingResult,
    PriceHistory,
    Role,
    SupplierOffer,
)
from tests.conftest import _user


def _supplier(client, code="L1"):
    r = client.post("/api/v1/suppliers", json={"code": code, "name": f"Lieferant {code}", "currency": "EUR",
                                               "shipping_cost_per_unit": "0.30", "lead_time_days": 5})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _xlsx(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Preisliste Testlieferant"])
    ws.append([])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, sid, data, name="liste.xlsx"):
    return client.post("/api/v1/imports", data={"supplier_id": sid}, files={"file": (name, data)})


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/health/ready").json()["checks"]["database"] == "ok"


def test_auth_required_and_roles(client, db, org):
    from fastapi.testclient import TestClient

    from sbc.api.main import app

    anon = TestClient(app)
    assert anon.get("/api/v1/suppliers").status_code == 401
    _user(db, org, "viewer@test.de", Role.viewer)
    tok = anon.post("/api/v1/auth/login", json={"email": "viewer@test.de", "password": "Sicher-Passwort-1"}).json()
    anon.headers["Authorization"] = f"Bearer {tok['access_token']}"
    assert anon.get("/api/v1/suppliers").status_code == 200
    assert anon.post("/api/v1/suppliers", json={"code": "X", "name": "X"}).status_code == 403
    assert anon.post("/api/v1/auth/login", json={"email": "viewer@test.de", "password": "falsch"}).status_code == 401


def test_import_flow_xlsx(client, db):
    sid = _supplier(client)
    data = _xlsx([
        ["EAN", "Artikelnummer", "Bezeichnung", "Marke", "EK netto", "Bestand"],
        ["4011558013301", "MF-1", "MANN-FILTER Ölfilter W 712/95", "MANN-FILTER", "3,10", 800],
        [12345678905, "OLD-0", "Artikel mit verlorener Null", "X", "1,00", 5],   # EAN ohne führende Null
        ["4006381333932", "BAD-EAN", "Falsche Prüfziffer", "Y", "2,00", 1],       # Warnung
        [None, "NOPRICE", "Ohne Preis", "Z", None, 1],                           # Fehler
        ["4011558013301", "MF-1", "Dublette", "MANN-FILTER", "3,10", 800],       # Dublette
    ])
    r = _upload(client, sid, data)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "mapping_review" and job["header_row"] == 2
    mapping = job["column_mapping"]
    assert mapping == {"EAN": "gtin", "Artikelnummer": "supplier_sku", "Bezeichnung": "title",
                       "Marke": "brand", "EK netto": "net_price", "Bestand": "stock"}

    assert _upload(client, sid, data).status_code == 409  # identische Datei

    r = client.put(f"/api/v1/imports/{job['id']}/mapping", json={"mapping": mapping, "start": True})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    j = process_job(db, job["id"])  # Worker-Ausführung synchron
    assert j.status == ImportStatus.completed_with_errors
    assert j.error_rows == 1 and j.stats["duplicates"] == 1 and j.stats["new_offers"] == 3
    old = db.query(SupplierOffer).filter_by(supplier_sku="OLD-0").one()
    assert old.gtin == "00012345678905"
    detail = client.get(f"/api/v1/imports/{job['id']}").json()
    assert detail["row_counts"] == {"ok": 3, "error": 1, "duplicate": 1}
    errs = client.get(f"/api/v1/imports/{job['id']}/rows", params={"status": "error"}).json()
    assert "Kein Einkaufspreis" in errs[0]["errors"][0]

    # Zweiter Import mit Preisänderung nutzt das gespeicherte Profil automatisch
    data2 = _xlsx([["EAN", "Artikelnummer", "Bezeichnung", "Marke", "EK netto", "Bestand"],
                   ["4011558013301", "MF-1", "MANN-FILTER Ölfilter W 712/95", "MANN-FILTER", "2,90", 700]])
    job2 = _upload(client, sid, data2, "liste2.xlsx").json()
    assert job2["analysis"]["profile_applied"] is True
    client.put(f"/api/v1/imports/{job2['id']}/mapping", json={"mapping": job2["column_mapping"]})
    process_job(db, job2["id"])
    mf = db.query(SupplierOffer).filter_by(supplier_sku="MF-1").one()
    assert mf.unit_price_net == Decimal("2.9") and mf.previous_unit_price_net == Decimal("3.1")
    assert db.query(PriceHistory).filter_by(subject_id=mf.id).count() == 2
    assert db.query(SupplierOffer).filter_by(supplier_sku="OLD-0").one().is_current is False
    changes = client.get(f"/api/v1/suppliers/{sid}/price-history").json()
    assert changes[0]["previous"] == "3.1000" or changes[0]["previous"].startswith("3.1")


def test_import_resume_is_idempotent(client, db, monkeypatch):
    sid = _supplier(client, "L2")
    rows = [["SKU", "EK"]] + [[f"A{i}", "1,00"] for i in range(1200)]
    job = _upload(client, sid, _xlsx(rows)).json()
    client.put(f"/api/v1/imports/{job['id']}/mapping",
               json={"mapping": {"SKU": "supplier_sku", "EK": "net_price"}, "start": False})
    from sbc.importer import service

    calls = {"n": 0}
    orig = service._process_batch

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("Worker abgestürzt")
        return orig(*a, **kw)

    monkeypatch.setattr(service, "_process_batch", flaky)
    j = process_job(db, job["id"])
    assert j.status == ImportStatus.failed and j.last_processed_row == 499
    monkeypatch.setattr(service, "_process_batch", orig)
    j = process_job(db, job["id"])
    assert j.status == ImportStatus.completed
    assert db.query(ImportRow).filter_by(job_id=j.id).count() == 1200
    assert db.query(SupplierOffer).filter(SupplierOffer.supplier_id == j.supplier_id).count() == 1200


def test_upload_rejects_disguised_file(client):
    sid = _supplier(client, "L3")
    r = _upload(client, sid, b"MZ\x90\x00binary", "liste.csv")
    assert r.status_code == 422
    r = _upload(client, sid, b"not a zip", "liste.xlsx")
    assert r.status_code == 422


def test_csv_semicolon_and_leading_zeros(client, db):
    sid = _supplier(client, "L4")
    csv = "EAN;SKU;Name;EK\n0012345678905;S1;Test;1,99\n".encode("cp1252")
    job = _upload(client, sid, csv, "liste.csv").json()
    client.put(f"/api/v1/imports/{job['id']}/mapping", json={"mapping": job["column_mapping"]})
    process_job(db, job["id"])
    o = db.query(SupplierOffer).filter_by(supplier_sku="S1").one()
    assert o.gtin == "00012345678905" and o.unit_price_net == Decimal("1.99")


def test_demo_review_and_purchase_flow(client, db, org):
    from sbc.demo import load

    load(db)
    db.commit()
    # Testdaten erscheinen nicht in der Standardansicht
    assert client.get("/api/v1/opportunities", params={"include_excluded": True}).json()["total"] == 0
    all_test = client.get("/api/v1/opportunities", params={"include_excluded": True, "include_test": True}).json()
    assert all_test["total"] == 3 and all(i["is_test_data"] for i in all_test["items"])

    casio = next(i for i in all_test["items"] if i["gtin"] == "04971850436140")
    assert casio["match_class"] == "MULTIPACK" and casio["units_required"] == 2
    # Multipack ist ohne Bestätigung nicht einkaufbar
    r = client.post("/api/v1/opportunities/purchase-list", json={"ids": [casio["id"]]})
    assert r.json()["created"] == [] and "nicht manuell bestätigt" in r.json()["skipped"][0]["reason"]

    mr = db.query(MatchingResult).join(SupplierOffer).filter(SupplierOffer.gtin == "04971850436140").one()
    r = client.post(f"/api/v1/matching/{mr.id}/review", json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["review_status"] == "confirmed"
    assert db.query(MatchingFeedback).filter_by(matching_result_id=mr.id).one().is_positive is True

    detail = client.get(f"/api/v1/opportunities/{casio['id']}").json()
    assert detail["calculations"]["current"]["outputs"]["goods_cost"] == "40.6000"  # 2 × (20 + 0,30 Versand)
    assert detail["calculations"]["ref_90d"] is not None
    assert not detail["is_excluded"], detail["exclusion_reasons"]

    r = client.post("/api/v1/opportunities/purchase-list", json={"ids": [casio["id"]]})
    assert len(r.json()["created"]) == 1, r.json()
    items = client.get("/api/v1/opportunities/purchase-list/items").json()
    assert items[0]["status"] == "proposed" and items[0]["quantity"] % 2 == 0

    # Stabilo: Bestand 0 → ausgeschlossen
    stab = next(i for i in all_test["items"] if i["gtin"] == "04006381333931")
    assert any("Bestand 0" in r for r in stab["exclusion_reasons"])

    # Export enthält EAN als Text
    exp = client.get("/api/v1/opportunities/export", params={"include_test": True, "include_excluded": True})
    assert exp.status_code == 200 and "04971850436140" in exp.text


def test_reject_match_removes_opportunity_from_list(client, db, org):
    from sbc.demo import load

    load(db)
    db.commit()
    mr = db.query(MatchingResult).join(SupplierOffer).filter(SupplierOffer.gtin == "04011558013301").one()
    client.post(f"/api/v1/matching/{mr.id}/review", json={"action": "reject"})
    db.expire_all()
    assert db.get(MatchingResult, mr.id).classification == MatchClass.NO_MATCH


def test_settings_validation(client):
    r = client.put("/api/v1/settings/score_weights", json={"value": {"profit": "0.5", "demand": "0.6"}})
    assert r.status_code == 422
    r = client.put("/api/v1/settings/purchasing", json={"value": {"auto_order_enabled": True}})
    assert r.status_code == 422
    r = client.put("/api/v1/settings/thresholds", json={"value": {"min_margin": "0.15"}})
    assert r.json()["min_margin"] == "0.15" and r.json()["min_contribution"] == "5.00"


def test_alembic_migrations_apply(tmp_path):
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'm.db'}", "APP_ENV": "test", "PATH": ""}
    import os

    env = {**os.environ, **env}
    backend = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_automation_endpoints(client, db, org):
    sid = _supplier(client, "MAIL")
    from sbc.models import Supplier

    s = db.get(Supplier, __import__("uuid").UUID(sid))
    s.email_import_rules = {"domains": ["lieferant.example"], "auto_start": True}
    db.commit()
    r = client.get("/api/v1/suppliers-lookup/by-email", params={"sender": "Preise@Lieferant.example"})
    assert r.status_code == 200 and r.json()["supplier_id"] == sid and r.json()["auto_start"] is True
    assert client.get("/api/v1/suppliers-lookup/by-email", params={"sender": "x@unknown.example"}).status_code == 404
    assert client.post("/api/v1/pipeline/analyze-pending", json={"limit": 10}).json() == {"queued": 0}
    assert client.post("/api/v1/pipeline/recalculate").json() == {"queued": True}
    rep = client.get("/api/v1/reports/daily").json()
    assert rep["count"] == 0 and "manuelle Freigabe" in rep["text"]


def test_api_keys(client):
    from fastapi.testclient import TestClient

    from sbc.api.main import app

    created = client.post("/api/v1/auth/api-keys", json={"name": "n8n"}).json()
    assert created["key"].startswith("sbc_")
    anon = TestClient(app)
    assert anon.get("/api/v1/suppliers", headers={"X-API-Key": created["key"]}).status_code == 200
    assert anon.get("/api/v1/suppliers", headers={"X-API-Key": created["key"] + "x"}).status_code == 401
    client.delete(f"/api/v1/auth/api-keys/{created['id']}")
    assert anon.get("/api/v1/suppliers", headers={"X-API-Key": created["key"]}).status_code == 401
    assert "key_hash" not in client.get("/api/v1/auth/api-keys").json()[0]


def test_bootstrap_creates_missing_admin_and_never_resets(db, org, monkeypatch, capsys):
    from sbc import cli
    from sbc.config import get_settings
    from sbc.models import User
    from sbc.security.auth import verify_password

    _user(db, org, "andere@test.de", Role.viewer)
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "Chef@Test.de")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "kurz")
    get_settings.cache_clear()
    cli.bootstrap()
    assert "mindestens 10 Zeichen" in capsys.readouterr().out
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Langes-Passwort-1")
    get_settings.cache_clear()
    cli.bootstrap()
    assert "Admin angelegt: chef@test.de" in capsys.readouterr().out
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Anderes-Passwort-2")
    get_settings.cache_clear()
    cli.bootstrap()
    assert "existiert bereits" in capsys.readouterr().out
    db.expire_all()
    admin = db.query(User).filter_by(email="chef@test.de").one()
    assert admin.role == Role.admin and verify_password("Langes-Passwort-1", admin.password_hash)
    get_settings.cache_clear()
