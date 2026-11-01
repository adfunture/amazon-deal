# Ein Image für drei Railway-Services: backend (API), worker (Dramatiq), scheduler (APScheduler).
# Der Startbefehl wird je Service in .railway/railway.ts gesetzt.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY alembic ./alembic
COPY sbc ./sbc
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
# Railway setzt PORT. "::" bindet IPv4+IPv6 (nötig für Railway Private Networking).
CMD ["sh", "-c", "uvicorn sbc.api.main:app --host :: --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
