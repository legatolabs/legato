// /api/* on legato.computer -> the managed gateway. The gateway's URL lives in the Vercel environment variable
// LEGATO_GATEWAY_URL, not in this public repository. vercel.json rewrites /api/<path> to /api/proxy?path=<path>.
export const config = { runtime: "edge" };

const HOP = ["host", "connection", "content-length", "transfer-encoding", "x-forwarded-host"];

export default async function handler(req) {
  const base = (process.env.LEGATO_GATEWAY_URL || "").replace(/\/$/, "");
  if (!base) {
    return Response.json({ error: { code: "unconfigured", message: "the managed API is not configured" } }, { status: 503 });
  }
  const url = new URL(req.url);
  const path = (url.searchParams.get("path") || "").replace(/^\/+/, "");
  if (path.split("/").some((p) => p === "..")) {
    return Response.json({ error: { code: "bad_request", message: "bad path" } }, { status: 400 });
  }
  url.searchParams.delete("path");
  const target = `${base}/${path}${url.searchParams.size ? "?" + url.searchParams : ""}`;
  const headers = new Headers(req.headers);
  for (const h of HOP) headers.delete(h);
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();
  const r = await fetch(target, { method: req.method, headers, body, redirect: "manual" });
  const out = new Headers(r.headers);
  for (const h of ["content-encoding", "content-length", "transfer-encoding", "connection"]) out.delete(h);
  return new Response(r.body, { status: r.status, headers: out });
}
