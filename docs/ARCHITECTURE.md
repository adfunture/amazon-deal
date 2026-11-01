# Architektur

## Überblick

```
Browser ──HTTPS──▶ frontend (Next.js) ──Private Network──▶ backend (FastAPI) ──▶ PostgreSQL
                     │  /api/backend/* Proxy, httpOnly-JWT          │   ▲              ▲
                                                                    ▼   │              │
n8n (optional) ──X-API-Key──▶ backend                           Redis (Queue, Cache, Rate Limits)
                                                                    │
                     scheduler (APScheduler) ──enqueue──▶ worker (Dramatiq) ──▶ Keepa / Claude / EZB
                                                                    └──────────▶ Railway Bucket (S3)
```

## Analysepipeline (Kostenkontrolle)

| Level | Was | Kosten | Wann |
|---|---|---|---|
| 1 | EAN/MPN gegen vorhandene Listings, Text- und Attributvergleich, Grobkalkulation | keine API | jedes Angebot |
| 2 | Keepa-Abfrage (Buy Box, Ø30/Ø90, Rank-Drops, Anbieter), Nachfrage- und Wettbewerbsscore | 1 Keepa-Token je Produkt | Angebote mit EAN, im Token-/Tagesbudget |
| 3 | Claude-Prüfung (Text, optional Bilder) | LLM-Tokens | nur bei unsicherem Match **und** erwartetem DB ≥ Schwelle (`analysis.ai_min_potential_contribution`) |

Schutzmechanismen: DB-/Redis-Cache (`KEEPA_CACHE_TTL_HOURS`), Negativ-Cache für EANs ohne Treffer, Redis-Locks gegen
parallele Doppelabfragen, Vorab-Token-Schätzung, 429 mit Wiedereinplanung nach `refillIn`, Tages- und Monatsbudgets
je Anbieter (`api_budgets`), Protokoll jedes Aufrufs in `api_usage`.

## Matching Engine (`sbc/matching`)

1. **Identifikatoren**: GTIN-14-normalisiert (führende Nullen und Excel-Floats sicher), MPN normalisiert, MPN im Titel, Marke.
2. **Text und Attribute**: RapidFuzz-Token-Ähnlichkeit, regelbasierte Extraktion von Packmenge (2er-Pack, x2, Pack of 3 …),
   Farben, Maßen und Modell-Tokens.
3. **KI** (`matching/ai.py`): Claude mit Structured Output (Pydantic-Schema), Bilder als zusätzliche Evidenz.
   Lieferanten- und Webdaten werden als `<untrusted_data>` übergeben. Eine harte Regelabweichung überstimmt die KI.
   Verdacht auf Prompt Injection führt zu `REVIEW_REQUIRED`.
4. **Klassifizierung**: EXACT_MATCH, VARIANT_MATCH, MULTIPACK, BUNDLE, PACKAGING_DIFFERENCE, POSSIBLE_MATCH, NO_MATCH,
   REVIEW_REQUIRED, dazu `units_required` (Lieferanteneinheiten je Marktplatzangebot).
5. **Human-in-the-Loop**: Jede Entscheidung erzeugt einen `matching_feedback`-Datensatz mit Label. Das ist die Grundlage der
   **Kalibrierung** (`calibration.py`: Score-Bins mit isotoner Glättung, erst ab 20 Labels je Bin aktiv, vorher „unkalibriert“).

Automatisch akzeptiert werden nur EXACT_MATCH und MULTIPACK oberhalb der Mindestkonfidenz. Einkaufsvorschläge für
Bundles, Multipacks und Varianten setzen eine manuelle Bestätigung voraus.

## Kalkulation (`sbc/profit/engine.py`)

- Nettoerlös = VK brutto / (1 + USt). Gebühren gelten netto (Vorsteuer). Provision, Zahlungsgebühr und Werbung beziehen sich auf den Bruttopreis.
- Wareneinsatz (landed) = EK × (1 − Rabatt) × FX × (1 + Zoll) + Lieferantenversand je Stück, multipliziert mit `units_required`.
- Kennzahlen: DB, DB-Marge, ROI auf Wareneinsatz, Break-even-VK (mit Mindestprovision), maximaler EK,
  Kapitalbedarf, Kapitalbindung, DB je 1.000 € Kapital und Monat.
- Szenarien: aktuell, Ø30, Ø90, konservativ (Minimum der Referenzen), −5 %, −10 %. Szenarien ohne Historie bleiben leer.
- Jede Kalkulation speichert einen vollständigen Input-Snapshot inklusive Gebührenprofil-ID, FX-Kurs und Datenstand
  in `profit_calculations` und ist damit reproduzierbar.

## Datenmodell

30 Tabellen, siehe `apps/backend/sbc/models.py`. Alle fachlichen Tabellen tragen `org_id` (vorbereitet für SaaS).
Wichtige Entscheidungen:

- `products` ist eine konkrete Verkaufseinheit. `product_identifiers` (GTIN, ASIN, MPN, SKU) ist n:1 und nicht eindeutig.
- `product_relations` (SAME, VARIANT, MULTIPACK_OF, BUNDLE_CONTAINS, …) speichert Evidenz und Konfidenz, `bundle_components` die Komponenten mit Menge.
- `supplier_offers` ist eindeutig je (Lieferant, offer_key). Preisänderungen landen in `price_history` und `previous_unit_price_net`.
- `listing_gtins` ist der Index GTIN → Marktplatz-Listing.
- Geld: `Numeric(14,4)` plus Währung plus Steuerbehandlung (Angebote netto, Listings brutto) plus Bezugsmenge (`price_basis_qty`, `unit_quantity`).

## Sicherheit

- Anmeldung per scrypt-Passworthash und JWT (HS256) im httpOnly-Cookie. Für Automatisierung gibt es widerrufbare API-Schlüssel (nur der SHA-256-Hash wird gespeichert).
- Rollen: viewer < reviewer < buyer < admin. Audit-Log für Logins, Uploads, Mapping, Reviews, Einstellungen und Einkaufsentscheidungen.
- Uploads: Größenlimit, Abgleich von Endung und Magic Bytes, ZIP-Bomben- und Pfad-Traversal-Schutz, XML ohne DTD,
  optional ClamAV (`CLAMAV_HOST`). Dateien liegen im privaten Bucket, Downloads nur über signierte URLs.
- SSRF-Schutz für URL-Quellen: nur öffentliche IPs, kein `*.railway.internal`, Redirects werden neu geprüft.
- Login-Rate-Limit über Redis, Security-Header, CORS nur für `FRONTEND_URL`.
- Secrets nur als Railway-Variablen; Produktionsstart ohne `APP_SECRET` oder mit lokalem Speicher wird verweigert.

## Skalierung (Ziel 100.000 Artikel/Tag)

Importe laufen streamend (openpyxl read-only, CSV-Iterator) in Batches zu 500 Zeilen mit Commit und Wiederaufnahmepunkt.
Die Analyse läuft in Chunks zu 50 Angeboten je Job und skaliert horizontal über `worker`-Replicas. Der Engpass sind die
externen Kontingente: Bei 100.000 EANs/Tag mit Keepa ohne Cache wären rund 100.000 Tokens/Tag nötig, also etwa
70 Tokens/Minute Dauerrate. Lasttests gegen eine Railway-Staging-Umgebung stehen noch aus (Phase 6).
