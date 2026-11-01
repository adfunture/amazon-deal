"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, Select } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { CONFLICT_LABELS, DECISION_LABELS, DECISION_TONE, MATCH_LABELS, ean, money, percent } from "@/lib/format";

type Match = {
  id: string; classification: string; score: string; confidence: string; calibrated: boolean; units_required: number | null;
  stage: string; reasoning: string | null; matched_attributes: Record<string, unknown>; mismatched_attributes: Record<string, unknown>;
  review_status: string; engine_version: string; model_version: string | null;
  decision_status: string; score_breakdown: { signal: string; points: number; detail: string }[]; identity_conflicts: string[];
  supplier_offer: Record<string, any>; listing: Record<string, any>;
};

function Side({ title, d, kind }: { title: string; d: Record<string, any>; kind: "offer" | "listing" }) {
  const img = (d.image_urls ?? [])[0];
  const rows: [string, React.ReactNode][] = kind === "offer"
    ? [["Titel", d.title], ["Marke", d.brand], ["EAN", ean(d.gtin)], ["MPN", d.mpn], ["SKU", d.supplier_sku], ["Stück je Einheit", d.unit_quantity],
       ["Zustand", d.condition], ["EK netto", money(d.unit_price_net, d.currency)], ["Bestand", d.stock_qty ?? "?"], ["Lieferant", d.supplier_name],
       ["Beschreibung", d.description]]
    : [["Titel", d.title], ["Marke", d.brand], ["EAN", (d.gtins ?? []).map(ean).join(", ") || "–"], ["MPN / Modell", `${d.mpn ?? "–"} / ${d.model ?? "–"}`],
       ["ID", `${d.marketplace} ${d.country} ${d.external_id}`], ["Stück je Angebot", d.number_of_items ?? d.package_quantity ?? "–"],
       ["Preis (brutto)", money(d.buybox_price ?? d.new_price)], ["Kategorie", d.category], ["Quelle", `${d.source}${d.is_test_data ? " (TESTDATEN)" : ""}`]];
  return (
    <Card className="flex-1">
      <CardHeader title={title} />
      <div className="p-4">
        <div className="mb-3 flex h-40 items-center justify-center rounded bg-white">
          {img ? <img src={img} alt="" className="max-h-40 object-contain" /> : <span className="text-xs text-muted">kein Bild</span>}
        </div>
        <dl className="space-y-1 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[130px_1fr] gap-2"><dt className="text-muted">{k}</dt><dd className="break-words">{v ?? "–"}</dd></div>
          ))}
        </dl>
      </div>
    </Card>
  );
}

