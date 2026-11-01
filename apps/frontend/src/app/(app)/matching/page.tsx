"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ExternalLink } from "lucide-react";
import { Gallery } from "@/components/gallery";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, Select } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { CONFLICT_LABELS, DECISION_LABELS, DECISION_TONE, MATCH_LABELS, dateTime, ean, money, number } from "@/lib/format";

type Candidate = {
  id: string; classification: string; decision_status: string; review_status: string; score: string; confidence: string;
  calibrated: boolean; units_required: number | null; stage: string; reasoning: string | null;
  score_breakdown: { signal: string; points: number; detail: string }[]; identity_conflicts: string[];
  bulk_blocker: string | null; listing: Record<string, any>;
};
type OfferItem = { offer: Record<string, any>; candidates: Candidate[] };

const AMAZON_TLD: Record<string, string> = { DE: "de", FR: "fr", IT: "it", ES: "es", NL: "nl", BE: "com.be", PL: "pl" };
const PAGE = 20;

function Field({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 text-sm">
      <dt className="text-muted">{k}</dt>
      <dd className="break-words">{v ?? "–"}</dd>
    </div>
  );
}

function CandidateCard({ c, onChoose, busy }: { c: Candidate; busy: boolean;
  onChoose: (c: Candidate, action: "confirm" | "variant" | "bundle", units: number | null) => void }) {
  const l = c.listing;
  const [units, setUnits] = useState(c.units_required ? String(c.units_required) : "");
  const link = l.marketplace === "amazon" ? `https://www.amazon.${AMAZON_TLD[l.country] ?? "de"}/dp/${l.external_id}` : null;
  const open = c.decision_status === "MANUAL_REVIEW";
  return (
    <Card className={c.decision_status === "VERIFIED_MATCH" ? "border-green-300" : c.decision_status === "NO_MATCH" ? "opacity-60" : ""}>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2 text-sm">
        <Badge tone={DECISION_TONE[c.decision_status] ?? "neutral"}>{DECISION_LABELS[c.decision_status]}</Badge>
        <Badge tone="info">{MATCH_LABELS[c.classification]}</Badge>
        <span className="font-semibold">{Math.round(Number(c.score) * 100)}/100</span>
        {c.units_required && c.units_required > 1 && <Badge tone="warn">×{c.units_required} je Angebot</Badge>}
        {c.identity_conflicts.map((x) => <Badge key={x} tone="bad">{CONFLICT_LABELS[x.replace("mapping:", "")] ?? x}</Badge>)}
        <span className="ml-auto text-xs text-muted">{c.stage}</span>
      </div>
      <div className="grid gap-3 p-3 md:grid-cols-[200px_1fr]">
        <Gallery urls={l.image_urls ?? []} height="h-36" />
        <div>
          <div className="mb-1 font-medium">
            {link ? <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">{l.title} <ExternalLink className="h-3 w-3" /></a> : l.title}
          </div>
          <dl className="space-y-0.5">
            <Field k="ASIN / Land" v={`${l.external_id} · ${l.country}${l.is_test_data ? " · TESTDATEN" : ""}`} />
            <Field k="Marke" v={l.brand} />
            <Field k="EAN" v={(l.gtins ?? []).map(ean).join(", ") || "–"} />
            <Field k="MPN / Modell" v={`${l.mpn ?? "–"} / ${l.model ?? "–"}`} />
            <Field k="Stück je Angebot" v={l.number_of_items ?? l.package_quantity ?? "–"} />
            <Field k="Preis brutto" v={`${money(l.buybox_price ?? l.new_price)}${l.buybox_price ? " (Buy Box)" : l.new_price ? " (Neu)" : ""}`} />
            <Field k="Rank · Anbieter" v={`${number(l.sales_rank)} · ${l.offer_count_new ?? "–"}`} />
            <Field k="Datenstand" v={`${l.source} · ${dateTime(l.fetched_at)}`} />
          </dl>
        </div>
      </div>
      {c.score_breakdown.length > 0 && (
        <div className="flex flex-wrap gap-1 px-3 pb-2 text-xs">
          {c.score_breakdown.map((b, i) => (
            <span key={i} className={`rounded px-1.5 py-0.5 ${b.points >= 0 ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"}`}>
              {b.points > 0 ? "+" : ""}{b.points} {b.detail}
            </span>
          ))}
        </div>
      )}
      {c.reasoning && <p className="px-3 pb-2 text-xs text-muted">{c.reasoning}</p>}
      {open && (
        <div className="flex flex-wrap items-end gap-2 border-t border-border px-3 py-2">
          <div><Label hint="Lieferanteneinheiten je Angebot">Menge</Label>
            <Input type="number" min={1} className="w-24" value={units} onChange={(e) => setUnits(e.target.value)} /></div>
          <Button variant="success" disabled={busy} onClick={() => onChoose(c, "confirm", units ? Number(units) : null)}>Dieser passt</Button>
          <Button variant="outline" disabled={busy} onClick={() => onChoose(c, "variant", units ? Number(units) : null)}>Variante</Button>
          <Button variant="outline" disabled={busy} onClick={() => onChoose(c, "bundle", units ? Number(units) : null)}>Bundle</Button>
        </div>
      )}
    </Card>
  );
}

function Review() {
  const params = useSearchParams();
  const focusOffer = params.get("offer");
  const [decision, setDecision] = useState("MANUAL_REVIEW");
  const [supplierId, setSupplierId] = useState("");
  const [offset, setOffset] = useState(0);
  const [idx, setIdx] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asin, setAsin] = useState("");
  const suppliers = useApi<{ id: string; name: string }[]>("suppliers");
  const { data, error, reload } = useApi<{ total: number; items: OfferItem[] }>(
    `matching/offers${qs({ decision, supplier_id: supplierId, offset, limit: PAGE })}`, [decision, supplierId, offset]);
  const focused = useApi<OfferItem>(focusOffer ? `matching/offers/${focusOffer}` : null);
  const [override, setOverride] = useState<OfferItem | null>(null);

  const item: OfferItem | undefined = override ?? (focusOffer && focused.data ? focused.data : data?.items[Math.min(idx, (data?.items.length ?? 1) - 1)]);
  useEffect(() => { setOverride(null); setAsin(""); }, [idx, offset, decision, supplierId]);

  const bulkIds = useMemo(() => (data?.items ?? []).flatMap((it) => it.candidates.filter((c) => !c.bulk_blocker).map((c) => c.id)), [data]);

  async function run(fn: () => Promise<OfferItem>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fn();
      setMsg(ok);
      if (res.candidates.some((c) => c.decision_status === "MANUAL_REVIEW")) {
        setOverride(res); // noch offene Kandidaten (z. B. nach „andere ASIN“) → Artikel bleibt sichtbar
      } else {
        setOverride(null);
        if (focusOffer) window.history.replaceState(null, "", "/matching");
      }
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const offerId = item?.offer.id;
  const choose = (c: Candidate, action: string, units: number | null) => run(
    () => api<OfferItem>(`matching/offers/${offerId}/choose`, { method: "POST", body: JSON.stringify({ matching_result_id: c.id, action, units_required: units ?? undefined }) }),
    "Zuordnung gespeichert – gilt künftig für alle Lieferanten mit diesem Produkt");
  const none = () => run(() => api<OfferItem>(`matching/offers/${offerId}/none`, { method: "POST", body: "{}" }), "Alle Kandidaten abgelehnt");
  const defer = () => run(() => api<OfferItem>(`matching/offers/${offerId}/defer`, { method: "POST", body: "{}" }), "Zurückgestellt");
  const addAsin = () => run(() => api<OfferItem>(`matching/offers/${offerId}/asin`, { method: "POST", body: JSON.stringify({ asin: asin.trim().toUpperCase() }) }),
    "ASIN geladen und verglichen – bitte prüfen und bestätigen");
  async function bulk() {
    if (!bulkIds.length || !confirm(`${bulkIds.length} eindeutige exakte Treffer (1:1, ohne Konflikte) auf dieser Seite bestätigen?`)) return;
    setBusy(true);
    try {
      const r = await api<{ confirmed: string[]; skipped: unknown[] }>("matching/bulk-confirm", { method: "POST", body: JSON.stringify({ ids: bulkIds }) });
      setMsg(`${r.confirmed.length} bestätigt, ${r.skipped.length} übersprungen`);
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const o = item?.offer;
  const pos = offset + idx + 1;
  return (
    <div>
      <PageHeader title="Product Matching Review"
        description="Je Lieferantenartikel alle Amazon-Kandidaten. Jede Entscheidung wird dauerhaft gespeichert und für alle Lieferanten desselben Produkts wiederverwendet."
        actions={<>
          <Select value={decision} onChange={(e) => { setDecision(e.target.value); setOffset(0); setIdx(0); }}>
            <option value="MANUAL_REVIEW">Manuelle Prüfung</option><option value="VERIFIED_MATCH">Verifiziert</option><option value="NO_MATCH">Kein Match</option>
          </Select>
          <Select value={supplierId} onChange={(e) => { setSupplierId(e.target.value); setOffset(0); setIdx(0); }}>
            <option value="">alle Lieferanten</option>
            {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
          {decision === "MANUAL_REVIEW" && <Button variant="outline" disabled={busy || !bulkIds.length} onClick={bulk}>Massenfreigabe ({bulkIds.length})</Button>}
        </>} />
      <ErrorBox error={error} />
      {msg && <div className="mb-3 rounded-md bg-accent-soft px-3 py-2 text-sm">{msg}</div>}
      {!item || !o ? <Card><Empty>Keine Lieferantenartikel in dieser Ansicht.</Empty></Card> : (
        <>
          <div className="mb-3 flex items-center gap-2 text-sm">
            <Button size="sm" variant="ghost" disabled={busy || pos <= 1}
              onClick={() => { if (idx > 0) setIdx(idx - 1); else { setOffset(Math.max(offset - PAGE, 0)); setIdx(PAGE - 1); } }}>← Zurück</Button>
            <span>Artikel {pos} von {data?.total ?? "?"}</span>
            <Button size="sm" variant="ghost" disabled={busy || !data || pos >= data.total}
              onClick={() => { if (idx < (data?.items.length ?? 0) - 1) setIdx(idx + 1); else { setOffset(offset + PAGE); setIdx(0); } }}>Weiter →</Button>
          </div>
          <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
            <Card className="h-fit lg:sticky lg:top-4">
              <CardHeader title="Lieferantenartikel" subtitle={o.supplier_name} />
              <div className="space-y-3 p-3">
                <Gallery urls={o.image_urls ?? []} />
                <div className="font-medium">{o.title}</div>
                <dl className="space-y-0.5">
                  <Field k="Marke" v={o.brand} /><Field k="EAN" v={ean(o.gtin)} /><Field k="MPN" v={o.mpn} />
                  <Field k="Artikelnr." v={o.supplier_sku} /><Field k="Stück je Einheit" v={o.unit_quantity} />
                  <Field k="Zustand" v={o.condition} /><Field k="EK netto" v={money(o.unit_price_net, o.currency)} />
                  <Field k="Bestand / MOQ" v={`${o.stock_qty ?? "?"} / ${o.moq ?? "–"}`} />
                  {o.description && <Field k="Beschreibung" v={<span className="text-xs">{o.description}</span>} />}
                </dl>
              </div>
              <div className="space-y-2 border-t border-border p-3">
                <div className="flex flex-wrap gap-2">
                  <Button variant="danger" disabled={busy} onClick={none}>Keiner passt</Button>
                  <Button variant="outline" disabled={busy} onClick={defer}>Zurückstellen</Button>
                </div>
                <div className="flex items-end gap-2">
                  <div className="flex-1"><Label hint="kostet 1 Keepa-Token">Andere ASIN</Label>
                    <Input value={asin} maxLength={10} placeholder="B0…" onChange={(e) => setAsin(e.target.value)} /></div>
                  <Button variant="outline" disabled={busy || asin.trim().length !== 10} onClick={addAsin}>Laden</Button>
                </div>
              </div>
            </Card>
            <div className="space-y-3">
              {item.candidates.length === 0 && <Card><Empty>Keine Amazon-Kandidaten – andere ASIN eingeben oder „Keiner passt“.</Empty></Card>}
              {item.candidates.map((c) => <CandidateCard key={c.id} c={c} busy={busy} onChoose={choose} />)}
              <p className="text-xs text-muted">„Dieser passt“ lehnt die übrigen offenen Kandidaten automatisch ab. Multipacks und Bundles mit der richtigen Menge bestätigen –
                Einkäufe sind erst nach dieser Freigabe möglich.</p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function MatchingPage() {
  return <Suspense><Review /></Suspense>;
}
