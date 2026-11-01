# Entwicklungsstand

Stand: 2026-11-01 · Phase 1 (Analyse und Architektur) und Phase 2 (MVP) sind abgeschlossen.

## Phase 1 – Projektanalyse

- Das Arbeitsverzeichnis `adfunture-automation` ist die Telegram- und Shopify-Automatisierung der **Adfunture Outdoor GmbH**,
  eine andere Firma mit anderem Zweck. Die SBC-Anwendung ist deshalb ein **eigenes Repository**
  (`Desktop/sbc-sourcing-intelligence`), so wie Schadenportal und Maschinenrechner.
- Wiederverwendet wurden die Railway-Erfahrungen aus dem Schadenportal (Next.js 16, Tailwind 4, Railway EU) und die
  Annahmen aus der vorhandenen SBC-Arbeitsdatei *MANN_FILTER_Amazon_eBay_Kalkulation.xlsx*: 15 % Provision,
  3,50 € FBA, 19 % USt. Sie dienen als Startwerte der Gebührenprofile.
- Railway: Config as Code (`railway.json`) ist abgekündigt (Stichtag 2026-12-01), neue Services nutzen nur noch
  **Infrastructure as Code** (`.railway/railway.ts`). Das Format wurde mit `railway config init` der installierten CLI geprüft.
- Keepa: Endpunkte, Domain-IDs, csv-Indizes und Zeitformat wurden gegen den Keepa-Backend-Code geprüft.
  **Amazon NL, BE und PL unterstützt Keepa nicht.** Diese Länder werden als „nicht verfügbar“ gekennzeichnet und folgen über die SP-API.
- Dokumente: [ARCHITECTURE.md](ARCHITECTURE.md), [DEPLOYMENT.md](DEPLOYMENT.md), [API.md](API.md), [N8N.md](N8N.md)

## Phase 2 – MVP: was tatsächlich funktioniert

Lokal getestet: 103 automatisierte Backend-Tests, TypeScript-Check, Next.js-Produktionsbuild und eine Browser-Prüfung
von Login, Dashboard, Opportunities, Detailseite und Matching-Review samt Bestätigung.

| Bereich | Status |
|---|---|
| Lieferantenverwaltung | ✅ Profil (Konditionen, MBW, MOQ, Lieferzeit, Zoll, Rating, E-Mail-Regeln), Feeds (SSRF-geprüft), Preislisten-Historie, Preisänderungen |
| Import (XLSX, XLS, CSV, TSV, JSON, XML, ZIP) | ✅ streamend in 500er-Batches, Blatt- und Kopfzeilenerkennung, Spaltenerkennung (DE/EN-Synonyme, Muster, Inhalt), Prüf-UI, Profil je Lieferant, EAN-Prüfziffer, führende Nullen, netto/brutto, Währung, Staffelpreise, Preiseinheit, Dubletten, Fehlerzeilen, Fortschritt, Wiederaufnahme, Archivierung im Bucket, Preisänderungen |
| Echte Listen | ✅ *Deca Lagerliste* (6.854 Zeilen) und *MANN-FILTER NUT* (4.935 Zeilen, 2 Staffeln) automatisch korrekt zugeordnet, 0 Fehler, ca. 250 Zeilen/s lokal |
| Produktnormalisierung | ✅ Produkt je Verkaufseinheit, Identifikatoren n:1, EAN nicht als globaler Schlüssel, Artikelnummer je Marke |
| Matching Stufe 1–2 und 4 | ✅ GTIN/MPN/Marke, MPN im Titel, Text-Ähnlichkeit, Pack-/Farb-/Maß-/Modell-Erkennung, 8 Klassen, Verkaufseinheit (`units_required`) |
| Matching Stufe 3 (KI) | ✅ implementiert (Claude Structured Output, Bilder, Injection-Schutz, Regelvorrang), getestet mit Fake-Provider. **Nicht live getestet** – `ANTHROPIC_API_KEY` fehlt |
| Human-in-the-Loop | ✅ Prüf-UI Seite an Seite, Bestätigen, Ablehnen, Bundle, Variante, Zurückstellen, Menge korrigieren, Feedback mit Label, Beziehungen und Bundle-Komponenten |
| Kalibrierung | ✅ implementiert. Wirksam erst ab 20 geprüften Labels je Score-Bin, bis dahin als „unkalibriert“ angezeigt |
| Keepa-Adapter | ✅ Produkt-Lookup per EAN, Parsing von Buy Box, Ø30/Ø90, Min90, Preisvolatilität, Rank, Rank-Drops, Anbieter, Amazon-Eigenhandel, Bilder; Token-Management, Cache, Dedup-Locks, Budgets, 429-Handling. **Nicht live getestet** – `KEEPA_API_KEY` fehlt, Tests nutzen eine gekennzeichnete Fixture |
| Profit Engine | ✅ Decimal, USt getrennt, alle Kostenarten, Break-even, max. EK, Kapitalbedarf und -bindung, DB/1.000 €/Monat, 6 Szenarien, fehlende Kosten sichtbar, reproduzierbarer Snapshot |
| Nachfrage und Score | ✅ Absatzschätzung mit Methode, Quelle, Konfidenz, Markt vs. eigener Anteil; Score 0–100 mit konfigurierbaren Gewichten, Datenabdeckung, harte Ausschlusskriterien |
| Einkauf | ✅ Test- und Nachbestellmengen (Budget, Bestand, MOQ, Bundle-Vielfache, MBW-Hinweis), Freigabe-Workflow. **Keine** automatische Bestellung |
| Dashboard | ✅ 13 Menüpunkte. Funktionsfähig: Dashboard, Opportunities (Filter, Sortierung, Mehrfachauswahl, CSV/Excel), Detail, Großhandelslisten, Lieferanten, Amazon Scanner, Bundles (Filteransicht), Matching Review, Einkaufsliste, Einstellungen (Grenzwerte, Gewichte, Budgets, Gebührenprofile), API Monitoring |
| Hintergrundjobs | ✅ Dramatiq + Redis (Queues imports, analysis, maintenance), Retry mit Backoff, Dead-Letter-Queue, Job-Protokoll, Dedup; Scheduler (EZB-Kurse, Neubewertung, Bericht, Löschfristen) |
| Sicherheit | ✅ Login, Rollen, API-Schlüssel, Audit-Log, Upload-Prüfung (+ optional ClamAV), SSRF-Schutz, Login-Rate-Limit, Security-Header, Produktions-Guards |
| Railway | ✅ `.railway/railway.ts` (frontend, backend, worker, scheduler, Postgres, Redis, Bucket, Private Networking, Pre-Deploy-Migration, Healthchecks). **Noch nicht deployt**, `railway config plan` steht noch aus (siehe offene Punkte) |
| n8n | ✅ 6 Workflows erzeugt und strukturell geprüft, **noch nicht in n8n importiert** |

