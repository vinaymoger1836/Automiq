import { NextRequest } from "next/server";

export async function proxyToApi(request: NextRequest, path: string): Promise<Response> {
  const base = process.env.API_INTERNAL_URL || "http://localhost:8000";
  const url = new URL(`${path}${request.nextUrl.search}`, base);
  const headers = new Headers(request.headers);
  for (const name of ["host", "connection", "content-length", "transfer-encoding"]) headers.delete(name);
  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
      redirect: "manual",
    });
    const responseHeaders = new Headers();
    for (const name of ["content-type", "cache-control", "location", "set-cookie", "x-request-id", "x-accel-buffering"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    responseHeaders.set("Cache-Control", "no-store");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json({ error: { code: "upstream_unavailable", message: "The API is unavailable" } }, { status: 503 });
  }
}
