import { PhasePlaceholder } from "@/components/shell";

export default function Page() {
  return (
    <PhasePlaceholder title="Preisüberwachung" phase={5} items={[
      "Bereits verfügbar: Preisänderungen je Lieferant (Lieferantenseite) und Preisverlauf je Opportunity (Detailseite)",
      "Geplant: Preisalarme, Watchlists, geplante Keepa-Aktualisierung für beobachtete Produkte",
    ]} />
  );
}
