"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Input, Label, PageHeader, Table, Td, Th } from "@/components/ui";
import { api, useApi } from "@/lib/api";

type Settings = Record<string, Record<string, any>>;
type Fee = Record<string, any> & { id: string; marketplace: string; country: string; channel: string; is_assumption: boolean };

const SECTIONS: { key: string; title: string; fields: [string, string][] }[] = [
  { key: "thresholds", title: "Grenzwerte & Ausschlusskriterien", fields: [
    ["min_margin", "Mindest-DB-Marge (0.12 = 12 %)"], ["min_contribution", "Mindest-DB je Stück (€)"],
    ["max_test_purchase", "Maximaler Testeinkauf (€)"], ["min_match_confidence", "Mindest-Matching-Konfidenz (0.95)"]] },
  { key: "score_weights", title: "Score-Gewichtung (Summe = 1)", fields: [
    ["profit", "Deckungsbeitrag & Marge"], ["demand", "Nachfrage"], ["stability_competition", "Preisstabilität & Wettbewerb"],
    ["turnover", "Kapitalumschlag"], ["sourcing_risk", "Beschaffungsrisiko"]] },
  { key: "purchasing", title: "Einkauf", fields: [
    ["test_order_months", "Testbestellung: Monatsabsatz × (Monate)"], ["reorder_cover_months", "Nachbestellung: Reichweite (Monate)"],
    ["own_share_of_market", "Realistischer eigener Marktanteil (0.15)"], ["budget_total", "Einkaufsbudget gesamt (€)"]] },
  { key: "analysis", title: "Analyse-Pipeline", fields: [
    ["ai_min_potential_contribution", "KI-Prüfung erst ab erwartetem DB (€)"], ["default_channel", "Standardkanal (FBA/FBM)"]] },
];

export default function SettingsPage() {
  const { data, error, reload } = useApi<Settings>("settings");
  const fees = useApi<Fee[]>("fee-profiles");
  const [draft, setDraft] = useState<Settings>({});
  const [budgets, setBudgets] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [feeDraft, setFeeDraft] = useState<Record<string, Fee>>({});

  useEffect(() => {
    if (data) {
      setDraft(structuredClone(data));
      setBudgets(JSON.stringify(data.api_budgets, null, 2));
    }
  }, [data]);

  async function save(key: string, value: Record<string, unknown>) {
    setMsg(null);
    try {
      await api(`settings/${key}`, { method: "PUT", body: JSON.stringify({ value }) });
      setMsg("Gespeichert. Neue Werte gelten für folgende Analysen; bestehende Opportunities über „Detailanalyse“ neu bewerten.");
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveFee(f: Fee) {
    const body = { ...f };
    for (const k of ["id", "org_id", "created_at", "updated_at"]) delete body[k];
    for (const [k, v] of Object.entries(body)) if (v === "") body[k] = null;
    try {
      await api(`fee-profiles/${f.id}`, { method: "PUT", body: JSON.stringify({ ...body, is_assumption: false }) });
      setMsg(`Gebührenprofil ${f.marketplace} ${f.country} ${f.channel} gespeichert (als geprüft markiert)`);
      fees.reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  const FEE_COLS = ["vat_rate", "referral_rate", "referral_min", "fixed_fee", "fulfillment_fee", "storage_cost_per_month", "packaging_cost",
    "payment_fee_rate", "return_rate", "return_cost", "ad_cost_rate", "other_cost"];

  return (
    <div>
      <PageHeader title="Einstellungen" description="Alle Grenzwerte sind veränderbare Startparameter." />
      <ErrorBox error={error} />
      {msg && <div className="mb-3 rounded-md bg-accent-soft px-3 py-2 text-sm">{msg}</div>}
      <div className="grid gap-4 lg:grid-cols-2">
        {SECTIONS.map((s) => (
          <Card key={s.key}>
            <CardHeader title={s.title} actions={<Button size="sm" onClick={() => save(s.key, draft[s.key])}>Speichern</Button>} />
            <div className="grid gap-3 p-4 md:grid-cols-2">
              {s.fields.map(([k, label]) => (
                <div key={k}><Label>{label}</Label>
                  <Input value={draft[s.key]?.[k] ?? ""} onChange={(e) => setDraft({ ...draft, [s.key]: { ...draft[s.key], [k]: e.target.value } })} />
                </div>
              ))}
              {s.key === "thresholds" && (
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!draft.thresholds?.require_stock}
                  onChange={(e) => setDraft({ ...draft, thresholds: { ...draft.thresholds, require_stock: e.target.checked } })} />Bestand 0 ausschließen</label>
              )}
              {s.key === "purchasing" && <p className="text-xs text-muted md:col-span-2">Automatische Bestellungen sind nicht implementiert und bleiben deaktiviert.</p>}
            </div>
          </Card>
        ))}
        <Card>
          <CardHeader title="API-Budgets (Tag/Monat)" subtitle="keepa: Tokens · anthropic: EUR (field=cost)" actions={
            <Button size="sm" onClick={() => { try { save("api_budgets", JSON.parse(budgets)); } catch { setMsg("Ungültiges JSON"); } }}>Speichern</Button>} />
          <textarea className="h-40 w-full rounded-b-lg border-0 bg-surface p-3 font-mono text-xs outline-none" value={budgets} onChange={(e) => setBudgets(e.target.value)} />
        </Card>
        <Card>
          <CardHeader title="Tagesbericht" actions={<Button size="sm" onClick={() => save("reports", { email_enabled: !draft.reports?.email_enabled })}>
            {draft.reports?.email_enabled ? "E-Mail deaktivieren" : "E-Mail aktivieren"}</Button>} />
          <p className="p-4 text-sm text-muted">Der Bericht wird täglich erzeugt. E-Mail-Versand nur bei aktivierter Option und gesetzten Variablen SMTP_URL und REPORT_RECIPIENTS.
            Status: <strong>{draft.reports?.email_enabled ? "aktiv" : "aus"}</strong></p>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Gebühren-, Steuer- und Versandprofile" subtitle="Raten als Dezimalzahl (0.15 = 15 %). Beträge netto in EUR. Startwerte sind Annahmen und müssen geprüft werden." />
        <Table>
          <thead><tr><Th>Profil</Th>{FEE_COLS.map((c) => <Th key={c}>{c}</Th>)}<Th /></tr></thead>
          <tbody>
            {fees.data?.map((f) => {
              const d = feeDraft[f.id] ?? f;
              return (
                <tr key={f.id}>
                  <Td className="whitespace-nowrap">{f.marketplace} {f.country} {f.channel} {f.is_assumption && <Badge tone="warn">Annahme</Badge>}</Td>
                  {FEE_COLS.map((c) => (
                    <Td key={c}><Input className="w-20 px-1.5 text-xs" value={d[c] ?? ""} onChange={(e) => setFeeDraft({ ...feeDraft, [f.id]: { ...d, [c]: e.target.value } })} /></Td>
                  ))}
                  <Td><Button size="sm" variant="outline" onClick={() => saveFee(d)}>Speichern</Button></Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
