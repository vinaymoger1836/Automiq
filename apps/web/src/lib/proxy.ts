import { NextRequest } from "next/server";

export async function proxyToApi(request: NextRequest, path: string): Promise<Response> {
  const base = process.env.API_INTERNAL_URL || "http://localhost:8000";
  const url = new URL(`${path}${request.nextUrl.search}`, base);
  const headers = new Headers(request.headers);
  for (const name of ["host", "connection", "content-length", "transfer-encoding"]) headers.delete(name);
  try {
    let body: ArrayBuffer | undefined;
    if (request.method !== "GET" && request.method !== "HEAD") {
      if (path.startsWith("/api/v1/webhooks/")) {
        const reader = request.body?.getReader();
        const chunks: Uint8Array[] = [];
        let size = 0;
        if (reader) {
          while (true) {
            const next = await reader.read();
            if (next.done) break;
            size += next.value.byteLength;
            if (size > 64_000) {
              await reader.cancel();
              return Response.json({ error: { code: "payload_too_large", message: "Payload too large" } }, { status: 413 });
            }
            chunks.push(next.value);
          }
        }
        const bounded = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { bounded.set(chunk, offset); offset += chunk.byteLength; }
        body = bounded.buffer;
      } else body = await request.arrayBuffer();
    }
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body,
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
