"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  createColumnHelper, flexRender, getCoreRowModel, useReactTable, type RowSelectionState, type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, Download } from "lucide-react";
import { Badge, Button, Card, Empty, ErrorBox, Input, Label, Select, TestDataBanner } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { MATCH_LABELS, dateTime, ean, money, number, percent } from "@/lib/format";

export type Opp = {
  id: string; status: string; channel: string; score: string | null; is_excluded: boolean; exclusion_reasons: string[];
  is_test_data: boolean; image: string | null; title: string; brand: string | null; gtin: string | null; asin: string | null;
  supplier: string; buy_price_net: string | null; sell_price_gross: string | null; contribution: string | null;
  contribution_margin: string | null; roi: string | null; demand_score: string | null; competition_score: string | null;
  match_confidence: string | null; match_class: string | null; match_status: string | null; units_required: number | null;
  coverage: number | null; missing_costs: string[]; updated_at: string; country: string; currency: string;
};

const SORT_KEYS: Record<string, string> = {
  score: "score", contribution: "contribution", contribution_margin: "margin", roi: "roi",
  sell_price_gross: "sell", buy_price_net: "buy", demand_score: "demand", updated_at: "updated", match_confidence: "confidence",
};

const col = createColumnHelper<Opp>();

function tone(score: number) {
  return score >= 70 ? "good" : score >= 45 ? "info" : score >= 25 ? "warn" : "bad";
}

