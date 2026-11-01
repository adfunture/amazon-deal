"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { dateTime, money } from "@/lib/format";

type Rec = {
  id: string; kind: string; quantity: number; unit_cost_net: string; total_cost_net: string; currency: string; status: string;
  rationale: { notes: string[]; expected_total_contribution?: string; units_per_sale?: number }; created_at: string;
  opportunity: { id: string; title: string; supplier: string; gtin: string | null; asin: string | null; contribution: string | null; is_test_data: boolean };
};

const TONE: Record<string, "neutral" | "good" | "bad" | "info"> = { proposed: "neutral", approved: "good", rejected: "bad", ordered: "info" };

export default function PurchasingPage() {
  const [status, setStatus] = useState("");
  const { data, error, reload } = useApi<Rec[]>(`opportunities/purchase-list/items${qs({ status })}`, [status]);
  const [qty, setQty] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  async function decide(id: string, s: string) {
    setErr(null);
    try {
      await api(`purchase-recommendations/${id}`, { method: "POST", body: JSON.stringify({ status: s, quantity: qty[id] ? Number(qty[id]) : undefined }) });
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const bySupplier = new Map<string, Rec[]>();
  for (const r of data ?? []) bySupplier.set(r.opportunity.supplier, [...(bySupplier.get(r.opportunity.supplier) ?? []), r]);

  return (
    <div>
      <PageHeader title="Einkaufsliste" description="Vorschläge für Test- und Nachbestellungen. Es wird nichts automatisch bestellt – Freigabe und Bestellung erfolgen manuell."
        actions={<Select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">alle</option><option value="proposed">vorgeschlagen</option><option value="approved">freigegeben</option>
          <option value="ordered">bestellt</option><option value="rejected">abgelehnt</option>
        </Select>} />
      <ErrorBox error={error ?? err} />
      {!data?.length ? <Card><Empty>Keine Einkaufsvorschläge. Wähle in den Opportunities „Zur Einkaufsliste“.</Empty></Card> : (
        [...bySupplier.entries()].map(([supplier, recs]) => {
          const total = recs.filter((r) => r.status !== "rejected").reduce((s, r) => s + Number(r.total_cost_net), 0);
          return (
            <Card key={supplier} className="mb-4">
              <CardHeader title={supplier} subtitle={`Kapitalbedarf (ohne abgelehnte): ${money(total)}`} />
              <Table>
                <thead><tr><Th>Produkt</Th><Th>Art</Th><Th className="text-right">Menge</Th><Th className="text-right">EK netto</Th><Th className="text-right">Summe</Th><Th className="text-right">erw. DB</Th><Th>Hinweise</Th><Th>Status</Th><Th /></tr></thead>
                <tbody>
                  {recs.map((r) => (
                    <tr key={r.id}>
                      <Td><Link className="text-accent hover:underline" href={`/opportunities/${r.opportunity.id}`}>{r.opportunity.title}</Link>
                        {r.opportunity.is_test_data && <Badge tone="test" className="ml-1">TESTDATEN</Badge>}
                        <div className="text-xs text-muted">{dateTime(r.created_at)}</div></Td>
                      <Td>{r.kind === "test" ? "Testbestellung" : "Nachbestellung"}</Td>
                      <Td className="num text-right">
                        {r.status === "proposed" ? <Input className="w-20 text-right" type="number" placeholder={String(r.quantity)} value={qty[r.id] ?? ""} onChange={(e) => setQty({ ...qty, [r.id]: e.target.value })} /> : r.quantity}
                      </Td>
                      <Td className="num text-right">{money(r.unit_cost_net, r.currency)}</Td>
                      <Td className="num text-right">{money(r.total_cost_net, r.currency)}</Td>
                      <Td className="num text-right">{money(r.rationale.expected_total_contribution)}</Td>
                      <Td className="text-xs text-muted">{r.rationale.notes.join(" · ") || "–"}</Td>
                      <Td><Badge tone={TONE[r.status]}>{r.status}</Badge></Td>
                      <Td className="whitespace-nowrap">
                        {r.status === "proposed" && <>
                          <Button size="sm" variant="success" onClick={() => decide(r.id, "approved")}>Freigeben</Button>{" "}
                          <Button size="sm" variant="outline" onClick={() => decide(r.id, "rejected")}>Ablehnen</Button>
                        </>}
                        {r.status === "approved" && <Button size="sm" variant="outline" onClick={() => decide(r.id, "ordered")}>Als bestellt markieren</Button>}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          );
        })
      )}
    </div>
  );
}
