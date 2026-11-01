import { PhasePlaceholder } from "@/components/shell";

export default function Page() {
  return (
    <PhasePlaceholder title="Online Scanner" phase={5} items={[
      "Quellenverwaltung (URL/API, Intervall, Auth, Rate Limit, Fehlerstatus)",
      "Nur offizielle APIs, autorisierte Feeds und zulässige Zugriffswege – keine Captcha-/Sperrenumgehung",
      "Neue Angebote/Preisrückgänge erkennen und über die Import-Pipeline analysieren",
    ]} />
  );
}
