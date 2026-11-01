# SBC AI Sourcing Intelligence

Webanwendung der SBC symbio GmbH & Co. KG, die neue profitable Handelsprodukte findet, prüft und priorisiert.
Sie liest Großhandelslisten ein, gleicht die Artikel mehrstufig mit Amazon-Angeboten ab (Keepa) und kalkuliert jeden
Treffer deterministisch. Daraus entstehen bewertete Einkaufschancen. Einkäufe gibt immer ein Mensch frei.

**Stand:** Phase 1 und Phase 2 (MVP) sind umgesetzt. Was funktioniert und was fehlt, steht in
[docs/PROGRESS.md](docs/PROGRESS.md).

## Aufbau

```
.railway/railway.ts        Railway Infrastructure as Code (6 Services + Bucket)
apps/backend/              Python 3.12 – FastAPI, SQLAlchemy, Alembic, Dramatiq
  sbc/api/                 REST-API (Auth, Lieferanten, Importe, Matching, Opportunities, Settings, Monitoring)
  sbc/importer/            Universal Import Engine (Reader, Spaltenerkennung, Normalisierung, Batch-Verarbeitung)
  sbc/matching/            Matching Engine (Identifikatoren, Text/Attribute, KI-Stufe, Kalibrierung)
  sbc/profit/              Deterministische Kalkulation (Decimal), Gebührenprofile
  sbc/scoring/             Nachfrage, Wettbewerb, Opportunity-Score, Ausschlusskriterien
  sbc/integrations/        Keepa, Claude, S3/Bucket, EZB-Wechselkurse, API-Budgets
  sbc/worker/              Hintergrundjobs (Service "worker")
  sbc/scheduler/           Zeitpläne (Service "scheduler")
  alembic/                 Datenbankmigrationen
  tests/                   101 automatisierte Tests
apps/frontend/             Next.js 16, React 19, Tailwind 4, TanStack Table, Recharts
infrastructure/n8n/        6 importierbare n8n-Workflows (inaktiv, Platzhalter-Credentials)
docs/                      Architektur, Deployment, API, n8n, Fortschritt
```

Backend, Worker und Scheduler nutzen ein gemeinsames Python-Paket (`apps/backend`). Sie laufen als drei
Railway-Services mit unterschiedlichem Startbefehl. Das vermeidet doppelten Code; Modelle und Geschäftslogik
sind überall identisch. Ein separates `apps/worker` gibt es deshalb nicht.

## Lokal starten

Voraussetzungen: Python 3.12+, Node.js 20.9+. PostgreSQL und Redis sind optional, lokal reicht SQLite ohne Queue.

```bash
cd apps/backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/pip
cp ../../.env.example .env   # APP_ENV=development, DATABASE_URL=sqlite:///./.data/dev.db, STORAGE_BACKEND=local, INITIAL_ADMIN_* setzen
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m sbc.cli bootstrap        # Organisation, Admin, Gebührenprofile
.venv/Scripts/python -m sbc.cli load-demo        # optional: TESTDATEN (als solche gekennzeichnet)
.venv/Scripts/uvicorn sbc.api.main:app --port 8000
```

```bash
cd apps/frontend
npm install
BACKEND_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

Ohne `REDIS_URL` werden Hintergrundjobs nicht ausgeführt (StubBroker). Mit Redis zusätzlich starten:
`dramatiq sbc.worker.tasks` und `python -m sbc.scheduler.main`.

## Tests

```bash
cd apps/backend && .venv/Scripts/python -m pytest -q     # 101 Tests
cd apps/frontend && npx tsc --noEmit && npm run build
```

Externe APIs (Keepa, Claude) laufen in den Tests nur gegen gekennzeichnete Fixtures bzw. Mock-Transporte.

## Deployment

Railway ist die Produktionsplattform. Die Schritt-für-Schritt-Anleitung steht in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Grundsätze

- Live-Daten und Testdaten sind strikt getrennt. Testdaten tragen `is_test_data=true`, sind standardmäßig
  ausgeblendet und werden als TESTDATEN markiert.
- Geldbeträge werden ausschließlich mit `Decimal` berechnet. Die Umsatzsteuer ist kein Kostenpunkt.
- Fehlende Kosten werden sichtbar gekennzeichnet. Fehlen Pflichtwerte, wird kein Ergebnis berechnet.
- Eine EAN ist kein globaler Produktschlüssel. Eine gleiche EAN bei anderer Verkaufseinheit ergibt kein exaktes Match.
- Die KI liefert nur Evidenz. Regeln haben Vorrang, und KI-Ergebnisse muss immer ein Mensch bestätigen.
- Es gibt keine automatischen Bestellungen und keinen E-Mail-Versand ohne ausdrückliche Aktivierung.
