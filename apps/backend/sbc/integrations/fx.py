"""Wechselkurse: EZB-Referenzkurse (täglich, öffentlich, ohne Schlüssel).

Quelle: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
Kurse werden mit Datum und Quelle in app_settings['fx_rates'] gespeichert und in jedem
Kalkulations-Snapshot referenziert (fx_rate). Manuelle Kurse können im Dashboard gesetzt werden.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select

from sbc.db import session_scope
from sbc.models import Organization
from sbc.settings_store import put_setting

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_NS = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}


def parse_ecb(xml: bytes) -> tuple[str, dict[str, str]]:
    root = ET.fromstring(xml)
    cube = root.find(".//e:Cube/e:Cube", _NS)
    if cube is None:
        raise ValueError("EZB-Antwort ohne Kursdaten")
    rates = {c.attrib["currency"]: c.attrib["rate"] for c in cube.findall("e:Cube", _NS)}
    return cube.attrib["time"], rates


def fetch_ecb() -> tuple[str, dict[str, str]]:
    resp = httpx.get(ECB_URL, timeout=30)
    resp.raise_for_status()
    return parse_ecb(resp.content)


def refresh_all_orgs() -> dict:
    date, rates = fetch_ecb()
    with session_scope() as db:
        for org_id in db.scalars(select(Organization.id)):
            put_setting(db, org_id, "fx_rates", {"base": "EUR", "rates": rates, "date": date,
                                                 "source": "EZB-Referenzkurs"})
    return {"date": date, "count": len(rates)}
