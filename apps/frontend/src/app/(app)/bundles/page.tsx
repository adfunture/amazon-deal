import { OpportunityTable } from "@/components/opportunity-table";
import { PageHeader } from "@/components/ui";

export default function BundlesPage() {
  return (
    <div>
      <PageHeader
        title="Bundle Opportunities"
        description="Multipacks und Bundles. Einkauf erst nach manueller Bestätigung des Matches und der Komponenten. Reverse-Bundle-Sourcing folgt in Phase 3."
      />
      <OpportunityTable bundleOnly />
    </div>
  );
}
