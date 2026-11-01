"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Button, Card, CardHeader, ErrorBox, PageHeader, Table, Td, TestDataBanner, Th } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { MATCH_LABELS, dateTime, ean, money, number, percent } from "@/lib/format";

type Calc = { scenario: string; inputs: Record<string, string | null>; outputs: Record<string, unknown>; missing: string[]; is_complete: boolean };
type Detail = {
  id: string; title: string; channel: string; status: string; score: string | null; is_test_data: boolean; is_excluded: boolean;
  exclusion_reasons: string[]; missing_costs: string[];
  score_breakdown: { components: Record<string, number | null>; weights: Record<string, string>; coverage: number;
    demand?: { method: string; source: string; market_units_month: string | null; own_units_month: string | null; confidence: string; notes: string[] };
    test_quantity?: number };
  supplier_offer: Record<string, any>; supplier: Record<string, any> | null; listing: Record<string, any>;
  matching: Record<string, any> | null; calculations: Record<string, Calc>;
  price_history: { metric: string; value: string; at: string; source: string }[];
  alternative_suppliers: { supplier: string; price: string; currency: string; stock: number | null }[];
  data_sources: Record<string, string | null>;
};

const SCENARIOS = ["current", "ref_30d", "ref_90d", "conservative", "drop_5", "drop_10"];
const COST_LABELS: Record<string, string> = {
  wareneinsatz: "Wareneinsatz (landed)", provision: "Marktplatzprovision", fulfillment_versand: "FBA / Versand",
  zahlungsgebuehr: "Zahlungsgebühr", werbung: "Werbung", lager: "Lager", verpackung: "Verpackung",
  konfektionierung: "Konfektionierung", retouren: "Retourenrisiko", fixgebuehr: "Fixgebühr", sonstiges: "Sonstiges",
};

