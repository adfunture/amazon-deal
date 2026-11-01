from __future__ import annotations

import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sbc import __version__
from sbc.api.routers import auth, imports, matching, opportunities, pipeline, suppliers, system
from sbc.config import get_settings

settings = get_settings()
logging.basicConfig(stream=sys.stdout, level=settings.log_level,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sbc.api")

app = FastAPI(
    title="SBC AI Sourcing Intelligence API",
    version=__version__,
    description="REST-API für Lieferantenimporte, Produktmatching, Profit-Kalkulation und Opportunities.",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unbehandelter Fehler bei %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Interner Fehler – siehe Server-Logs"})


app.include_router(system.router)
for r in (auth.router, suppliers.router, imports.router, matching.router, opportunities.router, pipeline.router):
    app.include_router(r, prefix="/api/v1")
