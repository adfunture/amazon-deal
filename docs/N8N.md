# n8n-Workflows

n8n ist **optional**. Alle Zeitpläne, die das System für sich selbst braucht (Neubewertung, Wechselkurse, Bericht,
Löschfristen), laufen im Railway-Service `scheduler`. n8n lohnt sich vor allem für **E-Mail-Postfächer**
(IMAP-Anhänge) und für eigene Integrationen. Die Datenverarbeitung hängt nie an n8n.

Die Dateien liegen in `infrastructure/n8n/` und werden mit `python scripts/build_n8n_workflows.py` erzeugt.
Alle Workflows sind **inaktiv** exportiert.

## Einrichtung

1. Im Backend einen Benutzer mit Rolle `buyer` anlegen, z. B. `n8n@…`, und dafür einen API-Schlüssel erzeugen:
   `POST /api/v1/auth/api-keys {"name":"n8n","user_email":"n8n@…"}` (als Admin). Den Schlüssel sicher übernehmen.
2. In n8n Credentials anlegen:
   - **SBC API Key**: Typ *Header Auth*, Name `X-API-Key`, Wert = Schlüssel aus Schritt 1
   - **Lieferanten-Postfach**: Typ *IMAP* (nur Workflow 1)
   - **Webhook Secret**: Typ *Header Auth* (nur Webhook in Workflow 1)
   - **SMTP**: nur Workflow 6, und nur wenn der E-Mail-Versand gewünscht ist
3. In den JSON-Dateien `REPLACE-WITH-BACKEND-DOMAIN` durch die öffentliche Backend-Domain ersetzen.
   n8n-Variablen (`$vars`) gibt es nur in kostenpflichtigen Editionen, daher dieser Platzhalter.
4. Workflow importieren (*Import from File*), die Credentials in den Knoten auswählen, testen und erst dann aktivieren.

## Workflows

| Datei | Auslöser | Ablauf | Fehlerbehandlung |
|---|---|---|---|
| `01-supplier-import.json` | IMAP (ungelesene Mails) oder Webhook `POST /webhook/sbc-supplier-import` | Anhänge filtern → Lieferant per Absender (`email_import_rules`) → `POST /imports` → bei bekanntem Profil **und** `auto_start` des Lieferanten: Import starten, sonst manuelle Zuordnung im Dashboard | Unbekannter Absender bzw. abgelehnter Upload → Stop-and-Error; 3 Wiederholungen bei HTTP-Fehlern |
| `02-product-matching.json` | alle 30 min | `POST /pipeline/analyze-pending` (ohne Keepa) → Status | unsichere Treffer landen automatisch im Matching-Review |
| `03-marketplace-analysis.json` | alle 6 h | Keepa-Analyse neuer Angebote, veraltete Marktdaten erneuern | Token- und Budgetgrenzen setzt das Backend durch (429 → Wiedereinplanung) |
| `04-profit-analysis.json` | täglich 05:00 | `POST /pipeline/recalculate` | – |
| `05-online-opportunity-hunter.json` | alle 6 h | Liste **autorisierter** Feeds (Code-Knoten) → Download → `POST /imports` | nicht erreichbare Quelle → Fehler; keine Captcha-/Sperrenumgehung |
| `06-daily-sourcing-report.json` | werktags 06:45 | `GET /reports/daily` (dedupliziert) → HTML-Bericht → E-Mail-Knoten **deaktiviert** | – |

**E-Mail-Importregeln** beim Lieferanten (Feld `email_import_rules`):

```json
{ "senders": ["preise@lieferant.de"], "domains": ["lieferant.de"], "auto_start": false }
```

`auto_start: true` startet den Import ohne Rückfrage, sofern die Spalten zu einem gespeicherten Profil passen.
Unbekannte Spaltenstrukturen warten immer auf die manuelle Prüfung.

**Hinweis:** Die Workflows wurden strukturell geprüft (gültiges JSON, alle Verbindungen zeigen auf vorhandene Knoten),
aber noch nicht in einer laufenden n8n-Instanz importiert. Knotenversionen: HTTP Request 4.2, Schedule Trigger 1.2,
Email Read IMAP 2, Webhook 2, Code 2, If 2.
