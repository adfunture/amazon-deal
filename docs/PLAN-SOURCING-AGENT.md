# Implementierungsplan: Intelligent Sourcing Agent

Stand: 2026-11-01 · Grundlage: Repository `adfunture/amazon-deal` (Commit 50f3f44). Dieses Dokument ist die
Bestandsaufnahme und der Plan. **Es wurden noch keine Änderungen am Code vorgenommen.**

## 0. Ausgangslage

- Backend (FastAPI, Dramatiq, Alembic) und Frontend (Next.js) sind die in Phase 1/2 gebaute Anwendung (~6.900 Zeilen Python, 105 Tests).
- **Railway:** Frontend, Postgres, Redis und Bucket laufen in EU West. **Backend, Worker und Scheduler starten noch nicht**,
  weil die Variablen (`APP_SECRET` usw.) fehlen. Deshalb gibt es noch **keine Produktivdaten**. Jede Schemaänderung ist
  heute noch risikolos, ab dem ersten echten Import nur noch per additiver Migration.
- **Postgres auf Railway hat kein pgvector.** Das Standard-Image `postgres-ssl:18` enthält die Extension nicht.
  Die Migration hat sie deshalb still übersprungen (siehe Entscheidung E2).

---

## 1. Was bereits vorhanden ist (wiederverwendbar)

| Bereich | Vorhanden | Datei |
|---|---|---|
| Import | Streaming-Reader, Spaltenerkennung, Profile je Lieferant, Batches, Wiederaufnahme, Dubletten, Fehlerzeilen | `importer/*` |
| Lieferantenangebote | `supplier_offers` je (Lieferant, Marke + Artikelnr.); `is_current`, `previous_unit_price_net`, `last_price_change_at` | `models.py` |
| EK-Historie | `price_history` (Metrik `supplier_net`), wird **nur bei Preisänderung** geschrieben | `importer/service.py` |
| Import-Statistik | neu, Preisänderungen, Dubletten, nicht mehr gelistet | `ImportJob.stats` |
| Produkte | `products` (Verkaufseinheit), `product_identifiers` (GTIN/MPN/SKU/ASIN, nicht eindeutig) | `models.py` |
| Matching | Identifikatoren, MPN im Titel, Marke, Text (RapidFuzz), Pack, Farbe (DE/EN/ES/FR/IT), Maße, Modell-Tokens; 8 Klassen; `units_required` | `matching/engine.py` |
| KI-Prüfung | Claude Structured Output inkl. Bild-URLs, Prompt-Injection-Schutz, Regelvorrang, Budget | `matching/ai.py` |
| Kalibrierung | Score-Bins mit isotoner Glättung aus Review-Labels | `matching/calibration.py` |
| Review | Paar-Ansicht, bestätigen, ablehnen, Bundle, Variante, zurückstellen, Menge; Feedback, `product_relations`, `bundle_components` | `api/routers/matching.py`, Frontend `/matching` |
| Keepa | Lookup per EAN (Batch ≤ 100), Cache (DB + Negativ-Cache), Locks, Token-Stand, Budgets, 429-Handling; Buy-Box-/Rank-Parsing live geprüft | `integrations/keepa*.py` |
| Kalkulation | Zentrale `profit/engine.py` (Decimal, USt, alle Kosten, Szenarien), `pipeline.evaluate()` | `profit/`, `pipeline.py` |
| Scoring | Nachfrage, Wettbewerb, Score, Ausschlusskriterien | `scoring/engine.py` |
| Hintergrund | Dramatiq-Queues, Retry, Dead-Letter, Job-Protokoll, Redis-Dedup; Scheduler (Neubewertung alle 6 h) | `worker/`, `scheduler/` |
| UI | Dashboard, Opportunities, Detail (Szenarien, VK-Verlauf), Importe, Lieferanten (Preisänderungen), Amazon Scanner, Einkaufsliste, Monitoring | `apps/frontend` |

## 2. Was fehlt – je Komponente

### Komponente 1 – Product Matching

1. **Keine Kandidatensuche ohne EAN.** Kandidaten kommen heute nur über GTIN (Keepa) und MPN-Gleichheit mit bereits
   bekannten Listings. Artikel ohne EAN finden praktisch nie ein Amazon-Listing. *Größte Lücke.*
