import "server-only";

export const TOKEN_COOKIE = "sbc_token";

/** Interne Backend-URL. Auf Railway über Private Networking (backend.railway.internal). */
export function backendUrl(): string {
  if (process.env.BACKEND_URL) return process.env.BACKEND_URL.replace(/\/$/, "");
  const host = process.env.BACKEND_HOST;
  const port = process.env.BACKEND_PORT ?? "8000";
  if (host) return `http://${host}:${port}`;
  return "http://localhost:8000";
}
