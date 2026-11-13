// Markdown for agents: a request for the home page or a docs page with `Accept: text/markdown` gets the Markdown
// source of that page (the same file GitHub shows). Without that header, HTML is served as usual.
export const config = { matcher: ["/", "/docs", "/docs/:path*"] };

export default async function middleware(request) {
  const url = new URL(request.url);
  const accept = request.headers.get("accept") || "";
  if (!/text\/markdown/i.test(accept) || url.pathname.endsWith(".md")) return;   // continue to the HTML page
  const path = url.pathname.replace(/\/$/, "");
  const md = path === "" || path === "/docs" ? "/docs/index.md" : `${path}.md`;
  const r = await fetch(new URL(md, url.origin));
  if (!r.ok) return;
  const body = await r.text();
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "text/markdown; charset=utf-8",
      "vary": "Accept",
      "x-markdown-tokens": String(Math.ceil(body.length / 4)),
      "cache-control": "public, max-age=300",
    },
  });
}
