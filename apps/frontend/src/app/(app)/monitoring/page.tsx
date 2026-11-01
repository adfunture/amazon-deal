"use client";

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, CardHeader, Empty, ErrorBox, PageHeader, Stat, Table, Td, Th } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateTime, money, number } from "@/lib/format";

type Mon = {
  per_day: { date: string; provider: string; units: string; cost_eur: string | null; requests: number; items: number }[];
  errors: Record<string, number>;
  jobs: { kind: string; status: string; count: number; avg_ms: number | null }[];
  recent_failed_jobs: { id: string; kind: string; status: string; last_error: string; created_at: string }[];
  keepa_tokens: { tokens_left: number; refill_rate: number; refill_in_ms: number; timestamp_ms: number } | null;
  budgets: Record<string, { daily?: number; monthly?: number; field?: string }>;
  per_product: { analyzed_offers: number; keepa_tokens_per_offer: string | null; ai_cost_eur_per_offer: string | null };
  railway_resources: string;
};

export default function MonitoringPage() {
  const { data, error } = useApi<Mon>("monitoring?days=30");
  const integ = useApi<Record<string, string>>("integrations");
  const chart = useMemo(() => {
    const m = new Map<string, Record<string, number | string>>();
    for (const r of data?.per_day ?? []) m.set(r.date, { ...(m.get(r.date) ?? { date: r.date }), [r.provider]: Number(r.units) });
    return [...m.values()];
  }, [data]);
  const sum = (p: string, f: "units" | "cost_eur") => (data?.per_day ?? []).filter((r) => r.provider === p).reduce((s, r) => s + Number(r[f] ?? 0), 0);

  return (
    <div>
      <PageHeader title="API Monitoring" description="Verbrauch, Kosten, Fehler und Hintergrundjobs der letzten 30 Tage" />
      <ErrorBox error={error} />
      {integ.data && (
        <Card className="mb-4 p-3">
          <div className="flex flex-wrap gap-2 text-sm">
            {Object.entries(integ.data).map(([k, v]) => (
              <Badge key={k} tone={v === "configured" || v === "ok" || v === "s3" ? "good" : v.startsWith("not") ? "warn" : "neutral"}>{k}: {v}</Badge>
            ))}
          </div>
        </Card>
      )}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Keepa-Tokens (30 T)" value={number(sum("keepa", "units"))} hint={data.budgets.keepa ? `Budget ${data.budgets.keepa.daily ?? "–"}/Tag` : undefined} />
            <Stat label="Keepa-Tokens verfügbar" value={data.keepa_tokens ? number(data.keepa_tokens.tokens_left) : "–"}
              hint={data.keepa_tokens ? `+${data.keepa_tokens.refill_rate}/min · Stand ${dateTime(new Date(data.keepa_tokens.timestamp_ms).toISOString())}` : "noch keine Abfrage"} />
            <Stat label="KI-Kosten (30 T)" value={money(sum("anthropic", "cost_eur"))} hint={data.budgets.anthropic ? `Budget ${data.budgets.anthropic.daily ?? "–"} €/Tag` : undefined} />
            <Stat label="Tokens je Angebot" value={number(data.per_product.keepa_tokens_per_offer)} hint={`${data.per_product.analyzed_offers} analysierte Angebote`} />
            <Stat label="KI-Kosten je Angebot" value={money(data.per_product.ai_cost_eur_per_offer)} />
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Verbrauch je Tag (Einheiten)" />
              <div className="h-64 p-3">
                {chart.length === 0 ? <Empty>Noch kein Verbrauch</Empty> : (
                  <ResponsiveContainer>
                    <BarChart data={chart}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="date" fontSize={10} /><YAxis fontSize={11} width={45} /><Tooltip /><Legend />
                      <Bar dataKey="keepa" stackId="a" fill="var(--accent)" />
                      <Bar dataKey="anthropic" stackId="b" fill="#7c3aed" />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </Card>
            <Card>
              <CardHeader title="Hintergrundjobs" subtitle={`API-Fehler: ${Object.entries(data.errors).map(([p, n]) => `${p} ${n}`).join(", ") || "keine"}`} />
              <Table>
                <thead><tr><Th>Job</Th><Th>Status</Th><Th className="text-right">Anzahl</Th><Th className="text-right">Ø Laufzeit</Th></tr></thead>
                <tbody>{data.jobs.map((j) => (
                  <tr key={j.kind + j.status}><Td>{j.kind}</Td><Td><Badge tone={j.status === "succeeded" ? "good" : j.status === "running" ? "info" : "bad"}>{j.status}</Badge></Td>
                    <Td className="num text-right">{j.count}</Td><Td className="num text-right">{j.avg_ms ? `${(j.avg_ms / 1000).toFixed(1)} s` : "–"}</Td></tr>
                ))}</tbody>
              </Table>
            </Card>
          </div>
          <Card className="mt-4">
            <CardHeader title="Fehlgeschlagene Jobs (Dead-Letter-Sicht)" />
            {!data.recent_failed_jobs.length ? <Empty>Keine</Empty> : (
              <Table><tbody>{data.recent_failed_jobs.map((j) => (
                <tr key={j.id}><Td className="whitespace-nowrap text-xs">{dateTime(j.created_at)}</Td><Td>{j.kind}</Td><Td className="text-xs text-danger">{j.last_error}</Td></tr>
              ))}</tbody></Table>
            )}
          </Card>
          <p className="mt-3 text-xs text-muted">{data.railway_resources}</p>
        </>
      )}
    </div>
  );
}