export function OpportunityTable({ bundleOnly = false }: { bundleOnly?: boolean }) {
  const [filters, setFilters] = useState({
    q: "", brand: "", category: "", marketplace: "", min_margin: "", min_contribution: "", min_sell: "", max_sell: "",
    min_buy: "", max_buy: "", min_demand: "", min_score: "", match_class: "", include_excluded: false, include_test: false,
  });
  const [applied, setApplied] = useState(filters);
  const [sorting, setSorting] = useState<SortingState>([{ id: "score", desc: true }]);
  const [page, setPage] = useState(0);
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [msg, setMsg] = useState<string | null>(null);
  const pageSize = 50;
  const suppliers = useApi<{ id: string; name: string }[]>("suppliers");
  const [supplierId, setSupplierId] = useState("");

  const params = useMemo(() => {
    const s = sorting[0];
    const p: Record<string, unknown> = {
      ...applied, supplier_id: supplierId, bundle_only: bundleOnly || undefined,
      min_margin: applied.min_margin ? Number(applied.min_margin) / 100 : "",
      sort: s ? SORT_KEYS[s.id] ?? "score" : "score", order: s?.desc === false ? "asc" : "desc",
      offset: page * pageSize, limit: pageSize,
      include_excluded: applied.include_excluded || undefined, include_test: applied.include_test || undefined,
    };
    return p;
  }, [applied, sorting, page, supplierId, bundleOnly]);

  const { data, error, reload } = useApi<{ total: number; items: Opp[] }>(`opportunities${qs(params)}`, [params]);

  const columns = useMemo(() => [
    col.display({
      id: "select",
      header: ({ table }) => <input type="checkbox" checked={table.getIsAllRowsSelected()} onChange={table.getToggleAllRowsSelectedHandler()} />,
      cell: ({ row }) => <input type="checkbox" checked={row.getIsSelected()} onChange={row.getToggleSelectedHandler()} />,
    }),
    col.accessor("image", {
      header: "", enableSorting: false,
      cell: (c) => c.getValue() ? <img src={c.getValue()!} alt="" className="h-10 w-10 rounded object-contain bg-white" /> : <div className="h-10 w-10 rounded bg-bg" />,
    }),
    col.accessor("title", {
      header: "Produkt", enableSorting: false,
      cell: ({ row: { original: o } }) => (
        <div className="min-w-[240px]">
          <Link href={`/opportunities/${o.id}`} className="font-medium text-accent hover:underline">{o.title}</Link>
          <div className="mt-0.5 flex flex-wrap gap-1 text-xs text-muted">
            <span>{o.brand ?? "–"}</span>·<span>{o.supplier}</span>·<span>{o.channel} {o.country}</span>
            {o.is_test_data && <Badge tone="test">TESTDATEN</Badge>}
            {o.is_excluded && <Badge tone="bad" title={o.exclusion_reasons.join("\n")}>ausgeschlossen</Badge>}
            {o.missing_costs.length > 0 && <Badge tone="warn" title={o.missing_costs.join(", ")}>Kosten fehlen</Badge>}
          </div>
        </div>
      ),
    }),
    col.accessor("gtin", { header: "EAN", enableSorting: false, cell: (c) => <span className="num text-xs">{ean(c.getValue())}</span> }),
    col.accessor("asin", { header: "ASIN", enableSorting: false, cell: (c) => <span className="text-xs">{c.getValue() ?? "–"}</span> }),
    col.accessor("buy_price_net", { header: "EK netto", cell: (c) => money(c.getValue(), c.row.original.currency) }),
    col.accessor("sell_price_gross", { header: "VK brutto", cell: (c) => money(c.getValue()) }),
    col.accessor("contribution", { header: "DB/Stk", cell: (c) => <span className={Number(c.getValue()) < 0 ? "text-danger" : ""}>{money(c.getValue())}</span> }),
    col.accessor("contribution_margin", { header: "Marge", cell: (c) => percent(c.getValue()) }),
    col.accessor("roi", { header: "ROI", cell: (c) => percent(c.getValue()) }),
    col.accessor("demand_score", { header: "Nachfrage", cell: (c) => number(c.getValue()) }),
    col.accessor("competition_score", { header: "Wettbewerb", enableSorting: false, cell: (c) => number(c.getValue()) }),
    col.accessor("score", {
      header: "Score",
      cell: ({ row: { original: o } }) => o.score === null ? "–" : (
        <Badge tone={tone(Number(o.score))} title={o.coverage !== null ? `Datenabdeckung ${Math.round((o.coverage ?? 0) * 100)} %` : ""}>{number(o.score)}</Badge>
      ),
    }),
    col.accessor("match_confidence", {
      header: "Match",
      cell: ({ row: { original: o } }) => (
        <div className="text-xs">
          <div>{o.match_class ? MATCH_LABELS[o.match_class] : "–"}{o.units_required && o.units_required > 1 ? ` ×${o.units_required}` : ""}</div>
          <div className="text-muted">{percent(o.match_confidence)} · {o.match_status}</div>
        </div>
      ),
    }),
    col.accessor("updated_at", { header: "Aktualisiert", cell: (c) => <span className="text-xs">{dateTime(c.getValue())}</span> }),
  ], []);

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    state: { sorting, rowSelection: selection },
    onSortingChange: (u) => { setSorting(u); setPage(0); },
    onRowSelectionChange: setSelection,
    getRowId: (r) => r.id,
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
  });

  const selected = Object.keys(selection).filter((k) => selection[k]);

  async function bulk(action: "purchase" | "ignore" | "analyze" | "shortlist") {
    setMsg(null);
    try {
      if (action === "purchase") {
        const r = await api<{ created: string[]; skipped: { reason: string }[] }>("opportunities/purchase-list", { method: "POST", body: JSON.stringify({ ids: selected }) });
        setMsg(`${r.created.length} Einkaufsvorschläge erstellt${r.skipped.length ? `, ${r.skipped.length} übersprungen: ${[...new Set(r.skipped.map((s) => s.reason))].join("; ")}` : ""}`);
      } else if (action === "analyze") {
        const r = await api<{ queued: number }>("opportunities/analyze", { method: "POST", body: JSON.stringify({ ids: selected }) });
        setMsg(`${r.queued} Angebote zur Detailanalyse eingeplant`);
      } else {
        await api("opportunities/status", { method: "POST", body: JSON.stringify({ ids: selected, status: action === "ignore" ? "ignored" : "shortlisted" }) });
        setMsg(action === "ignore" ? "Ignoriert" : "Auf Merkliste");
      }
      setSelection({});
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  function exportUrl(fmt: "csv" | "xlsx") {
    const p = { ...params, fmt, offset: undefined, limit: undefined, ids: selected.length ? selected : undefined };
    return `/api/backend/opportunities/export${qs(p)}`;
  }

  const set = (k: keyof typeof filters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setFilters({ ...filters, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <div>
      {applied.include_test && <TestDataBanner />}
      <Card className="mb-4 p-3">
        <form className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8" onSubmit={(e) => { e.preventDefault(); setApplied(filters); setPage(0); }}>
          <div className="col-span-2"><Label>Suche (Titel, EAN, ASIN, SKU)</Label><Input value={filters.q} onChange={set("q")} /></div>
          <div><Label>Marke</Label><Input value={filters.brand} onChange={set("brand")} /></div>
          <div><Label>Kategorie</Label><Input value={filters.category} onChange={set("category")} /></div>
          <div><Label>Lieferant</Label>
            <Select className="w-full" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
              <option value="">alle</option>
              {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </div>
          <div><Label>Marktplatz</Label>
            <Select className="w-full" value={filters.marketplace} onChange={set("marketplace")}>
              <option value="">alle</option><option value="amazon">Amazon</option><option value="ebay">eBay</option>
            </Select>
          </div>
          <div><Label>Matching-Status</Label>
            <Select className="w-full" value={filters.match_class} onChange={set("match_class")}>
              <option value="">alle</option>
              {Object.entries(MATCH_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </div>
          <div><Label>Min. Score</Label><Input type="number" value={filters.min_score} onChange={set("min_score")} /></div>
          <div><Label hint="%">Min. Marge</Label><Input type="number" value={filters.min_margin} onChange={set("min_margin")} /></div>
          <div><Label hint="€">Min. DB</Label><Input type="number" step="0.01" value={filters.min_contribution} onChange={set("min_contribution")} /></div>
          <div><Label hint="€">VK von</Label><Input type="number" value={filters.min_sell} onChange={set("min_sell")} /></div>
          <div><Label hint="€">VK bis</Label><Input type="number" value={filters.max_sell} onChange={set("max_sell")} /></div>
          <div><Label hint="€">EK von</Label><Input type="number" value={filters.min_buy} onChange={set("min_buy")} /></div>
          <div><Label hint="€">EK bis</Label><Input type="number" value={filters.max_buy} onChange={set("max_buy")} /></div>
          <div><Label>Min. Nachfrage</Label><Input type="number" value={filters.min_demand} onChange={set("min_demand")} /></div>
          <div className="col-span-2 flex flex-wrap items-end gap-3 text-sm">
            <label className="flex items-center gap-1"><input type="checkbox" checked={filters.include_excluded} onChange={set("include_excluded")} /> Ausgeschlossene zeigen</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={filters.include_test} onChange={set("include_test")} /> Testdaten zeigen</label>
            <Button type="submit" size="sm">Filtern</Button>
          </div>
        </form>
      </Card>

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted">{data ? `${data.total} Treffer` : "…"}{selected.length ? ` · ${selected.length} ausgewählt` : ""}</span>
        <div className="flex-1" />
        <Button size="sm" variant="outline" disabled={!selected.length} onClick={() => bulk("purchase")}>Zur Einkaufsliste</Button>
        <Button size="sm" variant="outline" disabled={!selected.length} onClick={() => bulk("analyze")}>Detailanalyse</Button>
        <Button size="sm" variant="outline" disabled={!selected.length} onClick={() => bulk("shortlist")}>Merken</Button>
        <Button size="sm" variant="outline" disabled={!selected.length} onClick={() => bulk("ignore")}>Ignorieren</Button>
        <a href={exportUrl("csv")}><Button size="sm" variant="ghost"><Download className="h-4 w-4" />CSV</Button></a>
        <a href={exportUrl("xlsx")}><Button size="sm" variant="ghost"><Download className="h-4 w-4" />Excel</Button></a>
      </div>
      {msg && <div className="mb-2 rounded-md bg-accent-soft px-3 py-2 text-sm">{msg}</div>}
      <ErrorBox error={error} />
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => (
                    <th key={h.id} className="whitespace-nowrap border-b border-border bg-bg px-3 py-2 text-left text-xs font-medium text-muted">
                      {h.column.getCanSort() ? (
                        <button className="inline-flex items-center gap-1" onClick={h.column.getToggleSortingHandler()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {h.column.getIsSorted() === "desc" ? <ArrowDown className="h-3 w-3" /> : h.column.getIsSorted() === "asc" ? <ArrowUp className="h-3 w-3" /> : null}
                        </button>
                      ) : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((r) => (
                <tr key={r.id} className={r.original.is_excluded ? "opacity-70" : ""}>
                  {r.getVisibleCells().map((c) => (
                    <td key={c.id} className="num border-b border-border px-3 py-2 align-top">{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {data && data.items.length === 0 && <Empty>Keine Opportunities für diese Filter.</Empty>}
        </div>
        {data && data.total > pageSize && (
          <div className="flex items-center justify-end gap-2 p-3 text-sm">
            <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>Zurück</Button>
            <span>Seite {page + 1} / {Math.ceil(data.total / pageSize)}</span>
            <Button size="sm" variant="outline" disabled={(page + 1) * pageSize >= data.total} onClick={() => setPage(page + 1)}>Weiter</Button>
          </div>
        )}
      </Card>
    </div>
  );
}