2. **Bekannte Zuordnungen** werden nur paarweise gemerkt. Es fehlt eine Zuordnung Produkt ↔ ASIN, die ein neuer
   Lieferant mit derselben Ware automatisch erbt, und ein **Identitäts-Fingerprint**, der bei Änderungen eine Neuprüfung auslöst.
3. **Mehrsprachigkeit:** keine Spracherkennung, keine Synonymwörterbücher (Men's ↔ Herren, Bike Carrier ↔ Fahrradträger),
   NL/PL fehlen, keine Embeddings.
4. **Merkmale:** Größen (S–XXL, Konfektions-/Schuhgrößen), Material und Lieferumfang werden nicht extrahiert.
5. **Status und Score:** Es gibt 8 Beziehungsklassen, aber keinen klaren Entscheidungsstatus
   (VERIFIED_MATCH / MANUAL_REVIEW / NO_MATCH). Der Score (0–1) ist nicht als Punkte je Signal erklärbar, und harte
   Identitätskonflikte (Farbe, Größe, Modell, Pack) sind nur teilweise als Sperre umgesetzt.
6. **Bildvergleich** nur über die KI. Ein günstiges Vorsignal (perceptual hash) fehlt.
7. **Review-Oberfläche** ist paarbasiert. „Andere ASIN wählen“, Kandidatenliste je Lieferantenartikel und Bildergalerie fehlen.

### Komponente 2 – Einkaufspreishistorie

1. **Keine echte Preisversion.** `price_history` speichert nur den Betrag. Es fehlen Import-Batch, Quelldatei, Preisgültigkeit
   (von/bis), Staffeln, MOQ, VPE, Währung und Preis je Stück zum jeweiligen Stand. Unveränderte Preise werden nicht bestätigt
   (kein „zuletzt bestätigt am“).
2. **Import-Auswertung** ohne Aufteilung in unverändert / gesunken / gestiegen.
3. **Re-Import ist nicht delta-gesteuert:** Nach jedem Import werden *alle* Angebote erneut analysiert (Keepa-Lookup und
   Matching je Angebot). Das kostet Tokens, und bestätigte Matches werden zwar nicht überschrieben, aber unnötig neu bewertet.
4. Kein Fallback, wenn ein Lieferant die Artikelnummer ändert, die EAN aber gleich bleibt.
5. Keine Kennzahlen je Artikel: min/max/Ø-EK, 30/90/180-Tage-Trend, günstigster aktueller Lieferant (währungs- und VPE-bereinigt).
6. UI: Importhistorie unvollständig, kein EK-Diagramm je Lieferant auf der Produktseite.

### Komponente 3 – Continuous Monitoring

1. **Kein Monitoring-Objekt.** Es gibt nur einen pauschalen 6-h-Job für Opportunities. Prioritäten A/B/C, Intervalle,
   Pause, Favoriten und nächste Fälligkeit fehlen.
2. **Keepa wird je Angebot einzeln abgefragt** (1 Request pro EAN) statt gebündelt per ASIN (bis 100 je Request).
3. **Zeitpunkte:** Es gibt nur `fetched_at`. Letzter Lauf, letzter erfolgreicher Keepa-Abruf und Datenstand (Keepa `lastUpdate`) sind nicht getrennt.
4. **Snapshots:** Jede Neubewertung schreibt 6 Kalkulationszeilen und eine Absatzschätzung. Die Tabellen wachsen ungebremst; es fehlt „nur bei Änderung speichern“.
5. **Opportunity-Ereignisse und Benachrichtigungen** fehlen komplett, ebenso Hysterese gegen Flattern und Dedup.
6. **Kaufbereitschaft:** Es fehlt die Unterscheidung „rechnerisch profitabel“ vs. „geprüft kaufbereit“.
   Heute sind *alle* Gebührenprofile Annahmen, also könnte kein Produkt streng genommen kaufbereit sein.
7. Grenzwerte: Mindest-ROI (15 %) und Verbesserungsschwelle (30 %) fehlen; vorhanden sind Marge 12 % und DB 5 €.
8. UI: Smart Watchlist und In-App-Alerts fehlen.

## 3. Datenbankänderungen (eine additive Migration je Phase, nichts wird gelöscht)

| Tabelle / Spalte | Zweck | Phase |
|---|---|---|
| `asin_mappings` (product_id, listing_id, marketplace, status VERIFIED/REJECTED/REVIEW, identity_fingerprint, matching_result_id, decided_by/at) | dauerhafte, wiederverwendbare Zuordnung Produkt ↔ ASIN | 2 |
| `matching_results` + `decision_status`, `score_100`, `score_breakdown`, `identity_conflicts`, `candidate_rank` | erklärbarer Score, harte Sperren | 2 |
| `supplier_offers` + `identity_fingerprint`, `language`, `attributes` (Größe, Material, Lieferumfang) | Neuprüfung nur bei Identitätsänderung | 2 |
| `offer_embeddings` / `listing_embeddings` (Vektor; pgvector oder Float-Array, siehe E2) | semantische Kandidatensuche | 2 |
| `supplier_price_versions` (offer_id, import_job_id, source_file, valid_from/until, imported_at, last_confirmed_at, unit_price_net, currency, price_basis_qty, unit_quantity, price_per_piece_eur + fx_rate, moq, tier_prices, change_type, previous_version_id) | versionierte EK-Historie | 3 |
| `supplier_offers` + `current_price_version_id`, `delisted_at` | aktueller Stand / nicht mehr gelistet | 3 |
| Übernahme vorhandener `price_history`-Zeilen (`supplier_net`) als Versionen | Bestand bleibt erhalten | 3 |
| `marketplace_listings` + `data_as_of`, `last_refresh_attempt_at`, `last_refresh_ok_at`, `keepa_last_update` | getrennte Zeitstempel | 4 |
| `monitoring_watches` (listing_id, priority A/B/C, interval_override, paused, is_favorite, next_due_at, last_run_at, last_error) | Monitoring-Aufträge | 4 |
| `profit_calculations` + `input_hash`, `trigger`, `data_as_of` | Snapshot nur bei Änderung, nachvollziehbar | 5 |
| `opportunities` + `profit_state`, `readiness_status`, `readiness_reasons`, `is_favorite`, `last_event_at` | Hysterese, Kaufbereitschaft | 5 |
| `opportunity_events` (type, opportunity_id, payload, dedupe_key **unique**, created_at) | NEW/REACTIVATED/IMPROVED/LOST/EK-Änderung/NEW_CHEAPEST_SUPPLIER/DATA_STALE/MANUAL_REVIEW | 5 |
| `notifications` (user/org, event_id, channel in_app/email/webhook, read_at, delivered_at) | In-App-Alerts, später E-Mail/n8n | 5 |
| `app_settings`: `monitoring` (Intervalle A/B/C), `opportunity_rules` (min_profit 5 €, min_roi 15 %, improvement 30 %, Hysterese), `staleness` (EK-/Amazon-Daten max. Alter) | konfigurierbar im Dashboard | 4/5 |

Migrationen entstehen nur lokal gegen eine leere Datenbank und laufen auf Railway per Pre-Deploy (`alembic upgrade head`).

## 4. Zusammenspiel der Komponenten (ereignisgesteuert)

```
Import (Worker) ──► je Zeile: Angebot zuordnen (Marke+Art.-Nr. → Fallback EAN innerhalb Lieferant)
                    ├─ neu                      ─► Matching-Auftrag
                    ├─ Identität geändert       ─► Mapping → REVIEW, Matching-Auftrag
                    ├─ Preis geändert           ─► neue Preisversion ─► Neukalkulation
                    ├─ Preis unverändert        ─► last_confirmed_at aktualisieren
                    └─ nicht mehr gelistet      ─► delisted_at, Neukalkulation (Angebot entfällt)

Matching (Worker)  ─► Stufe 1 bekannte Mappings → 2 EAN/MPN → 3 Marke/Modell → 4 mehrsprachiger Titel/Embedding
                      → 5 Bild-Hash → 6 KI (nur unklar & wirtschaftlich relevant)
                      ─► VERIFIED: asin_mapping + Watch anlegen ─► Neukalkulation
                      ─► MANUAL_REVIEW: Review-Queue + Ereignis MANUAL_REVIEW

Monitoring-Tick (Scheduler alle 5 min) ─► fällige Watches (FOR UPDATE SKIP LOCKED), nach Priorität
                      ─► Keepa-Batch per ASIN (≤ 100, Tokenbudget) ─► Listing + Datenstand
                      ─► Neukalkulation aller Angebote mit Mapping auf dieses Listing

Neukalkulation      ─► ausschließlich pipeline.evaluate() / profit.engine (keine zweite Logik)
                      ─► Snapshot nur bei geändertem input_hash
                      ─► Zustandsvergleich mit Hysterese ─► opportunity_events (dedupe_key) ─► notifications
```

Mehrere Worker: Redis-Dedup je Auftrag, `SKIP LOCKED` für Watches, eindeutige `dedupe_key` für Ereignisse, und jeder
Auftrag ist idempotent. Nach einem Neustart greifen deshalb Dramatiq-Retry und die Fälligkeitslogik, ohne Datenverlust.

## 5. API-Kosten

**Keepa (Ihr Tarif: 5 Tokens/Min ≈ 7.200/Tag, live gemessen)**

| Vorgang | Kosten | Hinweis |
|---|---|---|
| Produkt per EAN oder ASIN | 1 Token je Produkt | eine EAN kann mehrere Listings liefern (live: 7) |
| Produktsuche per Titel (ohne EAN) | **10 Tokens je Ergebnisseite** (≤ 10 Treffer) | laut Keepa-Doku; nur nach Vorfilter einsetzen |
| Monitoring-Kapazität (Beispiel) | 300 × A (6 h) = 1.200 · 3.000 × B (täglich) = 3.000 · 5.000 × C (wöchentlich) ≈ 715 | ≈ 4.900 Tokens/Tag, Rest für neue Importe |

Folge: Das Monitoring muss konsequent per ASIN bündeln und Prioritäten einhalten. Titelsuchen ohne EAN sind teuer und
nur für vorab kalkulatorisch interessante Artikel sinnvoll (EK und UVP vorhanden). Mehr Volumen erfordert einen größeren Keepa-Tarif.

**Claude (live gemessen):** ca. 1,3 Cent je Text-Prüfung; mit Bildern geschätzt 2–4 Cent. Aufruf nur bei unklarem Match
**und** erwartetem DB über der Schwelle. Bereits verifizierte Mappings lösen keinen Aufruf aus.

**Embeddings:** siehe E2. Lokales Modell: 0 € API-Kosten, aber ca. 0,5–1 GB mehr RAM im Worker (Railway-Kosten).
API-Variante (z. B. Voyage multilingual): Kosten im Bereich weniger Euro je 100.000 Titel.

## 6. Risiken

1. **EAN-Qualität bei Amazon:** live gesehen, dass eine EAN zu fremden Produkten gehört. EAN darf nie allein verifizieren.
2. **Keepa-Kontingent** begrenzt Monitoring und Titelsuche (siehe 5).
3. **Gebühren sind Annahmen:** Ohne SP-API-Gebührenschätzung oder geprüfte Profile erreicht kein Produkt den Status
   „geprüft kaufbereit“. Das ist gewollt, sollte aber bewusst entschieden werden (E4).
4. **Hysterese falsch eingestellt** führt zu Alarmflut oder verschluckten Chancen. Daher konfigurierbar, mit Tests.
5. **Datenwachstum** durch Snapshots. Abhilfe: nur bei Änderung speichern, ältere Snapshots verdichten.
6. **Varianten und Größen** (Textil): hohe Verwechslungsgefahr. Größe und Farbe sind deshalb harte Sperrmerkmale.
7. **Währungen und VPE:** Vergleichbar sind nur Preise je Stück in EUR zum jeweiligen Kurs; Staffeln getrennt ausweisen.
8. **Migrationen auf Produktion:** nur additiv, nie Autogenerate gegen Prod. Vor jeder Migration ein Backup.
9. **pgvector-Wechsel** ist nur jetzt (leere DB) risikolos.

## 7. Entscheidungen, die ich von Ihnen brauche

- **E1 Statusmodell:** Die 8 bestehenden Klassen (Exakt, Multipack, Bundle, Variante …) bleiben als *Beziehungsart* erhalten.
  Zusätzlich gibt es den *Entscheidungsstatus* VERIFIED_MATCH / MANUAL_REVIEW / NO_MATCH. **Empfehlung: ja.**
- **E2 Embeddings / pgvector:** (a) Postgres jetzt, solange leer, auf ein Image mit pgvector umstellen und ein lokales
  mehrsprachiges Modell im Worker nutzen; (b) ohne pgvector, Vektoren als Array und Kandidatensuche vorgefiltert nach Marke;
  (c) Embedding-API. **Empfehlung: (a)**, weil es jetzt kostenlos und risikofrei möglich ist.
- **E3 Artikel ohne EAN:** Keepa-Titelsuche (10 Tokens/Seite) nur für vorgefilterte Artikel, oder Amazon SP-API Catalog
  (kostenlos, braucht SP-API-Zugang). **Frage: Gibt es SP-API-Zugangsdaten (Developer-Registrierung)?**
- **E4 Kaufbereitschaft:** Gilt ein manuell geprüftes Gebührenprofil (Häkchen „geprüft“) als „Gebühren zuverlässig“, bis die SP-API da ist? **Empfehlung: ja.**
- **E5 Schwellen:** Gewinn ≥ 5 €, ROI ≥ 15 %, Verbesserung ≥ 30 %. Die bisherige Mindestmarge 12 % bleibt optional. **Empfehlung: so übernehmen.**

## 8. Umsetzungsreihenfolge

**Vorab (≈ 0,5 Tag):** Railway-Variablen setzen und Backend, Worker und Scheduler zum Laufen bringen; je nach E2 Postgres-Image umstellen.

| Schritt | Inhalt | Ergebnis / Tests |
|---|---|---|
| **P2a** Matching-Grundlage | Entscheidungsstatus, Score 0–100 mit Begründung je Signal, harte Konfliktsperren, `asin_mappings` inkl. Wiederverwendung, Identitäts-Fingerprint | Tests: EAN-Treffer, Farbe/Größe verschieden, Einzel vs. Multipack, ähnlich ≠ identisch, Bestätigen/Ablehnen |
| **P2b** Mehrsprachigkeit | Spracherkennung, Normalisierung, Synonym- und Farb-/Größenlexika DE/EN/FR/IT/ES/NL/PL, Merkmalsextraktion (Größe, Material, Lieferumfang); Marken/Modell/Artikelnummern bleiben unverändert | Tests: Jack & Jones EN↔DE, Thule EN↔DE, Wortreihenfolge |
| **P2c** Kandidatensuche & Bild | Embeddings (E2), Kandidaten ohne EAN (E3), Bild-Hash als Zusatzsignal, KI nur Stufe 6 | Tests: fehlende EAN, Bildvergleich (Fixtures) |
| **P2d** Review-UI | Ansicht je Lieferantenartikel mit Kandidatenliste, andere ASIN wählen, Bildergalerie, VPE-Prüfung | UI-Test im Browser |
| **P3** Preishistorie | `supplier_price_versions`, delta-gesteuerter Import (nur neue/geänderte Identitäten matchen), EAN-Fallback, Kennzahlen (min/max/Ø, 30/90/180, günstigster Lieferant), Importhistorie, EK-Diagramm | Tests: Erstimport, identischer Re-Import, EK ↑/↓, neu/entfernt, neue Staffel, Währung, VPE, Historie bleibt |
| **P4** Monitoring | `monitoring_watches`, Prioritäten A/B/C (konfigurierbar, pausierbar), Scheduler-Tick, Keepa-Batch per ASIN, getrennte Zeitstempel, DATA_STALE | Tests: Budget erschöpft, veraltete Daten, Pause, Worker-Neustart |
| **P5** Opportunities & Alerts | Snapshots bei Änderung, Hysterese, Ereignistypen, Dedup, Kaufbereitschaft, In-App-Benachrichtigungen (Kanal-Feld für E-Mail/n8n) | Tests: wird profitabel / wieder / verliert, EK- und VK-Änderung lösen Neukalkulation aus, keine doppelten Meldungen |
| **P6** Watchlist & QS | Smart Watchlist, Filter, Favoriten, manuelle Aktualisierung, Alert-Glocke; Gesamttest aller Komponenten, Regressionstests der bestehenden Funktionen | Ende-zu-Ende-Test mit Mock-Keepa |

Jeder Schritt wird einzeln committet, lokal vollständig getestet (bestehende 105 Tests plus neue) und erst danach deployt.
Keine automatischen Einkäufe, keine neuen Sourcing-Quellen.
