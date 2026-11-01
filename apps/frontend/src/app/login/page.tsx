"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Package } from "lucide-react";
import { Button, Card, ErrorBox, Input, Label } from "@/components/ui";

function LoginForm() {
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    setBusy(false);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(typeof d.detail === "string" ? d.detail : res.status === 422 ? "Eingaben ungültig" : "Anmeldung fehlgeschlagen");
      return;
    }
    const next = params.get("next");
    window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  }

  return (
    <Card className="w-full max-w-sm p-6">
      <div className="mb-5 flex items-center gap-2">
        <Package className="h-6 w-6 text-accent" />
        <div>
          <div className="font-semibold">SBC AI Sourcing Intelligence</div>
          <div className="text-xs text-muted">SBC symbio GmbH &amp; Co. KG</div>
        </div>
      </div>
      <ErrorBox error={error} />
      <form onSubmit={submit} className="space-y-3">
        <div>
          <Label>E-Mail</Label>
          <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <Label>Passwort</Label>
          <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "Anmelden…" : "Anmelden"}
        </Button>
      </form>
    </Card>
  );
}

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Suspense>
        <LoginForm />
      </Suspense>
    </div>
  );
}
