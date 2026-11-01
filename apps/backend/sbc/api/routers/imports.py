from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import row
from sbc.config import get_settings
from sbc.db import get_db
from sbc.importer.columns import FIELDS
from sbc.importer.service import analyze_job, save_profile, validate_mapping
from sbc.integrations.storage import get_storage
from sbc.models import ImportJob, ImportRow, ImportStatus, Role, RowStatus, Supplier
from sbc.security.uploads import UploadRejected, clamav_scan, validate_upload

router = APIRouter(prefix="/imports", tags=["imports"])
CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", "xls": "application/vnd.ms-excel",
    "csv": "text/csv", "tsv": "text/tab-separated-values", "json": "application/json",
    "xml": "application/xml", "zip": "application/zip",
}


def _job(db: Session, user: CurrentUser, job_id: uuid.UUID) -> ImportJob:
    j = db.get(ImportJob, job_id)
    if j is None or j.org_id != user.org_id:
        raise HTTPException(404, "Importjob nicht gefunden")
    return j


def _progress(j: ImportJob) -> dict:
    d = row(j)
    d["progress"] = (round(j.processed_rows / j.total_rows, 4) if j.total_rows else None)
    return d


@router.get("/fields")
def fields():
    return [{"key": k, "label": v} for k, v in FIELDS.items()]


@router.post("", status_code=201)
async def upload(request: Request, supplier_id: uuid.UUID = Form(...), file: UploadFile = File(...),
                 force: bool = Form(False), user: CurrentUser = Depends(require(Role.buyer)),
                 db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or supplier.org_id != user.org_id:
        raise HTTPException(404, "Lieferant nicht gefunden")
    settings = get_settings()
    name = Path(file.filename or "upload").name[:200]
    limit = settings.max_upload_mb * 1024 * 1024
    sha = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        head = b""
        while chunk := await file.read(1024 * 1024):
            if not head:
                head = chunk[:8192]
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"Datei größer als {settings.max_upload_mb} MB")
            sha.update(chunk)
            tmp.write(chunk)
        tmp_path = tmp.name
    try:
        ftype = validate_upload(name, head, size, settings.max_upload_mb)
        scan = clamav_scan(tmp_path)
    except UploadRejected as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(422, str(e)) from e
    digest = sha.hexdigest()
    dup = db.scalar(select(ImportJob).where(ImportJob.supplier_id == supplier_id,
                                            ImportJob.file_sha256 == digest,
                                            ImportJob.status != ImportStatus.failed))
    if dup and not force:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(409, {"message": "Identische Datei wurde bereits importiert", "job_id": str(dup.id)})
    job_id = uuid.uuid4()
    key = f"org/{user.org_id}/imports/{job_id}/{name}"
    with open(tmp_path, "rb") as fh:
        get_storage().put(key, fh, CONTENT_TYPES.get(ftype, "application/octet-stream"))
    Path(tmp_path).unlink(missing_ok=True)
    job = ImportJob(id=job_id, org_id=user.org_id, supplier_id=supplier_id, created_by=user.id,
                    file_name=name, file_key=key, file_sha256=digest, file_size=size, file_type=ftype,
                    options={"malware_scan": scan})
    db.add(job)
    db.flush()
    try:
        analysis = analyze_job(db, job)
    except Exception as e:
        job.status = ImportStatus.failed
        job.error = f"Analyse fehlgeschlagen: {e}"
        db.commit()
        raise HTTPException(422, job.error) from e
    audit(db, user, "import_uploaded", "import_job", job.id, {"file": name, "size": size}, request)
    db.commit()
    return {**_progress(job), "analysis": analysis}


@router.get("")
def list_imports(supplier_id: uuid.UUID | None = None, limit: int = 100,
                 user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(ImportJob).where(ImportJob.org_id == user.org_id)
    if supplier_id:
        q = q.where(ImportJob.supplier_id == supplier_id)
    jobs = db.scalars(q.order_by(ImportJob.created_at.desc()).limit(min(limit, 500)))
    names = dict(db.execute(select(Supplier.id, Supplier.name).where(Supplier.org_id == user.org_id)).all())
    return [{**{k: v for k, v in _progress(j).items() if k not in {"detected_columns", "column_mapping"}},
             "supplier_name": names.get(j.supplier_id)} for j in jobs]


@router.get("/{job_id}")
def get_import(job_id: uuid.UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    j = _job(db, user, job_id)
    counts = dict(db.execute(select(ImportRow.status, func.count()).where(ImportRow.job_id == j.id)
                             .group_by(ImportRow.status)).all())
    return {**_progress(j), "row_counts": {k.value if hasattr(k, "value") else k: v for k, v in counts.items()}}


@router.get("/{job_id}/rows")
def get_rows(job_id: uuid.UUID, status: RowStatus | None = None, offset: int = 0, limit: int = 100,
             user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    _job(db, user, job_id)
    q = select(ImportRow).where(ImportRow.job_id == job_id)
    if status:
        q = q.where(ImportRow.status == status)
    rows = db.scalars(q.order_by(ImportRow.row_number).offset(offset).limit(min(limit, 500)))
    return [row(r) for r in rows]


class MappingIn(BaseModel):
    mapping: dict[str, str | None]
    options: dict = Field(default_factory=dict)
    save_profile: bool = True
    profile_name: str | None = None
    start: bool = True


@router.put("/{job_id}/mapping")
def set_mapping(job_id: uuid.UUID, body: MappingIn, request: Request,
                user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    j = _job(db, user, job_id)
    if j.status not in (ImportStatus.mapping_review, ImportStatus.uploaded, ImportStatus.failed):
        raise HTTPException(409, f"Zuordnung im Status {j.status.value} nicht änderbar")
    headers = {c["header"] for c in j.detected_columns}
    mapping = {h: f for h, f in body.mapping.items() if f and f != "ignore" and h in headers}
    errors = validate_mapping(mapping)
    if errors:
        raise HTTPException(422, {"errors": errors})
    allowed_opts = {"currency", "prices_are_gross", "input_vat_rate", "decimal_comma", "price_tier_index",
                    "default_condition", "replaces_previous", "auto_analyze", "sku_is_mpn"}
    j.column_mapping = mapping
    j.options = {**(j.options or {}), **{k: v for k, v in body.options.items() if k in allowed_opts}}
    if body.save_profile:
        save_profile(db, j, body.profile_name)
    audit(db, user, "import_mapping_set", "import_job", j.id, {"mapping": mapping}, request)
    if body.start:
        j.status = ImportStatus.queued
        db.commit()
        from sbc.worker.tasks import process_import

        process_import.send(str(j.id))
    else:
        db.commit()
    return _progress(j)


@router.post("/{job_id}/resume")
def resume(job_id: uuid.UUID, user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    j = _job(db, user, job_id)
    if j.status not in (ImportStatus.failed, ImportStatus.processing, ImportStatus.queued):
        raise HTTPException(409, "Nur fehlgeschlagene oder hängende Importe können fortgesetzt werden")
    j.status = ImportStatus.queued
    db.commit()
    from sbc.worker.tasks import process_import

    process_import.send(str(j.id))
    return _progress(j)


@router.get("/{job_id}/download")
def download(job_id: uuid.UUID, user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    j = _job(db, user, job_id)
    if j.file_deleted_at:
        raise HTTPException(410, "Datei wurde gemäß Löschfrist entfernt")
    url = get_storage().presigned_get(j.file_key, 600, j.file_name)
    if url is None:
        raise HTTPException(501, "Download nur mit S3-Speicher verfügbar")
    return {"url": url, "expires_in": 600}
