import { NextRequest, NextResponse } from "next/server";
import { TOKEN_COOKIE, backendUrl } from "@/lib/server";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  if (!body?.email || !body?.password) {
    return NextResponse.json({ detail: "E-Mail und Passwort angeben" }, { status: 400 });
  }
  let res: Response;
  try {
    res = await fetch(`${backendUrl()}/api/v1/auth/login`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-forwarded-for": req.headers.get("x-forwarded-for") ?? "",
      },
      body: JSON.stringify({ email: body.email, password: body.password }),
    });
  } catch {
    return NextResponse.json({ detail: "Backend nicht erreichbar" }, { status: 502 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });
  const response = NextResponse.json({ user: data.user });
  response.cookies.set(TOKEN_COOKIE, data.access_token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 12,
  });
  return response;
}
