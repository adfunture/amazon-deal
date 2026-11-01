"""Erzeugt die importierbaren n8n-Workflows unter infrastructure/n8n/.

Alle Workflows sind INAKTIV exportiert (active=false). Zugangsdaten sind Platzhalter:
* n8n-Credential "SBC API Key" (Typ Header Auth, Header "X-API-Key")
* n8n-Credential "Lieferanten-Postfach" (IMAP)
* n8n-Credential "SMTP" (nur Workflow 6; E-Mail-Knoten ist deaktiviert)
* Backend-URL: Platzhalter REPLACE-WITH-BACKEND-DOMAIN vor dem Import ersetzen
  (n8n-Variablen sind nur in kostenpflichtigen Editionen verfügbar)

Aufruf: python scripts/build_n8n_workflows.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "infrastructure" / "n8n"
API = "=https://REPLACE-WITH-BACKEND-DOMAIN/api/v1"  # nach Import einmal ersetzen
API_CRED = {"httpHeaderAuth": {"id": "REPLACE_WITH_CREDENTIAL_ID", "name": "SBC API Key"}}


def node(name, ntype, version, pos, params, **extra):
    n = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, name + ntype)), "name": name,
         "type": ntype, "typeVersion": version, "position": pos}
    n.update(extra)
    return n


def http(name, pos, method, path, *, body=None, query=None, multipart=None, on_error="continueErrorOutput"):
    p = {"method": method, "url": f"{API}{path}", "authentication": "genericCredentialType",
         "genericAuthType": "httpHeaderAuth", "options": {"timeout": 120000}}
    if query:
        p["sendQuery"] = True
        p["queryParameters"] = {"parameters": [{"name": k, "value": v} for k, v in query.items()]}
    if body is not None:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = body
    if multipart:
        p["sendBody"] = True
        p["contentType"] = "multipart-form-data"
        p["bodyParameters"] = {"parameters": multipart}
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, p, credentials=API_CRED,
                retryOnFail=True, maxTries=3, waitBetweenTries=5000, onError=on_error)


def schedule(name, pos, cron):
    return node(name, "n8n-nodes-base.scheduleTrigger", 1.2, pos,
                {"rule": {"interval": [{"field": "cronExpression", "expression": cron}]}})


def code(name, pos, js):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})


def stop(name, pos, msg):
    return node(name, "n8n-nodes-base.stopAndError", 1, pos, {"errorMessage": msg})


def link(*pairs):
    conns: dict = {}
    for src, dst, *rest in pairs:
        out = rest[0] if rest else 0
        conns.setdefault(src, {"main": []})
        while len(conns[src]["main"]) <= out:
            conns[src]["main"].append([])
        conns[src]["main"][out].append({"node": dst, "type": "main", "index": 0})
    return conns


def workflow(name, nodes, connections, note):
    return {"name": name, "nodes": nodes, "connections": connections, "active": False,
            "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin", "saveManualExecutions": True},
            "meta": {"templateCredsSetupCompleted": False}, "pinData": {},
            "tags": [{"name": "SBC Sourcing"}], "staticData": None,
            "versionId": str(uuid.uuid5(uuid.NAMESPACE_URL, name)),
            "description": note}


SPLIT_ATTACHMENTS = """// Jeden unterstützten Anhang als eigenes Item ausgeben
const allowed = ['xlsx','xlsm','xls','csv','tsv','txt','json','xml','zip'];
const out = [];
for (const item of $input.all()) {
  const sender = (item.json.from?.value?.[0]?.address ?? item.json.from ?? '').toString().toLowerCase();
  for (const [key, bin] of Object.entries(item.binary ?? {})) {
    const ext = (bin.fileName ?? '').split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) continue;
    out.push({ json: { sender, fileName: bin.fileName, subject: item.json.subject }, binary: { file: bin } });
  }
}
return out;"""


def wf_supplier_import():
    nodes = [
        node("Lieferanten-Postfach", "n8n-nodes-base.emailReadImap", 2, [0, 0],
             {"mailbox": "INBOX", "postProcessAction": "read", "format": "resolved", "downloadAttachments": True,
              "options": {"customEmailConfig": '["UNSEEN"]'}},
             credentials={"imap": {"id": "REPLACE_WITH_CREDENTIAL_ID", "name": "Lieferanten-Postfach"}}),
        node("Webhook Upload", "n8n-nodes-base.webhook", 2, [0, 220],
             {"httpMethod": "POST", "path": "sbc-supplier-import", "authentication": "headerAuth",
              "responseMode": "onReceived", "options": {"binaryPropertyName": "file"}},
             credentials={"httpHeaderAuth": {"id": "REPLACE_WITH_CREDENTIAL_ID", "name": "Webhook Secret"}},
             webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "sbc-supplier-import"))),
        code("Anhänge aufteilen", [240, 0], SPLIT_ATTACHMENTS),
        code("Webhook normalisieren", [240, 220],
             "return $input.all().map(i => ({ json: { sender: (i.json.query?.sender ?? i.json.body?.sender ?? '').toLowerCase(),"
             " supplier_id: i.json.query?.supplier_id ?? i.json.body?.supplier_id ?? null }, binary: { file: Object.values(i.binary ?? {})[0] } }));"),
        http("Lieferant zuordnen", [480, 100], "GET", "/suppliers-lookup/by-email", query={"sender": "={{ $json.sender }}"}),
        code("Daten zusammenführen", [720, 0],
             "const src = $('Lieferant zuordnen').all();\n"
             "return $input.all().map((it, i) => ({ json: { ...it.json, supplier_id: it.json.supplier_id ?? src[i]?.json?.supplier_id,"
             " auto_start: src[i]?.json?.auto_start ?? false }, binary: $('Anhänge aufteilen').all()[i]?.binary ?? $('Webhook normalisieren').all()[i]?.binary }));"),
        http("Importjob anlegen", [960, 0], "POST", "/imports",
             multipart=[{"parameterType": "formData", "name": "supplier_id", "value": "={{ $json.supplier_id }}"},
                        {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "file"}]),
        node("Profil erkannt & Autostart?", "n8n-nodes-base.if", 2, [1200, 0],
             {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"}, "combinator": "and",
                             "conditions": [
                                 {"leftValue": "={{ $json.analysis.profile_applied }}", "rightValue": True,
                                  "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
                                 {"leftValue": "={{ $('Daten zusammenführen').item.json.auto_start }}", "rightValue": True,
                                  "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}}),
        http("Import starten", [1440, -80], "PUT", "/imports/{{ $json.id }}/mapping",
             body='={{ JSON.stringify({ mapping: $json.column_mapping, save_profile: false, start: true }) }}'),
        node("Manuelle Zuordnung nötig", "n8n-nodes-base.noOp", 1, [1440, 100], {}),
        stop("Fehler: Lieferant unbekannt", [720, 220], "Absender ist keinem Lieferanten zugeordnet (email_import_rules)"),
        stop("Fehler: Upload abgelehnt", [1200, 220], "Upload vom Backend abgelehnt – Details in der Ausführung"),
    ]
    conns = link(("Lieferanten-Postfach", "Anhänge aufteilen"), ("Webhook Upload", "Webhook normalisieren"),
                 ("Anhänge aufteilen", "Lieferant zuordnen"), ("Webhook normalisieren", "Lieferant zuordnen"),
                 ("Lieferant zuordnen", "Daten zusammenführen", 0), ("Lieferant zuordnen", "Fehler: Lieferant unbekannt", 1),
                 ("Daten zusammenführen", "Importjob anlegen"),
                 ("Importjob anlegen", "Profil erkannt & Autostart?", 0), ("Importjob anlegen", "Fehler: Upload abgelehnt", 1),
                 ("Profil erkannt & Autostart?", "Import starten", 0), ("Profil erkannt & Autostart?", "Manuelle Zuordnung nötig", 1))
    return workflow("SBC 1 – Supplier Import", nodes, conns,
                    "E-Mail-Anhänge/Webhook → Importjob. Start nur automatisch, wenn ein gespeichertes Profil passt und auto_start beim Lieferanten aktiv ist.")


def wf_simple(name, cron, steps, note):
    nodes = [schedule("Zeitplan", [0, 0], cron)]
    conns = []
    prev = "Zeitplan"
    x = 240
    for title, method, path, body in steps:
        nodes.append(http(title, [x, 0], method, path, body=body))
        conns.append((prev, title, 0))
        prev = title
        x += 240
    nodes.append(stop("Fehler melden", [x - 240, 200], "Backend-Aufruf fehlgeschlagen – siehe Ausführungsdetails"))
    for n in nodes[1:-1]:
        conns.append((n["name"], "Fehler melden", 1))
    return workflow(name, nodes, link(*conns), note)


REPORT_HTML = """const items = $json.items ?? [];
if (!items.length) return [{ json: { skip: true } }];
const rows = items.map((it, i) => `<tr><td>${i + 1}</td><td>${it.title ?? ''}</td><td>${it.gtin ?? ''}</td><td>${it.asin ?? ''}</td>`
  + `<td>${it.supplier}</td><td>${it.buy} €</td><td>${it.sell} €</td><td>${it.contribution} €</td><td>${it.score}</td><td>${it.test_qty ?? ''}</td></tr>`).join('');
