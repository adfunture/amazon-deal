import { NextRequest, NextResponse } from "next/server";

const PUBLIC = ["/login", "/api/auth/login", "/api/health"];

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC.some((p) => pathname.startsWith(p))) return NextResponse.next();
  if (!req.cookies.get("sbc_token")) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ detail: "Nicht angemeldet" }, { status: 401 });
    }
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
