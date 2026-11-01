const eur = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
const pct = new Intl.NumberFormat("de-DE", { style: "percent", maximumFractionDigits: 1 });
const num = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });

/** Beträge kommen vom Backend als String (Decimal) – erst hier zur Anzeige formatiert. */
export function money(v: string | number | null | undefined, currency = "EUR"): string {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  if (Number.isNaN(n)) return "–";
  return currency === "EUR" ? eur.format(n) : new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(n);
}

export function percent(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "–";
  return pct.format(Number(v));
}

export function number(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "–";
  return num.format(Number(v));
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "–";
  return new Date(v).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

export function ean(gtin14: string | null | undefined): string {
  if (!gtin14) return "–";
  if (gtin14.startsWith("000000")) return gtin14.slice(6);
  return gtin14.startsWith("0") ? gtin14.slice(1) : gtin14;
}

export const MATCH_LABELS: Record<string, string> = {
  EXACT_MATCH: "Exakt",
  VARIANT_MATCH: "Variante",
  MULTIPACK: "Multipack",
  BUNDLE: "Bundle",
  PACKAGING_DIFFERENCE: "Verpackung",
  POSSIBLE_MATCH: "Möglich",
  NO_MATCH: "Kein Match",
  REVIEW_REQUIRED: "Prüfen",
};