export default function OpportunityDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: d, error, reload } = useApi<Detail>(`opportunities/${id}`);
  const [msg, setMsg] = useState<string | null>(null);

  const chart = useMemo(() => {
    if (!d) return [];
    const byTime = new Map<string, Record<string, number | string>>();
    for (const p of d.price_history.filter((x) => x.metric !== "sales_rank")) {
      const k = p.at.slice(0, 16);
      byTime.set(k, { ...(byTime.get(k) ?? { at: k }), [p.metric]: Number(p.value) });
    }
    return [...byTime.values()];
  }, [d]);

  async function action(kind: "purchase" | "analyze" | "ignore") {
    try {
      if (kind === "purchase") {
        const r = await api<{ created: string[]; skipped: { reason: string }[] }>("opportunities/purchase-list", { method: "POST", body: JSON.stringify({ ids: [id] }) });
        setMsg(r.created.length ? "Einkaufsvorschlag erstellt (Status: vorgeschlagen)" : `Nicht möglich: ${r.skipped[0]?.reason}`);
      } else if (kind === "analyze") {
        await api("opportunities/analyze", { method: "POST", body: JSON.stringify({ ids: [id] }) });
        setMsg("Detailanalyse eingeplant – Ergebnis erscheint nach Abschluss des Worker-Jobs");
      } else {
        await api("opportunities/status", { method: "POST", body: JSON.stringify({ ids: [id], status: "ignored" }) });
        setMsg("Ignoriert");
      }
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <ErrorBox error={error} />;
  if (!d) return <div className="text-sm text-muted">Lade…</div>;
  const o = d.supplier_offer, l = d.listing, m = d.matching;
  const cur = d.calculations.current;
  const out = (cur?.outputs ?? {}) as Record<string, any>;

  return (
    <div>
      {d.is_test_data && <TestDataBanner />}
      <PageHeader
        title={d.title}
        description={`${l.marketplace} ${l.country} · ${d.channel} · ASIN/ID ${l.external_id} · EAN ${ean(o.gtin)}`}
        actions={<>
          <Link href={`/matching?offer=${o.id}`}><Button variant="outline">Manuell prüfen</Button></Link>
          <Button variant="outline" onClick={() => action("analyze")}>Detailanalyse</Button>
          <Button variant="outline" onClick={() => action("ignore")}>Ignorieren</Button>
          <Button onClick={() => action("purchase")}>Zur Einkaufsliste</Button>
        </>}
      />
      {msg && <div className="mb-3 rounded-md bg-accent-soft px-3 py-2 text-sm">{msg}</div>}
      {d.is_excluded && (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-danger">
          Ausgeschlossen: {d.exclusion_reasons.join(" · ")}
        </div>
      )}
      {d.missing_costs.length > 0 && (
        <div className="mb-3 rounded-md border border-orange-200 bg-orange-50 px-3 py-2 text-sm text-warning">
          Fehlende Kostenangaben (Ergebnis unvollständig): {d.missing_costs.join(", ")}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Lieferantenangebot" subtitle={d.supplier?.name} />
          <div className="space-y-1 p-4 text-sm">
            <Row k="Titel" v={o.title} /><Row k="Marke" v={o.brand} /><Row k="SKU" v={o.supplier_sku} /><Row k="MPN" v={o.mpn} />
            <Row k="EK netto" v={`${money(o.unit_price_net, o.currency)}${o.price_basis_qty > 1 ? ` (Preis je ${o.price_basis_qty})` : ""}`} />
            <Row k="Stück je Einheit" v={o.unit_quantity} /><Row k="Bestand" v={o.stock_qty ?? "unbekannt"} /><Row k="MOQ" v={o.moq ?? "–"} />
            <Row k="Lieferzeit" v={o.lead_time_days ? `${o.lead_time_days} Tage` : "–"} /><Row k="Zustand" v={o.condition} />
            <Row k="Zuletzt gesehen" v={dateTime(o.last_seen_at)} />
            {d.alternative_suppliers.length > 0 && (
              <div className="pt-2 text-xs"><div className="font-medium">Weitere Lieferanten (gleiche EAN)</div>
                {d.alternative_suppliers.map((a) => <div key={a.supplier}>{a.supplier}: {money(a.price, a.currency)} · Bestand {a.stock ?? "?"}</div>)}
              </div>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Marktplatzangebot" subtitle={`Quelle: ${l.source} · ${dateTime(l.fetched_at)}`} />
          <div className="space-y-1 p-4 text-sm">
            {l.image_urls?.[0] && <img src={l.image_urls[0]} alt="" className="mb-2 h-32 w-full rounded object-contain bg-white" />}
            <Row k="Titel" v={l.title} /><Row k="Marke" v={l.brand} /><Row k="Buy Box (brutto)" v={money(l.buybox_price)} />
            <Row k="Ø 30 Tage" v={money(l.avg30_price)} /><Row k="Ø 90 Tage" v={money(l.avg90_price)} /><Row k="Min. 90 Tage" v={money(l.min90_price)} />
            <Row k="Sales Rank" v={number(l.sales_rank)} /><Row k="Rank-Drops 30/90" v={`${l.sales_rank_drops30 ?? "–"} / ${l.sales_rank_drops90 ?? "–"}`} />
            <Row k="Neu-Anbieter" v={l.offer_count_new ?? "–"} /><Row k="Amazon verkauft" v={l.amazon_is_seller === null ? "unbekannt" : l.amazon_is_seller ? "ja" : "nein"} />
            <Row k="Stück je Angebot" v={l.number_of_items ?? l.package_quantity ?? "–"} />
          </div>
        </Card>
        <Card>
          <CardHeader title="Matching & Score" />
          <div className="space-y-1 p-4 text-sm">
            {m && <>
              <div className="flex flex-wrap gap-1">
                <Badge tone="info">{MATCH_LABELS[m.classification]}</Badge>
                <Badge tone={m.review_status === "confirmed" ? "good" : m.review_status === "rejected" ? "bad" : "warn"}>{m.review_status}</Badge>
                {m.units_required > 1 && <Badge tone="warn">{m.units_required} Lieferanteneinheiten je Angebot</Badge>}
              </div>
              <Row k="Score / Konfidenz" v={`${number(Number(m.score) * 100)} / ${percent(m.confidence)}${m.calibrated ? "" : " (unkalibriert)"}`} />
              <Row k="Stufe" v={`${m.stage} · ${m.engine_version}${m.model_version ? ` · ${m.model_version}` : ""}`} />
              <p className="text-xs text-muted">{m.reasoning}</p>
            </>}
            <div className="pt-2 font-medium">Opportunity-Score: {number(d.score)} <span className="text-xs font-normal text-muted">(Datenabdeckung {Math.round((d.score_breakdown.coverage ?? 0) * 100)} %)</span></div>
            {Object.entries(d.score_breakdown.components ?? {}).map(([k, v]) => (
              <Row key={k} k={`${k} (${Math.round(Number(d.score_breakdown.weights?.[k] ?? 0) * 100)} %)`} v={v === null ? "keine Daten" : number(v)} />
            ))}
            {d.score_breakdown.demand && (
              <div className="pt-2 text-xs text-muted">
                Absatz: Markt {d.score_breakdown.demand.market_units_month ?? "?"}/Monat, eigener Anteil {d.score_breakdown.demand.own_units_month ?? "?"}/Monat
                · Methode {d.score_breakdown.demand.method} ({d.score_breakdown.demand.source}) · Konfidenz {percent(d.score_breakdown.demand.confidence)}
                <br />{d.score_breakdown.demand.notes.join(" · ")}
                {d.score_breakdown.test_quantity ? <><br />Vorgeschlagene Testmenge: {d.score_breakdown.test_quantity} Angebote</> : null}
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Rentabilitätsrechnung (aktueller Preis)" subtitle={(cur?.inputs as Record<string, unknown> | undefined)?.fee_profile_is_assumption ? "Gebührenprofil = Startannahme" : undefined} />
          {cur && out.contribution !== undefined ? (
            <Table><tbody>
              <tr><Td>Verkaufspreis brutto</Td><Td className="num text-right">{money(cur.inputs.sell_price_gross)}</Td></tr>
              <tr><Td>− Umsatzsteuer ({percent(cur.inputs.vat_rate)}) – kein Kostenpunkt</Td><Td className="num text-right">{money(out.vat_amount)}</Td></tr>
              <tr><Td className="font-medium">= Nettoerlös</Td><Td className="num text-right font-medium">{money(out.net_revenue)}</Td></tr>
              {Object.entries(out.cost_breakdown ?? {}).filter(([, v]) => Number(v) !== 0).map(([k, v]) => (
                <tr key={k}><Td className="text-muted">− {COST_LABELS[k] ?? k}</Td><Td className="num text-right">{money(v as string)}</Td></tr>
              ))}
              <tr><Td className="font-semibold">= Deckungsbeitrag je Stück</Td><Td className={`num text-right font-semibold ${Number(out.contribution) < 0 ? "text-danger" : "text-success"}`}>{money(out.contribution)}</Td></tr>
              <tr><Td>DB-Marge / ROI</Td><Td className="num text-right">{percent(out.contribution_margin)} / {percent(out.roi)}</Td></tr>
              <tr><Td>Break-even-VK brutto</Td><Td className="num text-right">{money(out.break_even_price_gross)}</Td></tr>
              <tr><Td>Max. EK netto (Mindestmarge &amp; -DB)</Td><Td className="num text-right">{money(out.max_buy_price_net)}</Td></tr>
              {out.capital && <>
                <tr><Td>Kapitalbedarf Testmenge ({out.capital.quantity} Stk)</Td><Td className="num text-right">{money(out.capital.capital_required)}</Td></tr>
                <tr><Td>DB je 1.000 € Kapital und Monat</Td><Td className="num text-right">{money(out.capital.contribution_per_1000_per_month)}</Td></tr>
              </>}
            </tbody></Table>
          ) : (
            <div className="p-4 text-sm text-warning">Keine Berechnung möglich – fehlend: {cur?.missing?.join(", ") || "Daten"}</div>
          )}
        </Card>
        <Card>
          <CardHeader title="Szenarioanalyse" subtitle="Nur Szenarien mit vorhandenen Preisdaten" />
          <Table>
            <thead><tr><Th>Szenario</Th><Th className="text-right">VK brutto</Th><Th className="text-right">DB</Th><Th className="text-right">Marge</Th></tr></thead>
            <tbody>
              {SCENARIOS.map((s) => {
                const c = d.calculations[s];
                return (
                  <tr key={s}>
                    <Td>{c?.inputs?.scenario_label ?? s}</Td>
                    {c ? <>
                      <Td className="num text-right">{money(c.inputs.sell_price_gross)}</Td>
                      <Td className={`num text-right ${Number(c.outputs.contribution) < 0 ? "text-danger" : ""}`}>{money(c.outputs.contribution as string)}</Td>
                      <Td className="num text-right">{percent(c.outputs.contribution_margin as string)}</Td>
                    </> : <Td colSpan={3} className="text-right text-muted">keine historischen Daten</Td>}
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <CardHeader title="Preisverlauf (Messpunkte)" />
          <div className="h-56 p-3">
            {chart.length < 2 ? <div className="text-sm text-muted">Noch zu wenige Messpunkte</div> : (
              <ResponsiveContainer>
                <LineChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="at" fontSize={10} tickFormatter={(v) => String(v).slice(5, 10)} />
                  <YAxis fontSize={11} width={45} />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="buybox" name="Buy Box" stroke="var(--accent)" dot={false} />
                  <Line dataKey="new" name="Neu" stroke="#64748b" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>
      <p className="mt-4 text-xs text-muted">
        Datenquellen: Marktplatz {d.data_sources.listing_source} (Stand {dateTime(d.data_sources.listing_fetched_at)}), Lieferantenangebot zuletzt importiert {dateTime(d.data_sources.offer_last_seen)}.
        Gebühren aus Gebührenprofil{cur?.inputs?.fee_profile_is_assumption ? " (Startannahme – in Einstellungen prüfen)" : ""}.
      </p>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted">{k}</span>
      <span className="num text-right">{v ?? "–"}</span>
    </div>
  );
}
