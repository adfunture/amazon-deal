"""Import-Workflow: Analyse → Zuordnung prüfen → Verarbeitung in Batches (idempotent, fortsetzbar)."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from itertools import islice
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sbc.importer.columns import FIELDS, detect_header_row, guess_mapping, header_signature
from sbc.importer.normalize import ImportOptions, NormalizedOffer, normalize_row
from sbc.importer.readers import extract_zip, iter_rows, list_sheets
from sbc.integrations.storage import get_storage
from sbc.models import (
    IdentifierType,
    ImportJob,
    ImportProfile,
    ImportRow,
    ImportStatus,
    PriceHistory,
    Product,
    ProductIdentifier,
    RowStatus,
    Supplier,
    SupplierOffer,
)
from sbc.domain.text import normalize_text

log = logging.getLogger(__name__)
BATCH_SIZE = 500
PREVIEW_ROWS = 30


def _cell(v: object) -> object:
    """JSON-taugliche Rohwerte (Datum → ISO)."""
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()  # type: ignore[union-attr]
    if isinstance(v, Decimal):
        return str(v)
    return v


def _materialize(job: ImportJob, workdir: Path) -> tuple[Path, str]:
    """Lädt die Datei aus dem Objektspeicher in ein temporäres Verzeichnis (nicht dauerhaft)."""
    target = workdir / Path(job.file_name).name
    get_storage().download_to(job.file_key, target)
    if job.file_type == "zip":
        inner = extract_zip(target, workdir)
        from sbc.importer.readers import file_type_of

        return inner, file_type_of(inner.name)
    return target, job.file_type


def _headers_from(row: list[object]) -> list[str]:
    headers, seen = [], {}
    for i, c in enumerate(row):
        h = str(c).strip() if c not in (None, "") else f"Spalte {i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
        headers.append(h)
    return headers


def analyze_job(db: Session, job: ImportJob) -> dict[str, Any]:
    """Erkennt Blatt, Kopfzeile und Spaltenzuordnung; wendet ein passendes Profil an."""
    with tempfile.TemporaryDirectory() as tmp:
        path, ftype = _materialize(job, Path(tmp))
        sheets = list_sheets(path, ftype)
        sheet = job.sheet_name or (sheets[0].name if ftype in {"xlsx", "xlsm", "xls"} else None)
        if ftype in {"xlsx", "xlsm", "xls"} and not job.sheet_name:
            # Blatt mit den meisten erkannten Spaltenköpfen wählen
            best = None
            for sh in sheets:
                rows = list(islice(iter_rows(path, ftype, sh.name), 25))
                if not rows:
                    continue
                score = max((sum(1 for c in r if c not in (None, "")) for r in rows), default=0)
                hdr = detect_header_row(rows)
                from sbc.importer.columns import header_score

                s2 = header_score(rows[hdr]) * 10 + score
                if best is None or s2 > best[0]:
                    best = (s2, sh.name)
            sheet = best[1] if best else sheet
        preview = list(islice(iter_rows(path, ftype, sheet), 25 + PREVIEW_ROWS))
    if not preview:
        raise ValueError("Datei enthält keine Daten")
    header_idx = detect_header_row(preview)
    headers = _headers_from(preview[header_idx])
    samples = [list(r) for r in preview[header_idx + 1: header_idx + 1 + PREVIEW_ROWS]]
    guesses = guess_mapping(headers, samples)
    mapping = {g.header: g.field for g in guesses if g.field}
    sources = {g.header: {"method": g.method, "confidence": g.confidence} for g in guesses}

    profile = None
    sig = header_signature(headers)
    if job.profile_id:
        profile = db.get(ImportProfile, job.profile_id)
    if profile is None:
        profile = db.scalar(select(ImportProfile).where(
            ImportProfile.supplier_id == job.supplier_id, ImportProfile.header_signature == sig))
    if profile is not None:
        mapping = {h: f for h, f in profile.column_mapping.items() if h in headers}
        sources = {h: {"method": "profile", "confidence": 1.0} for h in mapping}
        job.profile_id = profile.id
        job.options = {**profile.options, **(job.options or {})}

    job.sheet_name = sheet
    job.header_row = header_idx
    job.detected_columns = [
        {"header": h, "samples": [_cell(r[i]) if i < len(r) else None for r in samples[:5]]}
        for i, h in enumerate(headers)
    ]
    job.column_mapping = mapping
    job.mapping_source = sources
    job.status = ImportStatus.mapping_review
    db.flush()
    return {"headers": headers, "mapping": mapping, "profile_applied": profile is not None,
            "header_signature": sig}


def validate_mapping(mapping: dict[str, str]) -> list[str]:
    errors = []
    fields = [f for f in mapping.values() if f and f != "ignore"]
    unknown = [f for f in fields if f not in FIELDS]
    if unknown:
        errors.append(f"Unbekannte Zielfelder: {', '.join(unknown)}")
    if not any(f in fields for f in ("net_price", "gross_price", "tier_price")):
        errors.append("Es muss eine Preisspalte zugeordnet sein")
    if not any(f in fields for f in ("gtin", "supplier_sku", "mpn")):
        errors.append("Es muss EAN, SKU oder MPN zugeordnet sein")
    dupes = {f for f in fields if f != "tier_price" and fields.count(f) > 1}
    if dupes:
        errors.append(f"Felder mehrfach zugeordnet: {', '.join(sorted(dupes))}")
    return errors


def save_profile(db: Session, job: ImportJob, name: str | None = None) -> ImportProfile:
    headers = [c["header"] for c in job.detected_columns]
    sig = header_signature(headers)
    profile = db.scalar(select(ImportProfile).where(
        ImportProfile.supplier_id == job.supplier_id, ImportProfile.header_signature == sig))
    if profile is None:
        profile = ImportProfile(org_id=job.org_id, supplier_id=job.supplier_id,
                                name=name or f"Profil {job.file_name}", header_signature=sig,
                                column_mapping={})
        db.add(profile)
    profile.column_mapping = job.column_mapping
    profile.sheet_name = job.sheet_name
    profile.header_row = job.header_row
    profile.options = job.options or {}
    db.flush()
    job.profile_id = profile.id
    return profile


def _row_hash(raw: dict) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()


def _find_or_create_product(db: Session, org_id, supplier: Supplier, n: NormalizedOffer) -> Product:
    """EAN ist kein globaler Schlüssel: Treffer nur, wenn auch Verkaufseinheit/Zustand passen."""
    candidates: list[Product] = []
    if n.gtin:
        candidates = list(db.scalars(
            select(Product).join(ProductIdentifier).where(
                ProductIdentifier.org_id == org_id,
                ProductIdentifier.id_type == IdentifierType.GTIN,
                ProductIdentifier.value == n.gtin,
            )
        ).unique())
    if not candidates and n.mpn_normalized and n.brand:
        candidates = [p for p in db.scalars(
            select(Product).join(ProductIdentifier).where(
                ProductIdentifier.org_id == org_id,
                ProductIdentifier.id_type == IdentifierType.MPN,
                ProductIdentifier.value == n.mpn_normalized,
            )
        ).unique() if normalize_text(p.brand) == normalize_text(n.brand)]
    for p in candidates:
        if p.unit_quantity == n.unit_quantity and p.condition == n.condition:
            return p
    p = Product(org_id=org_id, title=n.title or n.mpn or n.supplier_sku or n.gtin or "Unbenannt",
                brand=n.brand, mpn=n.mpn, category=n.category, description=n.description,
                unit_quantity=n.unit_quantity, condition=n.condition,
                normalized_title=normalize_text(n.title)[:500] if n.title else None,
                attributes={k: v for k, v in (("color", n.color), ("size", n.size)) if v})
    db.add(p)
    db.flush()
    ids = []
    if n.gtin:
        ids.append((IdentifierType.GTIN, n.gtin, n.gtin_raw, ""))
    if n.mpn_normalized:
        ids.append((IdentifierType.MPN, n.mpn_normalized, n.mpn, ""))
    if n.supplier_sku:
        ids.append((IdentifierType.SUPPLIER_SKU, n.supplier_sku, n.supplier_sku, str(supplier.id)))
    if n.asin:
        ids.append((IdentifierType.ASIN, n.asin, n.asin, "amazon"))
    for t, v, raw, scope in ids:
        db.add(ProductIdentifier(org_id=org_id, product_id=p.id, id_type=t, value=v[:100],
                                 raw_value=(raw or "")[:100] or None, scope=scope, source="supplier_import"))
    return p


def _upsert_offer(db: Session, job: ImportJob, supplier: Supplier, n: NormalizedOffer,
                  raw: dict, now: datetime) -> SupplierOffer:
    offer = db.scalar(select(SupplierOffer).where(
        SupplierOffer.supplier_id == supplier.id, SupplierOffer.offer_key == n.offer_key))
    chash = n.content_hash()
    if offer is None:
        product = _find_or_create_product(db, job.org_id, supplier, n)
        offer = SupplierOffer(org_id=job.org_id, supplier_id=supplier.id, offer_key=n.offer_key,
                              product_id=product.id, first_seen_at=now, content_hash="")
        db.add(offer)
        price_changed = True
    else:
        price_changed = offer.unit_price_net != n.unit_price_net and n.unit_price_net is not None
        if price_changed and offer.unit_price_net is not None:
            offer.previous_unit_price_net = offer.unit_price_net
            offer.last_price_change_at = now
    offer.last_import_job_id = job.id
    offer.supplier_sku = n.supplier_sku
    offer.gtin = n.gtin
    offer.mpn = n.mpn
    offer.title = n.title
    offer.brand = n.brand
    offer.unit_price_net = n.unit_price_net
    offer.currency = n.currency or supplier.currency
    offer.price_basis_qty = n.price_basis_qty
    offer.unit_quantity = n.unit_quantity
    offer.tier_prices = n.to_json()["tier_prices"]
    offer.rrp_gross = n.rrp_gross
    offer.stock_qty = n.stock_qty
    offer.moq = n.moq or supplier.default_moq
    offer.lead_time_days = n.lead_time_days or supplier.lead_time_days
    offer.condition = n.condition
    offer.image_urls = n.image_urls
    offer.is_current = True
    offer.content_hash = chash
    offer.raw = raw
    offer.last_seen_at = now
    db.flush()
    if price_changed and n.unit_price_net is not None:
        db.add(PriceHistory(org_id=job.org_id, subject_type="supplier_offer", subject_id=offer.id,
                            metric="supplier_net", value=n.unit_price_net, currency=offer.currency,
                            tax_basis="net", observed_at=now, source=f"import:{job.id}"))
    return offer


def process_job(db: Session, job_id) -> ImportJob:
    """Verarbeitet einen Importjob. Idempotent: bereits verarbeitete Zeilen werden übersprungen,
    nach Abbruch setzt der Job bei ``last_processed_row + 1`` fort."""
    if isinstance(job_id, str):
        job_id = uuid.UUID(job_id)
    job = db.get(ImportJob, job_id)
    if job is None:
        raise ValueError("Importjob nicht gefunden")
    if job.status in (ImportStatus.completed, ImportStatus.completed_with_errors):
        return job
    errs = validate_mapping(job.column_mapping)
    if errs:
        job.status, job.error = ImportStatus.failed, "; ".join(errs)
        db.commit()
        return job
    supplier = db.get(Supplier, job.supplier_id)
    assert supplier is not None
    opts = ImportOptions.from_dict({"currency": supplier.currency,
                                    "prices_are_gross": supplier.prices_include_vat, **(job.options or {})})
    job.status = ImportStatus.processing
    job.started_at = job.started_at or datetime.now(UTC)
    job.error = None
    db.commit()

    seen_keys: set[str] = set(db.scalars(
        select(SupplierOffer.offer_key).where(SupplierOffer.last_import_job_id == job.id)))
    stats = dict(job.stats or {})
    stats.setdefault("new_offers", 0)
    stats.setdefault("price_changes", 0)
    stats.setdefault("duplicates", 0)
    stats.setdefault("warnings", 0)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, ftype = _materialize(job, Path(tmp))
            rows_iter = iter_rows(path, ftype, job.sheet_name)
            header_row = None
            for idx, row in enumerate(rows_iter):
                if idx == job.header_row:
                    header_row = _headers_from(row)
                    break
            if header_row is None:
                raise ValueError("Kopfzeile nicht gefunden")
            data_idx = -1
            batch: list[tuple[int, list[object]]] = []
            total = 0
            for row in rows_iter:
                data_idx += 1
                if all(c in (None, "") for c in row):
                    continue
                total += 1
                if data_idx <= job.last_processed_row:
                    continue
                batch.append((data_idx, row))
                if len(batch) >= BATCH_SIZE:
                    _process_batch(db, job, supplier, header_row, batch, opts, seen_keys, stats)
                    batch = []
            if batch:
                _process_batch(db, job, supplier, header_row, batch, opts, seen_keys, stats)
            job.total_rows = total

        if (job.options or {}).get("replaces_previous", True):
            res = db.execute(update(SupplierOffer).where(
                SupplierOffer.supplier_id == supplier.id,
                SupplierOffer.last_import_job_id != job.id,
                SupplierOffer.is_current.is_(True),
            ).values(is_current=False))
            stats["offers_no_longer_listed"] = res.rowcount or 0
        job.stats = stats
        job.status = ImportStatus.completed_with_errors if job.error_rows else ImportStatus.completed
        job.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as e:  # Job bleibt fortsetzbar
        db.rollback()
        job = db.get(ImportJob, job_id)
        assert job is not None
        job.status = ImportStatus.failed
        job.error = f"{e.__class__.__name__}: {e}"[:2000]
        db.commit()
        log.exception("Import %s fehlgeschlagen", job_id)
    return job


def _process_batch(db: Session, job: ImportJob, supplier: Supplier, headers: list[str],
                   batch: list[tuple[int, list[object]]], opts: ImportOptions,
                   seen_keys: set[str], stats: dict) -> None:
    now = datetime.now(UTC)
    existing_rows = set(db.scalars(select(ImportRow.row_number).where(
        ImportRow.job_id == job.id, ImportRow.row_number.in_([i for i, _ in batch]))))
    for idx, row in batch:
        if idx in existing_rows:
            continue
        raw = {h: _cell(row[i]) if i < len(row) else None for i, h in enumerate(headers)}
        n = normalize_row(raw, job.column_mapping, opts)
        status = RowStatus.ok
        offer_id = None
        if n.errors:
            status = RowStatus.error
            job.error_rows += 1
        elif n.offer_key in seen_keys:
            status = RowStatus.duplicate
            n.warnings.append(f"Dublette innerhalb der Datei (Schlüssel {n.offer_key})")
            stats["duplicates"] += 1
        else:
            seen_keys.add(n.offer_key)  # type: ignore[arg-type]
            before = db.scalar(select(SupplierOffer.unit_price_net).where(
                SupplierOffer.supplier_id == supplier.id, SupplierOffer.offer_key == n.offer_key))
            existed = db.scalar(select(SupplierOffer.id).where(
                SupplierOffer.supplier_id == supplier.id, SupplierOffer.offer_key == n.offer_key))
            offer = _upsert_offer(db, job, supplier, n, raw, now)
            offer_id = offer.id
            if existed is None:
                stats["new_offers"] += 1
            elif before is not None and n.unit_price_net is not None and before != n.unit_price_net:
                stats["price_changes"] += 1
        if n.warnings:
            stats["warnings"] += 1
        db.add(ImportRow(job_id=job.id, row_number=idx, raw=raw, normalized=n.to_json(),
                         status=status, errors=n.errors, warnings=n.warnings,
                         row_hash=_row_hash(raw), supplier_offer_id=offer_id))
        job.processed_rows += 1
    job.last_processed_row = batch[-1][0]
    job.stats = dict(stats)
    db.commit()
