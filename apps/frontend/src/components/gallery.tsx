"use client";

import { useState } from "react";
import { X } from "lucide-react";

/** Bildergalerie mit Vergrößerung (Klick). Bilder werden direkt von der Quelle geladen. */
export function Gallery({ urls, height = "h-40" }: { urls: string[]; height?: string }) {
  const [active, setActive] = useState(0);
  const [zoom, setZoom] = useState(false);
  if (!urls.length) {
    return <div className={`flex ${height} items-center justify-center rounded bg-white text-xs text-muted`}>kein Bild</div>;
  }
  const main = urls[Math.min(active, urls.length - 1)];
  return (
    <div>
      <button type="button" onClick={() => setZoom(true)} className={`flex w-full ${height} items-center justify-center rounded bg-white`}>
        <img src={main} alt="" className="max-h-full max-w-full object-contain" />
      </button>
      {urls.length > 1 && (
        <div className="mt-1 flex gap-1 overflow-x-auto">
          {urls.slice(0, 8).map((u, i) => (
            <button key={u + i} type="button" onClick={() => setActive(i)}
              className={`h-10 w-10 shrink-0 rounded border bg-white ${i === active ? "border-accent" : "border-border"}`}>
              <img src={u} alt="" className="h-full w-full object-contain" />
            </button>
          ))}
        </div>
      )}
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={() => setZoom(false)}>
          <button type="button" className="absolute right-4 top-4 rounded bg-white p-1" aria-label="Schließen"><X className="h-5 w-5" /></button>
          <img src={main} alt="" className="max-h-full max-w-full rounded bg-white object-contain" />
        </div>
      )}
    </div>
  );
}
