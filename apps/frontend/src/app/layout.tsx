import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SBC AI Sourcing Intelligence",
  description: "Einkaufschancen für Amazon, eBay und Kaufland finden, prüfen und kalkulieren",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}