## Live-Tests mit echten Schlüsseln (2026-11-01)

- **Keepa**: Abfrage einer echten EAN (STABILO point 88) liefert 7 Listings, darunter fremde Produkte mit
  falsch gepflegter EAN (Trinkflasche, Korsage). Das bestätigt, dass die EAN allein nicht reicht.
  Buy Box: Keepa liefert sie bei `stats` zusätzlich als `stats.buyBoxPrice` (−2 = aktuell keine Buy Box).
  Der Parser wertet das jetzt aus, ohne Buy Box gilt der niedrigste Neupreis.
- **Keepa-Tarif: 5 Tokens/Minute** (≈ 7.200 Produkte/Tag). 100.000 Artikel/Tag sind damit über Keepa nicht
  erreichbar. Gegenmittel: Cache, nur Angebote mit EAN, Level-1-Vorfilter, ggf. größerer Keepa-Tarif.
- **Claude** (`claude-opus-5-5`, effort low): ca. 1,3 Cent je Prüfung. Bewertet die falsche EAN (Flasche) als Konflikt
  und erkennt bei einem spanischen 10er-Listing die abweichende Farbe („azul claro“ ≠ schwarz). Farbwörter ES/FR/IT
  sind seitdem auch in den Regeln enthalten.

## Was noch fehlt (ehrlich)

- **Live-Verifikation auf Railway**: Bucket, Postgres und Redis (Keepa und Claude sind lokal live getestet).
  Lokal liefen SQLite und der StubBroker; das Postgres-Schema wird erst auf Railway angewendet.
- **Kein Lasttest** mit 100.000 Artikeln/Tag (Phase 6).
- **Phase 3**: Bildvergleich steckt in der KI-Stufe, ein eigener Vision-Workflow mit Bild-Download und Hashing fehlt;
  ebenso Reverse-Bundle-Sourcing, Komponentenverwaltung in der UI und PDF-Import.
- **Phase 4**: SP-API (Gebührenschätzung `getMyFeesEstimate`, Katalog, eigene Verkäufe), eBay Browse API, Kaufland.
  Bis dahin stammen die Gebühren aus Gebührenprofilen (als *Annahme* markiert).
- **Phase 5**: Online Opportunity Hunter mit Quellenverwaltung, Preisalarme. Die Menüpunkte zeigen ehrlich „nicht implementiert“.
- **Phase 6**: Monitoring-Integration (z. B. Sentry), Backups prüfen, Lasttests, Performance-Tuning (Bulk-Inserts).
- Eigene Verkaufshistorie (SP-API) fehlt noch. Absatzschätzungen basieren bis dahin auf Keepa-Indikatoren.
- Embeddings/pgvector sind vorbereitet (Extension wird bei Verfügbarkeit aktiviert), aber noch nicht genutzt.

## Annahmen

- Die Gebührenprofile sind Startannahmen: Amazon 15 % (min. 0,30 €), FBA 3,50 €, FBM-Versand 4,90 €, eBay 11 % + 0,35 €,
  Werbung 5 %, Retouren 3 % × 5 €. Sie sind im Dashboard als „Annahme“ markiert und müssen je Kategorie geprüft werden.
- Die USt-Standardsätze (DE 19, FR 20, IT 22, ES 21, NL 21, BE 21, PL 23 %) können für ermäßigte Kategorien abweichen.
- Konfektionierung je Multipack/Bundle: 0,50 € (Annahme).
- Absatz aus Rank-Drops = Untergrenze, realistischer eigener Anteil standardmäßig 15 % und höchstens 1,5 / Anbieterzahl.
- Kapitalbindung: volle Bindung während der Lieferzeit, danach linearer Abverkauf.
