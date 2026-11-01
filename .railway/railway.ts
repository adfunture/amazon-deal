// Railway Infrastructure as Code für SBC AI Sourcing Intelligence.
// Prüfen:   railway config plan
// Anwenden: railway config apply   (nur nach Durchsicht des Plans)
//
// Nicht hier ausdrückbar und daher im Railway-Dashboard zu setzen (siehe docs/DEPLOYMENT.md):
// Restart-Policy (Standard ON_FAILURE genügt), öffentliche Railway-Domain für das Frontend.
import { bucket, defineRailway, github, group, postgres, preserve, project, redis, service } from "railway/iac";

// GitHub-Repository – vor dem ersten `railway config plan` anpassen.
const REPO = "sbcsymbio/sbc-sourcing-intelligence";
const BRANCH = "main";

export default defineRailway(() => {
  const db = postgres("postgres");
  const cache = redis("redis");
  const files = bucket("sbc-files");

  // Gemeinsame Variablen für alle Python-Services (backend, worker, scheduler)
  const pythonEnv = {
    APP_ENV: "production",
    LOG_LEVEL: "INFO",
    DATABASE_URL: db.env.DATABASE_URL,
    REDIS_URL: cache.env.REDIS_URL,
    STORAGE_BACKEND: "s3",
    S3_ENDPOINT: files.env.ENDPOINT,
    S3_BUCKET: files.env.BUCKET,
    S3_ACCESS_KEY: files.env.ACCESS_KEY_ID,
    S3_SECRET_KEY: files.env.SECRET_ACCESS_KEY,
    S3_REGION: files.env.REGION,
    FILE_RETENTION_DAYS: "180",
    // Secrets: Werte werden im Railway-Dashboard gesetzt und hier nur beibehalten
    APP_SECRET: preserve(),
    ANTHROPIC_API_KEY: preserve(),
    OPENAI_API_KEY: preserve(),
    KEEPA_API_KEY: preserve(),
    AMAZON_SP_API_CREDENTIALS: preserve(),
    EBAY_CLIENT_ID: preserve(),
    EBAY_CLIENT_SECRET: preserve(),
    SMTP_URL: preserve(),
    REPORT_RECIPIENTS: preserve(),
  };

  const backend = service("backend", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "apps/backend" }),
    // Build über apps/backend/Dockerfile (Railway nutzt ein vorhandenes Dockerfile automatisch)
    start: "uvicorn sbc.api.main:app --host :: --port 8000 --proxy-headers --forwarded-allow-ips=*",
    // Migrationen + Bootstrap laufen vor jedem Deploy; ein Fehler stoppt das Deployment
    preDeploy: "alembic upgrade head && python -m sbc.cli bootstrap",
    healthcheck: "/health",
    healthcheckTimeout: 120,
    env: {
      ...pythonEnv,
      PORT: "8000",
      FRONTEND_URL: preserve(),
      INITIAL_ADMIN_EMAIL: preserve(),
      INITIAL_ADMIN_PASSWORD: preserve(),
      MAX_UPLOAD_MB: "100",
    },
  });

  const worker = service("worker", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "apps/backend" }),
    start: "dramatiq sbc.worker.tasks --processes 2 --threads 4",
    env: pythonEnv,
  });

  // Genau eine Instanz – plant periodische Jobs in die Queues ein
  const scheduler = service("scheduler", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "apps/backend" }),
    start: "python -m sbc.scheduler.main",
    replicas: 1,
    env: {
      ...pythonEnv,
      TZ: "Europe/Berlin",
    },
  });

  const frontend = service("frontend", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "apps/frontend" }),
    build: "npm ci && npm run build",
    start: "npm run start",
    healthcheck: "/api/health",
    healthcheckTimeout: 120,
    env: {
      NODE_ENV: "production",
      NEXT_TELEMETRY_DISABLED: "1",
      // Private Networking: Frontend → backend.railway.internal:8000
      BACKEND_HOST: backend.env.RAILWAY_PRIVATE_DOMAIN,
      BACKEND_PORT: "8000",
    },
  });

  return project("sbc-sourcing-intelligence", {
    resources: [
      db,
      cache,
      files,
      backend,
      worker,
      scheduler,
      frontend,
      group("Python", [backend, worker, scheduler]),
      group("Daten", [db, cache, files]),
    ],
  });
});
