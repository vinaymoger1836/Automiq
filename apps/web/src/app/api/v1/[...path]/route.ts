import { NextRequest } from "next/server";
import { proxyToApi } from "@/lib/proxy";

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context) {
  const { path } = await context.params;
  return proxyToApi(request, `/api/v1/${path.map(encodeURIComponent).join("/")}`);
}

export const dynamic = "force-dynamic";
export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