const html = `<h2>SBC Sourcing – Top-Einkaufschancen</h2><table border="1" cellpadding="4" style="border-collapse:collapse">`
  + `<tr><th>#</th><th>Produkt</th><th>EAN</th><th>ASIN</th><th>Lieferant</th><th>EK netto</th><th>VK brutto</th><th>DB</th><th>Score</th><th>Testmenge</th></tr>${rows}</table>`
  + `<p>Vorschläge – jeder Einkauf erfordert manuelle Freigabe in der Einkaufsliste.</p>`;
return [{ json: { skip: false, html, count: items.length } }];"""


def wf_report():
    nodes = [
        schedule("Täglich 06:45", [0, 0], "45 6 * * 1-5"),
        http("Top Opportunities (dedupliziert)", [240, 0], "GET", "/reports/daily?limit=25"),
        code("Bericht erzeugen", [480, 0], REPORT_HTML),
        node("Etwas zu berichten?", "n8n-nodes-base.if", 2, [720, 0],
             {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"}, "combinator": "and",
                             "conditions": [{"leftValue": "={{ $json.skip }}", "rightValue": False,
                                             "operator": {"type": "boolean", "operation": "false", "singleValue": True}}]}}),
        node("E-Mail an Einkauf (DEAKTIVIERT)", "n8n-nodes-base.emailSend", 2.1, [960, -60],
             {"fromEmail": "REPLACE@example.com", "toEmail": "REPLACE@example.com",
              "subject": "=SBC Sourcing – {{ $json.count }} Einkaufschancen", "emailFormat": "html", "html": "={{ $json.html }}",
              "options": {}},
             credentials={"smtp": {"id": "REPLACE_WITH_CREDENTIAL_ID", "name": "SMTP"}}, disabled=True),
        stop("Fehler melden", [480, 200], "Bericht konnte nicht abgerufen werden"),
    ]
    conns = link(("Täglich 06:45", "Top Opportunities (dedupliziert)"),
                 ("Top Opportunities (dedupliziert)", "Bericht erzeugen", 0), ("Top Opportunities (dedupliziert)", "Fehler melden", 1),
                 ("Bericht erzeugen", "Etwas zu berichten?"), ("Etwas zu berichten?", "E-Mail an Einkauf (DEAKTIVIERT)", 0))
    return workflow("SBC 6 – Daily Sourcing Report", nodes, conns,
                    "Erzeugt den Tagesbericht. Der E-Mail-Knoten ist deaktiviert und muss bewusst aktiviert werden.")


def wf_hunter():
    nodes = [
        schedule("Alle 6 Stunden", [0, 0], "0 */6 * * *"),
        code("Quellen (autorisierte Feeds)", [240, 0],
             "// Nur offizielle APIs / autorisierte Feeds eintragen. Keine Captcha- oder Sperrenumgehung.\n"
             "return [\n  // { json: { url: 'https://partner.example/feed.csv', supplier_id: 'UUID', fileName: 'feed.csv' } },\n];"),
        node("Feed herunterladen", "n8n-nodes-base.httpRequest", 4.2, [480, 0],
             {"method": "GET", "url": "={{ $json.url }}", "options": {"response": {"response": {"responseFormat": "file", "outputPropertyName": "file"}}, "timeout": 120000}},
             retryOnFail=True, maxTries=3, waitBetweenTries=10000, onError="continueErrorOutput"),
        http("Importjob anlegen", [720, 0], "POST", "/imports",
             multipart=[{"parameterType": "formData", "name": "supplier_id", "value": "={{ $('Quellen (autorisierte Feeds)').item.json.supplier_id }}"},
                        {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "file"}]),
        node("Bereits importiert / Zuordnung offen", "n8n-nodes-base.noOp", 1, [960, 120], {}),
        stop("Quelle nicht verfügbar", [720, 200], "Feed nicht erreichbar – Quelle als nicht verfügbar markieren"),
    ]
    conns = link(("Alle 6 Stunden", "Quellen (autorisierte Feeds)"), ("Quellen (autorisierte Feeds)", "Feed herunterladen"),
                 ("Feed herunterladen", "Importjob anlegen", 0), ("Feed herunterladen", "Quelle nicht verfügbar", 1),
                 ("Importjob anlegen", "Bereits importiert / Zuordnung offen", 1))
    return workflow("SBC 5 – Online Opportunity Hunter (Feeds)", nodes, conns,
                    "Lädt autorisierte Lieferanten-Feeds und übergibt sie der Import-Pipeline. Volle Quellenverwaltung folgt in Phase 5.")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    wfs = {
        "01-supplier-import.json": wf_supplier_import(),
        "02-product-matching.json": wf_simple(
            "SBC 2 – Product Matching", "*/30 * * * *",
            [("Neue Angebote analysieren", "POST", "/pipeline/analyze-pending", '={"limit": 2000, "use_keepa": false}'),
             ("Status abrufen", "GET", "/pipeline/status", None)],
            "Plant Matching (Level 1–2 ohne Keepa) für neue Angebote ein. Unsichere Treffer landen im Matching-Review."),
        "03-marketplace-analysis.json": wf_simple(
            "SBC 3 – Marketplace Analysis", "15 */6 * * *",
            [("Keepa-Analyse neuer Angebote", "POST", "/pipeline/analyze-pending", '={"limit": 1000, "use_keepa": true}'),
             ("Veraltete Marktdaten erneuern", "POST", "/pipeline/refresh-market-data?max_age_hours=24", None)],
            "Löst Keepa-Abfragen aus. Token-Limits und Tagesbudgets setzt das Backend durch (429 → spätere Wiederholung)."),
        "04-profit-analysis.json": wf_simple(
            "SBC 4 – Profit Analysis", "0 5 * * *",
            [("Kalkulation & Score neu berechnen", "POST", "/pipeline/recalculate", None)],
            "Neuberechnung aller Kalkulationen und Scores (ohne externe API-Kosten)."),
        "05-online-opportunity-hunter.json": wf_hunter(),
        "06-daily-sourcing-report.json": wf_report(),
    }
    for fname, wf in wfs.items():
        (OUT / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        print("geschrieben:", fname)


if __name__ == "__main__":
    main()
