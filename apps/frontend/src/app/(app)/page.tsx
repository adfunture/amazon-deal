"use client";

import Link from "next/link";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardHeader, Empty, ErrorBox, PageHeader, Stat, Table, Td, Th } from "@/components/ui";
import { useApi } from "@/lib/api";
import { money, number, percent } from "@/lib/format";

type Dashboard = {
  analyzed_offers: number;
  analyzed_with_market_data: number;
  profitable_candidates: number;
  avg_margin: string | null;
  potential_contribution_per_unit_sum: string | null;
  top_opportunities: { id: string; title: string; score: string; contribution: string; margin: string }[];
  score_distribution: { bucket: number; count: number }[];
  api_usage_7d: { provider: string; units: string; cost_eur: string | null }[];
  imports: Record<string, number>;
  matching_pending: number;
  failed_jobs_7d: number;
  new_offers_7d: number;
  test_data_present: boolean;
};

export default function DashboardPage() {
  const { data, error } = useApi<Dashboard>("dashboard");
  const { data: integ } = useApi<Record<string, string>>("integrations");
  return (
    <div>
      <PageHeader title="Dashboard" description="Live-Daten (Testdaten sind hier ausgeblendet)" />
      <ErrorBox error={error} />
      {integ && (integ.keepa === "not_configured" || integ.anthropic === "not_configured") && (
        <div className="mb-4 rounded-md border border-orange-200 bg-orange-50 px-3 py-2 text-sm text-warning">
          Nicht konfiguriert: {[integ.keepa === "not_configured" && "Keepa", integ.anthropic === "not_configured" && "Anthropic (KI-Matching)"].filter(Boolean).join(", ")}.
          Ohne Keepa werden keine Amazon-Marktdaten abgerufen.
        </div>
      )}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Aktuelle Lieferantenangebote" value={number(data.analyzed_offers)} hint={`${number(data.analyzed_with_market_data)} mit Marktdaten`} />
            <Stat label="Profitable Kandidaten" value={number(data.profitable_candidates)} hint="nach Ausschlusskriterien" />
            <Stat label="Ø DB-Marge (Kandidaten)" value={percent(data.avg_margin)} />
            <Stat label="Σ DB je Stück (Kandidaten)" value={money(data.potential_contribution_per_unit_sum)} hint="Summe 1 Stück je Kandidat" />
            <Stat label="Matching offen" value={<Link className="text-accent" href="/matching">{data.matching_pending}</Link>} />
            <Stat label="Neue Angebote (7 Tage)" value={number(data.new_offers_7d)} />
            <Stat label="Fehlgeschlagene Jobs (7 Tage)" value={number(data.failed_jobs_7d)} />
            <Stat label="Importe" value={Object.entries(data.imports).map(([k, v]) => `${v} ${k}`).join(" · ") || "–"} />
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader title="Top Opportunities" />
              {data.top_opportunities.length === 0 ? (
                <Empty>Noch keine profitablen Kandidaten. Lade eine Großhandelsliste hoch.</Empty>
              ) : (
                <Table>
                  <thead><tr><Th>Produkt</Th><Th className="text-right">Score</Th><Th className="text-right">DB</Th><Th className="text-right">Marge</Th></tr></thead>
                  <tbody>
                    {data.top_opportunities.map((o) => (
                      <tr key={o.id}>
                        <Td><Link className="text-accent hover:underline" href={`/opportunities/${o.id}`}>{o.title}</Link></Td>
                        <Td className="num text-right">{number(o.score)}</Td>
                        <Td className="num text-right">{money(o.contribution)}</Td>
                        <Td className="num text-right">{percent(o.margin)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
            <Card>
              <CardHeader title="Score-Verteilung" />
              <div className="h-56 p-3">
                <ResponsiveContainer>
                  <BarChart data={data.score_distribution}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="bucket" tickFormatter={(b) => `${b}`} fontSize={11} />
                    <YAxis allowDecimals={false} fontSize={11} width={30} />
                    <Tooltip formatter={(v) => [v, "Anzahl"]} labelFormatter={(b) => `Score ${b}–${Number(b) + 9}`} />
                    <Bar dataKey="count" fill="var(--accent)" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <CardHeader title="API-Verbrauch (7 Tage)" />
              <div className="p-3 text-sm">
                {data.api_usage_7d.length === 0 ? <span className="text-muted">Kein Verbrauch</span> : data.api_usage_7d.map((u) => (
                  <div key={u.provider} className="flex justify-between"><span>{u.provider}</span><span className="num">{number(u.units)} {u.cost_eur ? `· ${money(u.cost_eur)}` : ""}</span></div>
                ))}
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
