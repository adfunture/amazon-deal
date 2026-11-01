import { OpportunityTable } from "@/components/opportunity-table";
import { PageHeader } from "@/components/ui";

export default function OpportunitiesPage() {
  return (
    <div>
      <PageHeader title="Product Opportunities" description="Bewertete Einkaufschancen – Beträge netto (EK) bzw. brutto (VK), DB nach allen erfassten Kosten" />
      <OpportunityTable />
    </div>
  );
}
