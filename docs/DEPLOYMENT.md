# Deployment auf Railway

Railway ist die verbindliche Produktionsplattform. Die Infrastruktur steht als Code in `.railway/railway.ts`
(Railway Infrastructure as Code). Das frühere `railway.json`/`railway.toml` (Config as Code) ist abgekündigt.
Neue Services können es nicht mehr nutzen, und bestehende laufen am 2026-12-01 aus.

## Zielbild

| Service | Quelle | Start | Öffentlich |
|---|---|---|---|
| `frontend` | `apps/frontend` (Railpack, Next.js) | `npm run start` | ja (Railway-Domain / eigene Domain) |
| `backend` | `apps/backend/Dockerfile` | `uvicorn … --host :: --port 8000` | optional (für n8n/API-Doku) |
| `worker` | `apps/backend/Dockerfile` | `dramatiq sbc.worker.tasks --processes 2 --threads 4` | nein |
| `scheduler` | `apps/backend/Dockerfile` | `python -m sbc.scheduler.main` | nein (1 Replica) |
| `postgres` | Railway PostgreSQL | – | nein |
| `redis` | Railway Redis | – | nein |
| `sbc-files` | Railway Storage Bucket (S3-kompatibel) | – | privat, nur signierte URLs |

Das Frontend spricht das Backend über **Private Networking** an (`BACKEND_HOST = backend.RAILWAY_PRIVATE_DOMAIN`, Port 8000).
Uvicorn bindet deshalb an `::` (IPv4 und IPv6). Der Browser sieht nur das Frontend. Das JWT liegt als httpOnly-Cookie
und wird vom Next.js-Proxy (`/api/backend/*`) an FastAPI weitergereicht.

**Scheduler:** Ein eigener, dauerhaft laufender Service (APScheduler) reiht die Jobs in Redis ein. Ein Railway-Cron
wäre auch möglich, das IaC-Schema dokumentiert aber kein Cron-Feld. Der Scheduler-Service ist damit vollständig
im Code beschrieben, und die Zeitpläne liegen versioniert in `sbc/scheduler/main.py`.

## 1. GitHub-Repository anlegen

```bash
cd sbc-sourcing-intelligence
git remote add origin https://github.com/adfunture/amazon-deal.git
git push -u origin main
```

Danach `REPO` in `.railway/railway.ts` ist auf `adfunture/amazon-deal` gesetzt und die Railway-GitHub-App
für das Repository freigeben (Railway → Account Settings → GitHub).

## 2. Railway-Projekt erstellen und verknüpfen

```bash
railway login
railway init            # neues Projekt "sbc-sourcing-intelligence" (Region EU wählen, z. B. europe-west4)
railway link            # im Repo-Root
```

## 3.–7. PostgreSQL, Redis, Bucket und Services anlegen

Alles wird aus `.railway/railway.ts` erzeugt:

```bash
railway config plan     # zeigt die geplanten Änderungen, ändert nichts
railway config apply    # fragt vor dem Anwenden nach
```

Den Plan vor dem Anwenden prüfen. Die **Bucket-Region lässt sich nach der Erstellung nicht ändern**. `railway.ts` setzt
`region: "ams"` (EU West, Amsterdam), denn ohne Angabe landet der Bucket in `sjc` (USA). Die Services laufen über
`replicas: { "europe-west4-drams3a": 1 }` in EU West. Für Postgres und Redis die Standardregion des Projekts auf
EU West stellen (Project Settings), **bevor** `config apply` die Datenbanken anlegt.

**pgvector:** Die Migration aktiviert `vector` nur, wenn die Postgres-Instanz die Erweiterung anbietet
(`pg_available_extensions`). Matching und Suche funktionieren ohne pgvector (Trigramm-Indizes über `pg_trgm`).

## 8. Scheduler

Das ist nach `config apply` erledigt. Zeitpläne (Europe/Berlin): EZB-Kurse werktags 16:15, Neubewertung alle 6 h,
Tagesbericht 06:30, Löschfristen 03:00. Einzelne Jobs lassen sich mit `SCHEDULE_<NAME>=off` oder einem eigenen
Cron-Ausdruck übersteuern, z. B. `SCHEDULE_DAILY_REPORT=0 7 * * 1-5`.

