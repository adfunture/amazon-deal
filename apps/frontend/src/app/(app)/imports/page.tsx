"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Upload } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Label, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTime, number } from "@/lib/format";

type Job = {
  id: string; file_name: string; supplier_name: string; status: string; total_rows: number | null; processed_rows: number;
  error_rows: number; progress: number | null; created_at: string; stats: Record<string, number>;
};

const STATUS_TONE: Record<string, "neutral" | "good" | "warn" | "bad" | "info"> = {
  uploaded: "neutral", mapping_review: "warn", queued: "info", processing: "info", completed: "good",
  completed_with_errors: "warn", failed: "bad",
};

export default function ImportsPage() {
  const router = useRouter();
  const suppliers = useApi<{ id: string; name: string }[]>("suppliers");
  const { data: jobs, error, reload } = useApi<Job[]>("imports");
  const [supplierId, setSupplierId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function upload(force = false) {
    if (!file || !supplierId) return;
    setBusy(true);
    setErr(null);
    const fd = new FormData();
    fd.set("supplier_id", supplierId);
    fd.set("file", file);
    if (force) fd.set("force", "true");
    try {
      const job = await api<{ id: string }>("imports", { method: "POST", body: fd });
      router.push(`/imports/${job.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && !force) {
        if (confirm("Diese Datei wurde für den Lieferanten bereits importiert. Trotzdem erneut importieren?")) return upload(true);
      }
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Großhandelslisten" description="XLSX, XLS, CSV, TSV, JSON, XML und ZIP · max. 100 MB · Verarbeitung im Hintergrund" />
      <Card className="mb-4 p-4">
        <ErrorBox error={err} />
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label>Lieferant</Label>
            <Select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className="min-w-56">
              <option value="">wählen…</option>
              {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </div>
          <div>
            <Label>Datei</Label>
            <input type="file" accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt,.json,.xml,.zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
          </div>
          <Button onClick={() => upload()} disabled={!file || !supplierId || busy}><Upload className="h-4 w-4" />{busy ? "Lade hoch…" : "Hochladen & analysieren"}</Button>
          {suppliers.data?.length === 0 && <Link href="/suppliers" className="text-sm text-accent">Zuerst einen Lieferanten anlegen</Link>}
        </div>
        <p className="mt-2 text-xs text-muted">PDF-Preislisten (KI-Extraktion) folgen in Phase 3. E-Mail-Anhänge und SFTP laufen über n8n bzw. den Feed-Import.</p>
      </Card>
      <ErrorBox error={error} />
      <Card>
        <CardHeader title="Importe" actions={<Button size="sm" variant="ghost" onClick={reload}>Aktualisieren</Button>} />
        {!jobs?.length ? <Empty>Noch keine Importe</Empty> : (
          <Table>
            <thead><tr><Th>Datei</Th><Th>Lieferant</Th><Th>Status</Th><Th className="text-right">Zeilen</Th><Th className="text-right">Fehler</Th><Th className="text-right">Neu / Preisänd.</Th><Th>Erstellt</Th></tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <Td><Link className="text-accent hover:underline" href={`/imports/${j.id}`}>{j.file_name}</Link></Td>
                  <Td>{j.supplier_name}</Td>
                  <Td><Badge tone={STATUS_TONE[j.status]}>{j.status}</Badge>{j.progress !== null && j.status === "processing" && <span className="ml-2 text-xs">{Math.round(j.progress * 100)} %</span>}</Td>
                  <Td className="num text-right">{number(j.processed_rows)}{j.total_rows ? ` / ${number(j.total_rows)}` : ""}</Td>
                  <Td className="num text-right">{j.error_rows}</Td>
                  <Td className="num text-right">{j.stats?.new_offers ?? "–"} / {j.stats?.price_changes ?? "–"}</Td>
                  <Td className="text-xs">{dateTime(j.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
