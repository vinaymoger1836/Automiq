import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const apiUrl = process.env.API_INTERNAL_URL;
  if (!apiUrl) {
    return NextResponse.json({ status: "degraded", checks: { api: "unconfigured" } }, { status: 503 });
  }
  try {
    const response = await fetch(`${apiUrl}/health/ready`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    const body: unknown = await response.json();
    return NextResponse.json(body, { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ status: "degraded", checks: { api: "unavailable" } }, { status: 503 });
  }
}
