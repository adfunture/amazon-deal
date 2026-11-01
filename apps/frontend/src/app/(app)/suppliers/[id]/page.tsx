"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, Table, Td, Th } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTime, ean, money } from "@/lib/format";

type Supplier = Record<string, any> & {
  imports: { id: string; file_name: string; status: string; created_at: string; processed_rows: number; error_rows: number }[];
  profiles: { id: string; name: string; column_mapping: Record<string, string>; updated_at: string }[];
};
type Change = { offer_id: string; sku: string | null; gtin: string | null; title: string | null; previous: string; current: string; changed_at: string };

const EDITABLE = [
  ["name", "Name"], ["country", "Land"], ["currency", "Währung"], ["payment_terms", "Zahlungsbedingungen"], ["incoterm", "Incoterm"],
  ["shipping_cost_per_order", "Versand je Bestellung (€)"], ["shipping_cost_per_unit", "Versand je Stück (€)"],
  ["free_shipping_from", "Frei Haus ab (€)"], ["min_order_value", "Mindestbestellwert (€)"], ["default_moq", "Standard-MOQ"],
  ["lead_time_days", "Lieferzeit (Tage)"], ["import_duty_rate", "Zollsatz (z. B. 0.042)"], ["rating", "Bewertung 0–5"],
] as const;

export default function SupplierDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, reload } = useApi<Supplier>(`suppliers/${id}`);
  const changes = useApi<Change[]>(`suppliers/${id}/price-history`);
  const [form, setForm] = useState<Record<string, any> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (data) setForm(Object.fromEntries(Object.entries(data).filter(([k]) => !["imports", "profiles", "feeds"].includes(k))));
  }, [data]);

  async function save() {
    if (!form) return;
    const body: Record<string, unknown> = { ...form };
    for (const k of ["id", "org_id", "created_at", "updated_at"]) delete body[k];
    for (const [k] of EDITABLE) if (body[k] === "") body[k] = null;
    try {
      await api(`suppliers/${id}`, { method: "PUT", body: JSON.stringify(body) });
      setMsg("Gespeichert");
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <ErrorBox error={error} />;
  if (!data || !form) return <div className="text-sm text-muted">Lade…</div>;
  return (
    <div>
      <PageHeader title={data.name} description={`Lieferanten-ID ${data.code}`} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Profil" />
          <div className="grid gap-3 p-4 md:grid-cols-3">
            {EDITABLE.map(([k, label]) => (
              <div key={k}><Label>{label}</Label><Input value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></div>
            ))}
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form.prices_include_vat} onChange={(e) => setForm({ ...form, prices_include_vat: e.target.checked })} />Preise inkl. USt</label>
            <div className="md:col-span-3"><Label>Notizen</Label><Input value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
            <div className="flex items-center gap-3 md:col-span-3"><Button onClick={save}>Speichern</Button>{msg && <span className="text-sm">{msg}</span>}</div>
          </div>
        </Card>
        <Card>
          <CardHeader title="Importprofile" />
          {!data.profiles.length ? <Empty>Noch kein Profil – entsteht beim ersten Import</Empty> : data.profiles.map((p) => (
            <div key={p.id} className="border-b border-border px-4 py-2 text-sm">
              <div className="font-medium">{p.name}</div>
              <div className="text-xs text-muted">{Object.entries(p.column_mapping).map(([h, f]) => `${h} → ${f}`).join(" · ")}</div>
            </div>
          ))}
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Preislisten-Historie" />
          <Table>
            <thead><tr><Th>Datei</Th><Th>Status</Th><Th className="text-right">Zeilen</Th><Th>Datum</Th></tr></thead>
            <tbody>{data.imports.map((j) => (
              <tr key={j.id}>
                <Td><Link className="text-accent hover:underline" href={`/imports/${j.id}`}>{j.file_name}</Link></Td>
                <Td><Badge>{j.status}</Badge></Td><Td className="num text-right">{j.processed_rows} ({j.error_rows} Fehler)</Td><Td className="text-xs">{dateTime(j.created_at)}</Td>
              </tr>
            ))}</tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader title="Preisänderungen zwischen Importen" />
          {!changes.data?.length ? <Empty>Keine Preisänderungen</Empty> : (
            <Table>
              <thead><tr><Th>Artikel</Th><Th>EAN</Th><Th className="text-right">Vorher</Th><Th className="text-right">Jetzt</Th><Th className="text-right">Δ</Th></tr></thead>
              <tbody>{changes.data.map((c) => {
                const d = Number(c.current) / Number(c.previous) - 1;
                return (
                  <tr key={c.offer_id}>
                    <Td className="text-xs">{c.title ?? c.sku}</Td><Td className="num text-xs">{ean(c.gtin)}</Td>
                    <Td className="num text-right">{money(c.previous, data.currency)}</Td><Td className="num text-right">{money(c.current, data.currency)}</Td>
                    <Td className={`num text-right ${d < 0 ? "text-success" : "text-danger"}`}>{(d * 100).toFixed(1)} %</Td>
                  </tr>
                );
              })}</tbody>
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