## 9. Environment Variables

Datenbank-, Redis- und Bucket-Variablen sind Referenzen und werden automatisch gesetzt. Im Dashboard sind nur die
Secrets einzutragen, die in `railway.ts` als `preserve()` markiert sind:

| Variable | Services | Pflicht |
|---|---|---|
| `APP_SECRET` | backend, worker, scheduler (gleicher Wert) | ja, ≥ 32 Zeichen |
| `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD` | backend | für den ersten Start; danach löschen |
| `FRONTEND_URL` | backend | ja (CORS), z. B. `https://sourcing.sbc-symbio.de` |
| `KEEPA_API_KEY` | backend, worker | für Amazon-Marktdaten |
| `ANTHROPIC_API_KEY` | backend, worker | für die KI-Matchingprüfung |
| `SMTP_URL`, `REPORT_RECIPIENTS` | worker | nur für den E-Mail-Bericht |
| `AMAZON_SP_API_CREDENTIALS`, `EBAY_CLIENT_ID/SECRET` | backend, worker | ab Phase 4 |

Tipp: `APP_SECRET` als Shared Variable des Projekts anlegen und in allen drei Python-Services referenzieren.

## 10. Datenbankmigrationen

Sie laufen automatisch als **Pre-Deploy-Command** des Backends:
`alembic upgrade head && python -m sbc.cli bootstrap`. Schlägt die Migration fehl, wird nicht deployt.
Worker und Scheduler deployen parallel. Jobs, die vor Abschluss der Migration starten, wiederholt Dramatiq automatisch.

**Niemals** `alembic revision --autogenerate` gegen die Produktionsdatenbank ausführen. Neue Migrationen entstehen
lokal gegen eine leere SQLite- oder Postgres-Datenbank.

## 11. Domains und HTTPS

Railway → Service `frontend` → Settings → Networking → *Generate Domain* oder *Custom Domain*. HTTPS stellt Railway
automatisch bereit. Eigene Domains lassen sich auch in `railway.ts` eintragen (`domains: ["sourcing.example.de"]`).
Danach `FRONTEND_URL` im Backend anpassen. Für n8n zusätzlich eine Domain für `backend` erzeugen.

## 12. Funktionstests nach dem Deployment

1. `https://<backend-domain>/health` liefert `{"status":"ok"}`. `/health/ready` prüft zusätzlich Datenbank und Redis.
2. Frontend öffnen und mit dem Admin aus Schritt 9 anmelden. Danach `INITIAL_ADMIN_PASSWORD` löschen.
3. *API Monitoring* zeigt den Status der Integrationen. Keepa, Anthropic und Storage sollten `configured` bzw. `s3` melden.
4. Lieferant anlegen, eine kleine Liste hochladen, die Zuordnung prüfen und den Import starten.
   Der Fortschritt läuft im Worker. In den Railway-Logs von `worker` erscheint `process_import`.
5. Amazon Scanner: eine bekannte EAN abfragen. Danach zeigt *API Monitoring* den Keepa-Tokenverbrauch.
6. Matching Review: Treffer bestätigen, anschließend Opportunity und Einkaufsliste prüfen.

## Betrieb

- **Logs:** Railway → Service → Deployments/Logs. Alle Services loggen auf stdout.
- **Ressourcen:** Railway → Service → Metrics (CPU, RAM, Netzwerk). Kosten unter Project → Usage.
- **Backups:** Railway → Postgres → Backups aktivieren (täglich). Vor riskanten Migrationen ein manuelles Backup anlegen.
- **Skalierung:** Den `worker` bei großen Importen über `replicas` skalieren. Den Scheduler bei genau einer Replica lassen.
- **Dead-Letter:** Endgültig fehlgeschlagene Nachrichten liegen in Redis (`dramatiq:<queue>.XQ`, 14 Tage) und erscheinen in *API Monitoring*.

## Lokale Docker-Variante (optional)

Für einen produktionsnahen lokalen Test: `docker build -t sbc-backend apps/backend` und den Container mit
`DATABASE_URL`/`REDIS_URL` starten. Docker Compose ist für Railway nicht nötig.
