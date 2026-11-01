"""Gemeinsame Logging-Regeln für API, Worker und Scheduler.

Wichtig: httpx/httpcore protokollieren auf INFO jede Anfrage-URL. Keepa erwartet den API-Key
als Query-Parameter – er würde sonst im Klartext in den Railway-Logs landen. Zusätzlich
maskiert ein Filter ``key=``/``api_key=``-Parameter in allen Log-Meldungen.
"""

from __future__ import annotations

import logging
import re

_SECRET_PARAM = re.compile(r"((?:^|[?&\s])(?:key|api_key|apikey|access_key|token)=)[^&\s\"']+", re.I)


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        red = _SECRET_PARAM.sub(r"\1***", msg)
        if red != msg:
            record.msg, record.args = red, None
        return True


def configure() -> None:
    for name in ("httpx", "httpcore", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    f = RedactSecrets()
    root = logging.getLogger()
    if not any(isinstance(x, RedactSecrets) for x in root.filters):
        root.addFilter(f)
    for h in root.handlers:
        if not any(isinstance(x, RedactSecrets) for x in h.filters):
            h.addFilter(f)


configure()
