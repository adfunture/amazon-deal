"use client";

import Link from "next/link";
import { useState } from "react";
import { Button, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { money } from "@/lib/format";

type Supplier = {
  id: string; code: string; name: string; country: string | null; currency: string; lead_time_days: number | null;
  min_order_value: string | null; rating: string | null; current_offers: number; is_active: boolean;
};

const EMPTY = { code: "", name: "", country: "DE", currency: "EUR", incoterm: "", lead_time_days: "", min_order_value: "",
  shipping_cost_per_unit: "", payment_terms: "", prices_include_vat: false };

export default function SuppliersPage() {
  const { data, error, reload } = useApi<Supplier[]>("suppliers");
  const [form, setForm] = useState(EMPTY);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api("suppliers", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          country: form.country || null, incoterm: form.incoterm || null, payment_terms: form.payment_terms || null,
          lead_time_days: form.lead_time_days ? Number(form.lead_time_days) : null,
          min_order_value: form.min_order_value || null, shipping_cost_per_unit: form.shipping_cost_per_unit || null,
        }),
      });
      setForm(EMPTY);
      setOpen(false);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  const f = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <div>
      <PageHeader title="Lieferanten" actions={<Button onClick={() => setOpen(!open)}>{open ? "Abbrechen" : "Lieferant anlegen"}</Button>} />
      {open && (
        <Card className="mb-4 p-4">
          <ErrorBox error={err} />
          <form onSubmit={create} className="grid gap-3 md:grid-cols-4">
            <div><Label>Lieferanten-ID *</Label><Input required value={form.code} onChange={f("code")} /></div>
            <div className="md:col-span-2"><Label>Name *</Label><Input required value={form.name} onChange={f("name")} /></div>
            <div><Label>Land</Label><Input maxLength={2} value={form.country} onChange={f("country")} /></div>
            <div><Label>Währung</Label><Select className="w-full" value={form.currency} onChange={f("currency")}>{["EUR", "USD", "GBP", "PLN", "CHF", "CZK"].map((c) => <option key={c}>{c}</option>)}</Select></div>
            <div><Label>Incoterm</Label><Input value={form.incoterm} onChange={f("incoterm")} placeholder="EXW, DAP, DDP…" /></div>
            <div><Label hint="Tage">Lieferzeit</Label><Input type="number" value={form.lead_time_days} onChange={f("lead_time_days")} /></div>
            <div><Label hint="€">Mindestbestellwert</Label><Input type="number" step="0.01" value={form.min_order_value} onChange={f("min_order_value")} /></div>
            <div><Label hint="€ je Stück, netto">Versandkosten</Label><Input type="number" step="0.01" value={form.shipping_cost_per_unit} onChange={f("shipping_cost_per_unit")} /></div>
            <div className="md:col-span-2"><Label>Zahlungsbedingungen</Label><Input value={form.payment_terms} onChange={f("payment_terms")} /></div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.prices_include_vat} onChange={f("prices_include_vat")} />Preise inkl. USt</label>
            <div className="md:col-span-4"><Button type="submit">Speichern</Button></div>
          </form>
        </Card>
      )}
      <ErrorBox error={error} />
      <Card>
        <CardHeader title={`${data?.length ?? 0} Lieferanten`} />
        {!data?.length ? <Empty>Noch keine Lieferanten</Empty> : (
          <Table>
            <thead><tr><Th>ID</Th><Th>Name</Th><Th>Land</Th><Th>Währung</Th><Th className="text-right">Lieferzeit</Th><Th className="text-right">MBW</Th><Th className="text-right">Aktuelle Angebote</Th></tr></thead>
            <tbody>
              {data.map((s) => (
                <tr key={s.id}>
                  <Td>{s.code}</Td>
                  <Td><Link className="text-accent hover:underline" href={`/suppliers/${s.id}`}>{s.name}</Link></Td>
                  <Td>{s.country ?? "–"}</Td><Td>{s.currency}</Td>
                  <Td className="num text-right">{s.lead_time_days ? `${s.lead_time_days} T` : "–"}</Td>
                  <Td className="num text-right">{money(s.min_order_value)}</Td>
                  <Td className="num text-right">{s.current_offers}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