function Review() {
  const focus = useSearchParams().get("focus");
  const [status, setStatus] = useState("MANUAL_REVIEW");
  const [cls, setCls] = useState("");
  const { data, error, reload } = useApi<{ total: number; items: Match[] }>(`matching/queue${qs({ ...(status.includes("_") ? { decision: status } : { status }), classification: cls, limit: 50 })}`, [status, cls]);
  const [idx, setIdx] = useState(0);
  const [units, setUnits] = useState("");
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const focused = useApi<Match>(focus ? `matching/${focus}` : null);
  const m = focus && focused.data ? focused.data : data?.items[idx];

  useEffect(() => { setUnits(m?.units_required ? String(m.units_required) : ""); setComment(""); }, [m?.id]);

  async function decide(action: string) {
    if (!m) return;
    try {
      const r = await api<{ opportunity_id: string | null }>(`matching/${m.id}/review`, {
        method: "POST",
        body: JSON.stringify({ action, units_required: units ? Number(units) : undefined, comment: comment || undefined }),
      });
      setMsg(`Gespeichert (${action})${r.opportunity_id ? " – Opportunity neu bewertet" : ""}`);
      if (focus) window.history.replaceState(null, "", "/matching");
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      <PageHeader title="Product Matching Review" description="Links Lieferant, rechts Marktplatz. Jede Entscheidung wird als Feedback für die Kalibrierung gespeichert."
        actions={<>
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setIdx(0); }}>
            <option value="MANUAL_REVIEW">Manuelle Prüfung</option><option value="VERIFIED_MATCH">Verifiziert</option>
            <option value="NO_MATCH">Kein Match</option><option value="deferred">zurückgestellt</option>
          </Select>
          <Select value={cls} onChange={(e) => { setCls(e.target.value); setIdx(0); }}>
            <option value="">alle Klassen</option>{Object.entries(MATCH_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </>} />
      <ErrorBox error={error} />
      {msg && <div className="mb-3 rounded-md bg-accent-soft px-3 py-2 text-sm">{msg}</div>}
      {!m ? <Card><Empty>Keine Matches in dieser Ansicht.</Empty></Card> : (
        <>
          <Card className="mb-3 p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={DECISION_TONE[m.decision_status] ?? "neutral"}>{DECISION_LABELS[m.decision_status] ?? m.decision_status}</Badge>
              <Badge tone="info">{MATCH_LABELS[m.classification]}</Badge>
              <span>Score <strong>{Math.round(Number(m.score) * 100)}</strong>/100 · Konfidenz {percent(m.confidence)}{m.calibrated ? "" : " (unkalibriert)"}</span>
              {m.identity_conflicts?.map((c) => <Badge key={c} tone="bad">Konflikt: {CONFLICT_LABELS[c.replace("mapping:", "")] ?? c}</Badge>)}
              <span className="text-muted">· Stufe {m.stage} · {m.engine_version}{m.model_version ? ` · ${m.model_version}` : ""}</span>
              {m.units_required && m.units_required > 1 && <Badge tone="warn">benötigt {m.units_required} Lieferanteneinheiten</Badge>}
              <div className="flex-1" />
              {!focus && data && <span className="text-xs text-muted">{idx + 1} / {data.items.length} (gesamt {data.total})</span>}
            </div>
            {m.reasoning && <p className="mt-2 text-sm">{m.reasoning}</p>}
            {m.score_breakdown?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1 text-xs">
                {m.score_breakdown.map((b, i) => (
                  <span key={i} className={`rounded px-1.5 py-0.5 ${b.points >= 0 ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"}`}>
                    {b.points > 0 ? "+" : ""}{b.points} {b.detail}
                  </span>
                ))}
              </div>
            )}
            <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
              <div><span className="font-medium text-success">Übereinstimmend:</span> {Object.entries(m.matched_attributes).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(" · ") || "–"}</div>
              <div><span className="font-medium text-danger">Abweichend:</span> {Object.entries(m.mismatched_attributes).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(" · ") || "–"}</div>
            </div>
          </Card>
          <div className="flex flex-col gap-3 lg:flex-row">
            <Side title="Lieferantenprodukt" d={m.supplier_offer} kind="offer" />
            <Side title="Marktplatzprodukt" d={m.listing} kind="listing" />
          </div>
          <Card className="mt-3 p-3">
            <div className="flex flex-wrap items-end gap-2">
              <div><Label hint="Lieferanteneinheiten je Angebot">Menge</Label><Input type="number" min={1} className="w-28" value={units} onChange={(e) => setUnits(e.target.value)} /></div>
              <div className="min-w-64 flex-1"><Label>Kommentar</Label><Input value={comment} onChange={(e) => setComment(e.target.value)} /></div>
              <Button variant="success" onClick={() => decide("confirm")}>Match bestätigen</Button>
              <Button variant="danger" onClick={() => decide("reject")}>Ablehnen</Button>
              <Button variant="outline" onClick={() => decide("bundle")}>Als Bundle</Button>
              <Button variant="outline" onClick={() => decide("variant")}>Variante</Button>
              <Button variant="outline" onClick={() => decide("defer")}>Zurückstellen</Button>
              {!focus && <>
                <Button variant="ghost" disabled={idx === 0} onClick={() => setIdx(idx - 1)}>←</Button>
                <Button variant="ghost" disabled={!data || idx >= data.items.length - 1} onClick={() => setIdx(idx + 1)}>→</Button>
              </>}
            </div>
            <p className="mt-2 text-xs text-muted">Bundles, Multipacks und Varianten werden nie automatisch eingekauft – erst nach Bestätigung hier.</p>
          </Card>
        </>
      )}
    </div>
  );
}

export default function MatchingPage() {
  return <Suspense><Review /></Suspense>;
}
