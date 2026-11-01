import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { TOKEN_COOKIE, backendUrl } from "@/lib/server";

/**
 * Reverse-Proxy zum FastAPI-Backend. Das JWT liegt als httpOnly-Cookie vor und wird hier
 * als Bearer-Token angehängt – es ist für Browser-JavaScript nie lesbar.
 */
async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path.some((p) => p === ".." || p.includes("\\"))) {
    return NextResponse.json({ detail: "Ungültiger Pfad" }, { status: 400 });
  }
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  const target = `${backendUrl()}/api/v1/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  if (token) headers.set("authorization", `Bearer ${token}`);
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers.set("x-forwarded-for", fwd);

  const init: RequestInit & { duplex?: "half" } = { method: req.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = req.body;
    init.duplex = "half";
  }
  let res: Response;
  try {
    res = await fetch(target, init);
  } catch {
    return NextResponse.json({ detail: "Backend nicht erreichbar" }, { status: 502 });
  }
  const out = new Headers();
  for (const h of ["content-type", "content-disposition"]) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  const response = new NextResponse(res.body, { status: res.status, headers: out });
  if (res.status === 401) response.cookies.delete(TOKEN_COOKIE);
  return response;
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
