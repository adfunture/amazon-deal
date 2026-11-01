"use client";

import { useState } from "react";
import { Button, Card, CardHeader, Empty, ErrorBox, Label, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, ean, money, number } from "@/lib/format";

type Listing = Record<string, any>;

export default function AmazonScanner() {
  const [codes, setCodes] = useState("");
  const [country, setCountry] = useState("DE");
  const [res, setRes] = useState<Record<string, Listing[]> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const list = codes.split(/[\s,;]+/).filter(Boolean).slice(0, 20);
      setRes(await api("amazon/lookup", { method: "POST", body: JSON.stringify({ codes: list, country }) }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Amazon Scanner" description="Keepa-Abfrage für bis zu 20 EANs. Kostet 1 Keepa-Token je Produkt; frische Ergebnisse werden aus dem Cache geliefert." />
      <Card className="mb-4 p-4">
        <ErrorBox error={err} />
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-80 flex-1"><Label>EANs (durch Komma, Leerzeichen oder Zeilen getrennt)</Label>
            <textarea className="h-20 w-full rounded-md border border-border bg-surface p-2 text-sm" value={codes} onChange={(e) => setCodes(e.target.value)} /></div>
          <div><Label>Marktplatz</Label>
            <Select value={country} onChange={(e) => setCountry(e.target.value)}>
              {["DE", "FR", "IT", "ES"].map((c) => <option key={c}>{c}</option>)}
              <option value="NL" disabled>NL (Keepa nicht verfügbar)</option><option value="BE" disabled>BE (Keepa nicht verfügbar)</option>
              <option value="PL" disabled>PL (Keepa nicht verfügbar)</option>
            </Select></div>
          <Button onClick={run} disabled={busy || !codes.trim()}>{busy ? "Frage ab…" : "Abfragen"}</Button>
        </div>
        <p className="mt-2 text-xs text-muted">Amazon NL, BE und PL werden von Keepa nicht unterstützt; diese Marktplätze folgen über die SP-API (Phase 4).</p>
      </Card>
      {res && (
        <Card>
          <CardHeader title="Ergebnisse" />
          {Object.keys(res).length === 0 ? <Empty>Keine gültigen EANs</Empty> : (
            <Table>
              <thead><tr><Th>EAN</Th><Th>ASIN</Th><Th>Titel</Th><Th className="text-right">Buy Box</Th><Th className="text-right">Ø 90 T</Th><Th className="text-right">Rank</Th><Th className="text-right">Drops 30</Th><Th className="text-right">Anbieter</Th><Th>Stand</Th></tr></thead>
              <tbody>
                {Object.entries(res).flatMap(([code, ls]) => ls.length === 0
                  ? [<tr key={code}><Td className="num">{ean(code)}</Td><Td colSpan={8} className="text-muted">kein Amazon-Listing gefunden</Td></tr>]
                  : ls.map((l) => (
                    <tr key={code + l.external_id}>
                      <Td className="num">{ean(code)}</Td><Td>{l.external_id}</Td><Td className="max-w-md">{l.title}</Td>
                      <Td className="num text-right">{money(l.buybox_price)}</Td><Td className="num text-right">{money(l.avg90_price)}</Td>
                      <Td className="num text-right">{number(l.sales_rank)}</Td><Td className="num text-right">{l.sales_rank_drops30 ?? "–"}</Td>
                      <Td className="num text-right">{l.offer_count_new ?? "–"}</Td><Td className="text-xs">{dateTime(l.fetched_at)}</Td>
                    </tr>
                  )))}
              </tbody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
