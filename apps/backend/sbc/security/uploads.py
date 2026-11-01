"""Upload-Prüfung: Größenlimit, Dateityp/Magic Bytes, optional ClamAV (clamd INSTREAM).

Ist CLAMAV_HOST nicht gesetzt, wird der Upload als ``scan=not_configured`` markiert.
Dateien werden nie ausgeführt; Makros in XLSM werden von openpyxl nicht ausgewertet.
"""

from __future__ import annotations

import os
import socket
import struct

from sbc.importer.readers import file_type_of, sniff_magic


class UploadRejected(ValueError):
    pass


def validate_upload(name: str, head: bytes, size: int, max_mb: int) -> str:
    if size <= 0:
        raise UploadRejected("Leere Datei")
    if size > max_mb * 1024 * 1024:
        raise UploadRejected(f"Datei größer als {max_mb} MB")
    try:
        ft = file_type_of(name)
        sniff_magic(head, ft)
    except ValueError as e:
        raise UploadRejected(str(e)) from e
    return ft


def clamav_scan(path: str) -> str:
    """Gibt 'clean', 'not_configured' zurück oder wirft UploadRejected bei Fund."""
    host = os.environ.get("CLAMAV_HOST")
    if not host:
        return "not_configured"
    port = int(os.environ.get("CLAMAV_PORT", "3310"))
    with socket.create_connection((host, port), timeout=30) as s, open(path, "rb") as fh:
        s.sendall(b"zINSTREAM\0")
        while chunk := fh.read(64 * 1024):
            s.sendall(struct.pack("!L", len(chunk)) + chunk)
        s.sendall(struct.pack("!L", 0))
        reply = s.recv(4096).decode(errors="replace").strip("\0\n ")
    if reply.endswith("OK"):
        return "clean"
    if "FOUND" in reply:
        raise UploadRejected(f"Malware erkannt: {reply}")
    raise UploadRejected(f"Virenscan fehlgeschlagen: {reply}")
