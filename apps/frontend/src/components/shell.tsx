"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, Boxes, ClipboardList, Gauge, GitCompare, Globe, LineChart, LogOut, Package, Search,
  Settings, ShoppingCart, Sparkles, Truck, Upload,
} from "lucide-react";
import { cn } from "@/components/ui";

const NAV = [
  { href: "/", label: "Dashboard", icon: Gauge },
  { href: "/opportunities", label: "Product Opportunities", icon: Sparkles },
  { href: "/imports", label: "Großhandelslisten", icon: Upload },
  { href: "/suppliers", label: "Lieferanten", icon: Truck },
  { href: "/online-scanner", label: "Online Scanner", icon: Globe, phase: 5 },
  { href: "/amazon", label: "Amazon Scanner", icon: Search },
  { href: "/ebay", label: "eBay Scanner", icon: ShoppingCart, phase: 4 },
  { href: "/bundles", label: "Bundle Opportunities", icon: Boxes },
  { href: "/matching", label: "Matching Review", icon: GitCompare },
  { href: "/purchasing", label: "Einkaufsliste", icon: ClipboardList },
  { href: "/price-monitoring", label: "Preisüberwachung", icon: LineChart },
  { href: "/settings", label: "Einstellungen", icon: Settings },
  { href: "/monitoring", label: "API Monitoring", icon: Activity },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-brand text-brand-fg md:flex">
        <div className="flex items-center gap-2 px-4 py-4">
          <Package className="h-5 w-5" />
          <div className="leading-tight">
            <div className="text-sm font-semibold">SBC Sourcing</div>
            <div className="text-[11px] opacity-70">AI Sourcing Intelligence</div>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pb-4">
          {NAV.map(({ href, label, icon: Icon, phase }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "mb-0.5 flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm opacity-85 hover:bg-white/10 hover:opacity-100",
                  active && "bg-white/15 opacity-100",
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1">{label}</span>
                {phase && <span className="rounded bg-white/15 px-1 text-[10px]">P{phase}</span>}
              </Link>
            );
          })}
        </nav>
        <button onClick={logout} className="flex items-center gap-2 border-t border-white/10 px-4 py-3 text-sm opacity-80 hover:opacity-100">
          <LogOut className="h-4 w-4" /> Abmelden
        </button>
      </aside>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 overflow-x-auto border-b border-border bg-brand px-3 py-2 text-brand-fg md:hidden">
          {NAV.map(({ href, label }) => (
            <Link key={href} href={href} className="whitespace-nowrap rounded px-2 py-1 text-xs hover:bg-white/10">
              {label}
            </Link>
          ))}
        </div>
        <main className="mx-auto max-w-[1500px] px-4 py-6 md:px-6">{children}</main>
      </div>
    </div>
  );
}

export function PhasePlaceholder({ title, phase, items }: { title: string; phase: number; items: string[] }) {
  return (
    <div>
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="mt-4 rounded-lg border border-dashed border-border bg-surface p-6">
        <p className="text-sm">
          Dieses Modul ist für <strong>Phase {phase}</strong> geplant und noch <strong>nicht implementiert</strong>. Es werden hier
          bewusst keine simulierten Daten angezeigt.
        </p>
        <ul className="mt-3 list-disc pl-5 text-sm text-muted">
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
