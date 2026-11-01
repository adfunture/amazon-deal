"use client";

import { use, useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Input, Label, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTime, number } from "@/lib/format";

type Job = {
  id: string; file_name: string; status: string; sheet_name: string | null; header_row: number; error: string | null;
  detected_columns: { header: string; samples: unknown[] }[]; column_mapping: Record<string, string>;
  mapping_source: Record<string, { method: string; confidence: number }>; options: Record<string, unknown>;
  total_rows: number | null; processed_rows: number; error_rows: number; progress: number | null; stats: Record<string, number>;
  row_counts?: Record<string, number>; created_at: string; finished_at: string | null;
};
type Row = { row_number: number; status: string; errors: string[]; warnings: string[]; raw: Record<string, unknown> };

const TONE: Record<string, "neutral" | "good" | "warn" | "bad" | "info"> = {
  uploaded: "neutral", mapping_review: "warn", queued: "info", processing: "info", completed: "good", completed_with_errors: "warn", failed: "bad",
};

export default function ImportDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: job, error, reload } = useApi<Job>(`imports/${id}`);
  const fields = useApi<{ key: string; label: string }[]>("imports/fields");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [opts, setOpts] = useState({ prices_are_gross: false, currency: "", price_tier_index: "", replaces_previous: true, sku_is_mpn: false });
  const [profileName, setProfileName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [rowFilter, setRowFilter] = useState<"error" | "duplicate" | "ok">("error");
  const rows = useApi<Row[]>(`imports/${id}/rows?status=${rowFilter}&limit=100`, [rowFilter, job?.status]);

  useEffect(() => {
    if (job) setMapping(job.column_mapping ?? {});
  }, [job?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [job?.status, reload]); // eslint-disable-line react-hooks/exhaustive-deps

  async function start() {
    setErr(null);
    try {
      await api(`imports/${id}/mapping`, {
        method: "PUT",
        body: JSON.stringify({
          mapping, save_profile: true, profile_name: profileName || undefined, start: true,
          options: {
            prices_are_gross: opts.prices_are_gross, replaces_previous: opts.replaces_previous, sku_is_mpn: opts.sku_is_mpn,
            ...(opts.currency ? { currency: opts.currency } : {}),
            ...(opts.price_tier_index !== "" ? { price_tier_index: Number(opts.price_tier_index) } : {}),
          },
        }),
      });
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function resume() {
    await api(`imports/${id}/resume`, { method: "POST" });
    reload();
  }

  if (error) return <ErrorBox error={error} />;
  if (!job) return <div className="text-sm text-muted">Lade…</div>;
  const editable = ["mapping_review", "uploaded", "failed"].includes(job.status);
  const tierCols = Object.entries(mapping).filter(([, f]) => f === "tier_price").map(([h]) => h);

  return (
    <div>
      <PageHeader title={job.file_name} description={`Blatt: ${job.sheet_name ?? "–"} · Kopfzeile: Zeile ${job.header_row + 1} · hochgeladen ${dateTime(job.created_at)}`}
        actions={<Badge tone={TONE[job.status]}>{job.status}</Badge>} />
      <ErrorBox error={err ?? job.error} />
      {["queued", "processing", "completed", "completed_with_errors", "failed"].includes(job.status) && (
        <Card className="mb-4 p-4">
          <div className="mb-2 flex justify-between text-sm">
            <span>Fortschritt: {number(job.processed_rows)}{job.total_rows ? ` / ${number(job.total_rows)}` : ""} Zeilen</span>
            <span>Fehler: {job.error_rows} · Neu: {job.stats?.new_offers ?? 0} · Preisänderungen: {job.stats?.price_changes ?? 0} · Dubletten: {job.stats?.duplicates ?? 0}
              {job.stats?.offers_no_longer_listed ? ` · nicht mehr gelistet: ${job.stats.offers_no_longer_listed}` : ""}</span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-bg"><div className="h-full bg-accent transition-all" style={{ width: `${Math.round((job.progress ?? (job.status.startsWith("completed") ? 1 : 0)) * 100)}%` }} /></div>
          {job.status === "failed" && <Button className="mt-3" size="sm" onClick={resume}>Import fortsetzen</Button>}
        </Card>
      )}

      <Card className="mb-4">
        <CardHeader title="Spaltenzuordnung" subtitle={editable ? "Automatisch erkannt – bitte prüfen und korrigieren. Wird als Lieferantenprofil gespeichert." : "Verwendete Zuordnung"} />
        <Table>
          <thead><tr><Th>Spalte in Datei</Th><Th>Beispielwerte</Th><Th>Zielfeld</Th><Th>Erkennung</Th></tr></thead>
          <tbody>
            {job.detected_columns.map((c) => {
              const src = job.mapping_source?.[c.header];
              return (
                <tr key={c.header}>
                  <Td className="font-medium">{c.header}</Td>
                  <Td className="max-w-md truncate text-xs text-muted">{c.samples.filter((s) => s !== null && s !== "").slice(0, 3).map(String).join(" | ")}</Td>
                  <Td>
                    <Select disabled={!editable} value={mapping[c.header] ?? ""} onChange={(e) => setMapping({ ...mapping, [c.header]: e.target.value })}>
                      <option value="">– ignorieren –</option>
                      {fields.data?.filter((f) => f.key !== "ignore").map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                    </Select>
                  </Td>
                  <Td className="text-xs">{src ? `${src.method} (${Math.round(src.confidence * 100)} %)` : "–"}</Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
        {editable && (
          <div className="grid gap-3 border-t border-border p-4 md:grid-cols-6">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={opts.prices_are_gross} onChange={(e) => setOpts({ ...opts, prices_are_gross: e.target.checked })} />Preise sind brutto</label>
            <label className="flex items-center gap-2 text-sm" title="Für Markenteile-Listen ohne eigene MPN-Spalte (z. B. MANN-FILTER)"><input type="checkbox" checked={opts.sku_is_mpn} onChange={(e) => setOpts({ ...opts, sku_is_mpn: e.target.checked })} />Artikelnr. = Herstellernr.</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={opts.replaces_previous} onChange={(e) => setOpts({ ...opts, replaces_previous: e.target.checked })} />Ersetzt vorherige Liste</label>
            <div><Label hint="leer = Lieferantenwährung">Währung</Label><Input value={opts.currency} maxLength={3} onChange={(e) => setOpts({ ...opts, currency: e.target.value.toUpperCase() })} /></div>
            {tierCols.length > 0 && (
              <div><Label>Basis-Staffelpreis</Label>
                <Select className="w-full" value={opts.price_tier_index} onChange={(e) => setOpts({ ...opts, price_tier_index: e.target.value })}>
                  <option value="">erste verfügbare</option>
                  {tierCols.map((h, i) => <option key={h} value={i}>{h}</option>)}
                </Select>
              </div>
            )}
            <div><Label>Profilname</Label><Input value={profileName} onChange={(e) => setProfileName(e.target.value)} placeholder="optional" /></div>
            <div className="md:col-span-6"><Button onClick={start}>Zuordnung speichern &amp; Import starten</Button></div>
          </div>
        )}
      </Card>

      {!editable && (
        <Card>
          <CardHeader title="Zeilen" actions={
            <Select value={rowFilter} onChange={(e) => setRowFilter(e.target.value as typeof rowFilter)}>
              <option value="error">Fehler ({job.row_counts?.error ?? 0})</option>
              <option value="duplicate">Dubletten ({job.row_counts?.duplicate ?? 0})</option>
              <option value="ok">OK ({job.row_counts?.ok ?? 0})</option>
            </Select>
          } />
          <Table>
            <thead><tr><Th>Zeile</Th><Th>Meldungen</Th><Th>Rohdaten</Th></tr></thead>
            <tbody>
              {rows.data?.map((r) => (
                <tr key={r.row_number}>
                  <Td className="num">{r.row_number + job.header_row + 2}</Td>
                  <Td className="text-xs">{r.errors.map((e) => <div key={e} className="text-danger">{e}</div>)}{r.warnings.map((w) => <div key={w} className="text-warning">{w}</div>)}</Td>
                  <Td className="max-w-xl truncate text-xs text-muted">{Object.entries(r.raw).filter(([, v]) => v !== null).map(([k, v]) => `${k}: ${v}`).join(" · ")}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
