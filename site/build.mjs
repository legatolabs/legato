// Builds legato.computer into site/dist: the landing page as is, and every Markdown doc in the repository rendered
// to a clean-URL page (/docs, /docs/benchmarks, /docs/compare/agent-s3, ...), with the raw Markdown next to each page
// (/docs/benchmarks.md), llms.txt, llms-full.txt, a sitemap and robots.txt. The docs are the same files GitHub shows,
// so the site and the repository cannot drift.  Run: cd site && npm ci && node build.mjs
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, posix, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { Marked } from "marked";

const SITE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(SITE, "..");
const DIST = join(SITE, "dist");
const ORIGIN = "https://legato.computer";
const REPO = "https://github.com/legatolabs/legato";

// sidebar order; anything else under docs/ is still built and linked from the sitemap
const NAV = [
  ["Start", [["README.md", "Overview and quickstart"], ["docs/models.md", "Models"], ["docs/sandboxes.md", "Sandboxes"],
             ["docs/mcp.md", "MCP server"], ["docs/rl.md", "RL rollouts"], ["docs/faq.md", "FAQ"]]],
  ["Results", [["docs/benchmarks.md", "Benchmarks"]]],
  ["Compare", [["docs/compare/anthropic-computer-use-demo.md", "vs Anthropic's demo"],
               ["docs/compare/osworld-qwen-agent.md", "vs OSWorld's Qwen agent"],
               ["docs/compare/agent-s3.md", "vs Agent S3"], ["docs/compare/ui-tars-desktop.md", "vs UI-TARS-desktop"]]],
  ["Migrate", [["docs/migrate/from-cua-agent.md", "From cua-agent"], ["docs/migrate/from-bytebot.md", "From Bytebot"]]],
];

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith(".md") ? [p] : [];
  });
}

// repository path (posix) -> site URL path
const urlOf = (rel) => (rel === "README.md" ? "/docs" : "/" + rel.replace(/\.md$/, ""));
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const slug = (s) => s.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function rewriteHref(href, fromRel) {
  if (/^([a-z]+:|#|\/)/i.test(href)) return href;                 // absolute, mailto, anchor, site path
  const [path, hash] = href.split("#");
  const target = posix.normalize(posix.join(posix.dirname(fromRel), path));
  if (target.endsWith(".md") && (target === "README.md" || target.startsWith("docs/"))) {
    return urlOf(target) + (hash ? "#" + hash : "");
  }
  const kind = existsSync(join(ROOT, target)) && statSync(join(ROOT, target)).isDirectory() ? "tree" : "blob";
  return `${REPO}/${kind}/main/${target}${hash ? "#" + hash : ""}`; // examples/, benchmarks/, LICENSE ... on GitHub
}

function render(md, fromRel) {
  const renderer = {
    heading({ tokens, depth }) {
      const inner = this.parser.parseInline(tokens);
      return `<h${depth} id="${slug(inner)}">${inner}</h${depth}>\n`;
    },
    link({ href, title, tokens }) {
      const h = rewriteHref(href, fromRel);
      const ext = /^https?:/.test(h) && !h.startsWith(ORIGIN) ? ' rel="noopener"' : "";
      return `<a href="${esc(h)}"${title ? ` title="${esc(title)}"` : ""}${ext}>${this.parser.parseInline(tokens)}</a>`;
    },
  };
  const m = new Marked({ gfm: true });
  m.use({ renderer });
  return m.parse(md);
}

function describe(md) {
  const para = md.split(/\n\s*\n/).map((b) => b.trim()).find((b) => b && !/^(#|```|\||-|>|\d+\.)/.test(b)) || "";
  const text = para.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/[*_`]/g, "").replace(/\s+/g, " ");
  return text.length > 158 ? text.slice(0, 155).replace(/\s+\S*$/, "") + "..." : text;
}

function page({ title, description, body, rel, url }) {
  const side = NAV.map(([group, items]) => `<div class="g">${group}</div>` + items.map(([r, label]) =>
    `<a href="${urlOf(r)}"${r === rel ? ' aria-current="page"' : ""}>${label}</a>`).join("")).join("");
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · Legato</title>
<meta name="description" content="${esc(description)}">
<link rel="canonical" href="${ORIGIN}${url}">
<link rel="alternate" type="text/markdown" href="${url === "/docs" ? "/docs/index.md" : url + ".md"}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/styles.css">
</head>
<body>
${HEADER}
<main class="docs wrap">
  <nav class="side" aria-label="Docs">${side}</nav>
  <article class="doc">
${body}
    <p class="edit"><a href="${REPO}/blob/main/${rel}" rel="noopener">Edit this page on GitHub</a> · <a href="${url === "/docs" ? "/docs/index.md" : url + ".md"}">Markdown</a></p>
  </article>
</main>
${FOOTER}
</body>
</html>
`;
}

const shell = readFileSync(join(SITE, "index.html"), "utf8");
const HEADER = shell.match(/<header class="top">[\s\S]*?<\/header>/)[0];
const FOOTER = shell.match(/<footer>[\s\S]*?<\/footer>/)[0];

rmSync(DIST, { recursive: true, force: true });
mkdirSync(DIST, { recursive: true });
for (const f of ["index.html", "styles.css", "site.js", "favicon.svg"]) cpSync(join(SITE, f), join(DIST, f));
const video = join(ROOT, "benchmarks/results/h2h-1/demo-claude.mp4");
if (existsSync(video)) { mkdirSync(join(DIST, "media"), { recursive: true }); cpSync(video, join(DIST, "media/demo-claude.mp4")); }

const files = ["README.md", ...walk(join(ROOT, "docs")).map((p) => relative(ROOT, p).split("\\").join("/"))];
const urls = ["/"], full = [];
for (const rel of files) {
  const md = readFileSync(join(ROOT, rel), "utf8");
  const url = urlOf(rel);
  const title = (md.match(/^#\s+(.+)$/m) || [, "Legato"])[1].replace(/[*`]/g, "");
  const out = url === "/docs" ? join(DIST, "docs/index") : join(DIST, url);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out + ".html", page({ title, description: describe(md), body: render(md, rel), rel, url }));
  writeFileSync(out + ".md", md);
  urls.push(url);
  full.push(`<!-- ${ORIGIN}${url} -->\n${md}`);
}
cpSync(join(ROOT, "llms.txt"), join(DIST, "llms.txt"));
writeFileSync(join(DIST, "llms-full.txt"), full.join("\n\n"));
writeFileSync(join(DIST, "sitemap.xml"), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n`
  + urls.map((u) => `  <url><loc>${ORIGIN}${u}</loc></url>`).join("\n") + "\n</urlset>\n");
writeFileSync(join(DIST, "robots.txt"), `User-agent: *\nAllow: /\nSitemap: ${ORIGIN}/sitemap.xml\n`);
console.log(`built ${files.length} docs pages into ${relative(ROOT, DIST)}`);
