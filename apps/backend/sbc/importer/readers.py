"""Streamende Reader für Lieferantendateien.

Alle Reader liefern Zeilen als Listen roher Zellwerte. Große Dateien werden nicht
vollständig in den Speicher geladen (openpyxl read_only, csv-Iterator).
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED = {"xlsx", "xlsm", "xls", "csv", "tsv", "txt", "json", "xml", "zip"}
NOT_YET = {"pdf": "PDF-Import (KI-Extraktion) ist für Phase 3 geplant und noch nicht aktiv."}

MAX_ZIP_MEMBER_BYTES = 200 * 1024 * 1024
MAX_ZIP_RATIO = 100  # Schutz gegen Zip-Bomben


class UnsupportedFile(ValueError):
    pass


@dataclass
class SheetInfo:
    name: str
    rows_estimate: int | None


def file_type_of(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in SUPPORTED:
        return "csv" if ext == "txt" else ext
    if ext in NOT_YET:
        raise UnsupportedFile(NOT_YET[ext])
    raise UnsupportedFile(f"Dateityp .{ext} wird nicht unterstützt")


def sniff_magic(data: bytes, file_type: str) -> None:
    """Grobe Inhaltsprüfung: Endung muss zum Inhalt passen (verhindert getarnte Binärdateien)."""
    head = data[:8]
    if file_type in {"xlsx", "xlsm", "zip"} and not head.startswith(b"PK"):
        raise UnsupportedFile("Datei ist kein gültiges XLSX/ZIP-Archiv")
    if file_type == "xls" and not head.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFile("Datei ist keine gültige XLS-Datei")
    if file_type in {"csv", "tsv", "json", "xml"} and b"\x00" in data[:4096].replace(b"\x00\x00", b""):
        # UTF-16 erlaubt, sonst keine Nullbytes in Textdateien
        if not (data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE)):
            raise UnsupportedFile("Textdatei enthält Binärdaten")
    if data[:2] == b"MZ":
        raise UnsupportedFile("Ausführbare Dateien sind nicht erlaubt")


def _decode(data: bytes) -> str:
    for bom, enc in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
                     (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return data.decode(enc)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def list_sheets(path: Path, file_type: str) -> list[SheetInfo]:
    if file_type in {"xlsx", "xlsm"}:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return [SheetInfo(ws.title, ws.max_row) for ws in wb.worksheets]
        finally:
            wb.close()
    if file_type == "xls":
        import xlrd

        book = xlrd.open_workbook(path, on_demand=True)
        return [SheetInfo(n, book.sheet_by_name(n).nrows) for n in book.sheet_names()]
    return [SheetInfo("data", None)]


def iter_rows(path: Path, file_type: str, sheet: str | None = None) -> Iterator[list[object]]:
    if file_type in {"xlsx", "xlsm"}:
        yield from _iter_xlsx(path, sheet)
    elif file_type == "xls":
        yield from _iter_xls(path, sheet)
    elif file_type in {"csv", "tsv"}:
        yield from _iter_csv(path, "\t" if file_type == "tsv" else None)
    elif file_type == "json":
        yield from _iter_json(path)
    elif file_type == "xml":
        yield from _iter_xml(path)
    elif file_type == "zip":
        raise UnsupportedFile("ZIP muss vor dem Lesen entpackt werden (extract_zip)")
    else:
        raise UnsupportedFile(file_type)


def _iter_xlsx(path: Path, sheet: str | None) -> Iterator[list[object]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else _best_sheet(wb)
        for row in ws.iter_rows(values_only=True):
            yield list(row)
    finally:
        wb.close()


def _best_sheet(wb):  # erstes Blatt mit Inhalt
    for ws in wb.worksheets:
        if ws.max_row and ws.max_row > 1:
            return ws
    return wb.worksheets[0]


def _iter_xls(path: Path, sheet: str | None) -> Iterator[list[object]]:
    import xlrd

    book = xlrd.open_workbook(path, on_demand=True)
    sh = book.sheet_by_name(sheet) if sheet else book.sheet_by_index(0)
    for r in range(sh.nrows):
        yield [c.value if c.ctype not in (0, 6) else None for c in sh.row(r)]


def _iter_csv(path: Path, delimiter: str | None) -> Iterator[list[object]]:
    raw = path.read_bytes()
    text = _decode(raw)
    if delimiter is None:
        sample = text[:20000]
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
    # Alle Werte bleiben Strings -> führende Nullen bleiben erhalten
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        yield [c if c != "" else None for c in row]


def _iter_json(path: Path) -> Iterator[list[object]]:
    data = json.loads(_decode(path.read_bytes()))
    if isinstance(data, dict):
        for key in ("items", "products", "data", "rows", "articles"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise UnsupportedFile("JSON muss eine Liste von Objekten enthalten")
    headers: list[str] = []
    for item in data[:200]:
        for k in item:
            if k not in headers:
                headers.append(k)
    yield list(headers)
    for item in data:
        yield [_scalar(item.get(h)) for h in headers]


def _scalar(v: object) -> object:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _iter_xml(path: Path) -> Iterator[list[object]]:
    """Flache XML-Feeds: das häufigste wiederholte Element = Datensatz, Kindelemente = Spalten."""
    # defusedxml ist nicht nötig, da ElementTree keine externen Entitäten auflöst;
    # DTDs werden trotzdem abgelehnt.
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw[:2048] or b"<!ENTITY" in raw:
        raise UnsupportedFile("XML mit DTD/Entitäten wird aus Sicherheitsgründen abgelehnt")
    root = ET.fromstring(raw)
    counts: dict[str, int] = {}
    for el in root.iter():
        if len(el):
            counts[el.tag] = counts.get(el.tag, 0) + 1
    if not counts:
        raise UnsupportedFile("Keine Datensätze im XML gefunden")
    record_tag = max((t for t in counts if t != root.tag), key=lambda t: counts[t], default=root.tag)
    records = [el for el in root.iter(record_tag)]
    headers: list[str] = []
    for rec in records[:200]:
        for child in rec:
            tag = child.tag.split("}")[-1]
            if tag not in headers:
                headers.append(tag)
    yield headers
    for rec in records:
        values = {child.tag.split("}")[-1]: (child.text or "").strip() or None for child in rec}
        yield [values.get(h) for h in headers]


def extract_zip(path: Path, target_dir: Path) -> Path:
    """Entpackt die erste unterstützte Datei eines ZIP-Archivs sicher (ohne Pfad-Traversal)."""
    with zipfile.ZipFile(path) as zf:
        candidates = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.startswith(".") or "__MACOSX" in info.filename:
                continue
            try:
                ft = file_type_of(name)
            except UnsupportedFile:
                continue
            if ft == "zip":
                continue
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise UnsupportedFile(f"{name} ist im entpackten Zustand zu groß")
            if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_RATIO:
                raise UnsupportedFile(f"{name}: verdächtiges Kompressionsverhältnis")
            candidates.append((info, name))
        if not candidates:
            raise UnsupportedFile("ZIP enthält keine unterstützte Datei")
        info, name = candidates[0]
        target = target_dir / name
        with zf.open(info) as src, open(target, "wb") as dst:
            remaining = MAX_ZIP_MEMBER_BYTES
            while chunk := src.read(1024 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise UnsupportedFile("Entpackte Datei überschreitet das Limit")
                dst.write(chunk)
        return target
