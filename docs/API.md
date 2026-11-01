# API

Die interaktive Dokumentation (OpenAPI) läuft unter `https://<backend>/docs` bzw. `/redoc`.

**Authentifizierung**
- Browser: `POST /api/v1/auth/login` → JWT (das Frontend speichert es als httpOnly-Cookie).
- Automatisierung: Header `X-API-Key: sbc_…` (Anlage: `POST /api/v1/auth/api-keys`, nur Admin; der Schlüssel wird einmalig angezeigt).

Geldbeträge werden als **Strings** übertragen (Decimal, keine Float-Rundung). EANs werden als GTIN-14 übertragen
(`04006381333931`); die Anzeige kürzt sie auf EAN-13.

| Methode | Pfad | Rolle | Zweck |
|---|---|---|---|
| GET | `/health`, `/health/ready` | – | Liveness / Readiness (DB, Redis) |
| POST | `/api/v1/auth/login` | – | Anmeldung |
| GET | `/api/v1/auth/me` | alle | eigenes Profil |
| GET/POST | `/api/v1/auth/users` | admin | Benutzerverwaltung |
| GET/POST/DELETE | `/api/v1/auth/api-keys` | admin | API-Schlüssel |
| GET/POST | `/api/v1/suppliers` | alle / buyer | Lieferanten |
| GET/PUT | `/api/v1/suppliers/{id}` | alle / buyer | Profil, Importe, Importprofile |
| POST | `/api/v1/suppliers/{id}/feeds` | buyer | Feed-Quelle (SSRF-geprüft) |
| GET | `/api/v1/suppliers/{id}/price-history` | alle | Preisänderungen zwischen Importen |
| GET | `/api/v1/suppliers-lookup/by-email?sender=` | alle | Absender → Lieferant (n8n) |
| GET | `/api/v1/imports/fields` | alle | Zielfelder der Spaltenzuordnung |
| POST | `/api/v1/imports` (multipart: supplier_id, file, force) | buyer | Upload und Analyse; 409 bei identischer Datei |
| GET | `/api/v1/imports`, `/api/v1/imports/{id}` | alle | Status, Fortschritt, Zeilenzähler |
| GET | `/api/v1/imports/{id}/rows?status=error\|duplicate\|ok` | alle | Fehlerhafte Datensätze |
| PUT | `/api/v1/imports/{id}/mapping` | buyer | Zuordnung und Optionen speichern, Profil anlegen, Import starten |
| POST | `/api/v1/imports/{id}/resume` | buyer | Abgebrochenen Import fortsetzen |
| GET | `/api/v1/imports/{id}/download` | buyer | signierte Download-URL der Originaldatei |
| GET | `/api/v1/matching/queue?status=&classification=` | alle | Prüfwarteschlange |
| GET | `/api/v1/matching/{id}` | alle | Vergleich Lieferant ↔ Marktplatz |
| POST | `/api/v1/matching/{id}/review` | reviewer | confirm, reject, bundle, variant, defer, correct (+ units_required) |
| GET | `/api/v1/matching/calibration/report` | alle | Kalibrierungs-Bins |
| GET | `/api/v1/opportunities` | alle | Filter: q, brand, category, supplier_id, marketplace, channel, min_margin, min_contribution, min/max_sell, min/max_buy, min_demand, min_score, match_class, bundle_only, include_excluded, include_test; sort, order, offset, limit |
| GET | `/api/v1/opportunities/export?fmt=csv\|xlsx&ids=` | alle | Export |
| GET | `/api/v1/opportunities/{id}` | alle | Detail: Kalkulationen je Szenario, Preisverlauf, Quellen |
| POST | `/api/v1/opportunities/status` | reviewer | ignored, shortlisted, watching … |
| POST | `/api/v1/opportunities/analyze` | buyer | Detailanalyse einplanen |
| POST | `/api/v1/opportunities/purchase-list` | buyer | Einkaufsvorschläge erzeugen (keine Bestellung) |
| GET | `/api/v1/opportunities/purchase-list/items` | alle | Einkaufsliste |
| POST | `/api/v1/purchase-recommendations/{id}` | buyer | approved, rejected, ordered (manuell) |
| POST | `/api/v1/amazon/lookup` | buyer | Keepa-Lookup ≤ 20 EANs |
| POST | `/api/v1/pipeline/analyze-pending` | buyer | Angebote ohne Analyse einplanen |
| POST | `/api/v1/pipeline/refresh-market-data` | buyer | veraltete Marktdaten erneuern |
| POST | `/api/v1/pipeline/recalculate` | buyer | Kalkulation und Score neu berechnen |
| GET | `/api/v1/pipeline/status` | alle | laufende Jobs, offene Angebote |
| GET | `/api/v1/reports/daily` | alle | Top-Opportunities (dedupliziert) und Text |
| GET | `/api/v1/dashboard` | alle | Kennzahlen (ohne Testdaten) |
| GET/PUT | `/api/v1/settings[/{key}]` | alle / admin | thresholds, score_weights, purchasing, api_budgets, analysis, fx_rates, reports |
| GET/PUT | `/api/v1/fee-profiles[/{id}]` | alle / admin | Gebühren-, Steuer- und Versandprofile |
| GET | `/api/v1/monitoring?days=` | alle | API-Verbrauch, Kosten, Fehler, Jobs, Keepa-Tokens |
| GET | `/api/v1/jobs`, `/api/v1/audit` | alle / admin | Jobs, Audit-Log |
| GET | `/api/v1/integrations` | alle | Konfigurationsstatus der Integrationen |
