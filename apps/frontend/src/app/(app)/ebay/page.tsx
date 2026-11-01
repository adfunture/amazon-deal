import { PhasePlaceholder } from "@/components/shell";

export default function Page() {
  return (
    <PhasePlaceholder title="eBay Scanner" phase={4} items={[
      "eBay Browse API (OAuth Client Credentials): Suche nach GTIN, MPN und Titel",
      "Strikte Trennung: Angebotspreis ≠ erzielter Verkaufspreis ≠ geschätzte Nachfrage ≠ eigene Verkäufe",
      "Keine erfundenen Verkaufszahlen, wenn die API sie nicht liefert",
    ]} />
  );
}
