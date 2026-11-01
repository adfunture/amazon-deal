// Healthcheck für Railway (Frontend-Service)
export function GET() {
  return Response.json({ status: "ok" });
}
