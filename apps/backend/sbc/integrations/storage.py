"""Objektspeicher: Railway Storage Bucket (S3-kompatibel) oder lokal (nur Entwicklung/Tests).

Railway-Buckets sind privat; Zugriff von außen nur über signierte URLs (presigned GET/PUT).
Railway unterstützt derzeit keine Bucket-Lifecycle-Regeln – Löschfristen setzt der Scheduler
(``cleanup_expired_files``) anhand von FILE_RETENTION_DAYS durch.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from sbc.config import get_settings


class Storage(Protocol):
    def put(self, key: str, fh: BinaryIO, content_type: str) -> None: ...
    def download_to(self, key: str, target: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def presigned_get(self, key: str, expires_s: int = 900, filename: str | None = None) -> str | None: ...
    def healthy(self) -> bool: ...


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config

        s = get_settings()
        if not (s.s3_endpoint and s.s3_bucket and s.s3_access_key and s.s3_secret_key):
            raise RuntimeError("S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY fehlen")
        self.bucket = s.s3_bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4",
                          s3={"addressing_style": "path" if s.s3_force_path_style else "virtual"}),
        )

    def put(self, key, fh, content_type):
        self._s3.upload_fileobj(fh, self.bucket, key, ExtraArgs={"ContentType": content_type})

    def download_to(self, key, target):
        self._s3.download_file(self.bucket, key, str(target))

    def delete(self, key):
        self._s3.delete_object(Bucket=self.bucket, Key=key)

    def presigned_get(self, key, expires_s=900, filename=None):
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self._s3.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_s)

    def healthy(self):
        try:
            self._s3.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


class LocalStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or get_settings().local_storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("ungültiger Schlüssel")
        return p

    def put(self, key, fh, content_type):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as out:
            shutil.copyfileobj(fh, out)

    def download_to(self, key, target):
        shutil.copyfile(self._path(key), target)

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)

    def presigned_get(self, key, expires_s=900, filename=None):
        return None  # lokal: Download über API-Endpunkt

    def healthy(self):
        return self.root.exists()


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage() if get_settings().storage_backend == "local" else S3Storage()
    return _storage


def set_storage(s: Storage) -> None:
    global _storage
    _storage = s
