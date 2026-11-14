// POST /api/waitlist: the early-access form. Runs on Vercel, so a signup never depends on the Modal gateway being up.
// Each signup becomes an issue labelled "early-access" in a private GitHub repository: GitHub emails you, and the
// issues are the list. Needs two Vercel environment variables:
//   WAITLIST_GITHUB_TOKEN  a fine-grained token with Issues: read and write on that one repository
//   WAITLIST_GITHUB_REPO   owner/name, default legatolabs/legato-cloud (keep it private: issues hold emails)
// It also forwards the signup to the gateway's own waitlist when LEGATO_GATEWAY_URL is set (best effort).
export const config = { runtime: "edge" };

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const env = (k) => (process.env[k] || "").trim();
const fail = (status, code, message) => Response.json({ error: { code, message } }, { status });

async function github(entry) {
  const token = env("WAITLIST_GITHUB_TOKEN"), repo = env("WAITLIST_GITHUB_REPO") || "legatolabs/legato-cloud";
  if (!token) return null;
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json",
                    "User-Agent": "legato-waitlist", "Content-Type": "application/json" };
  // already signed up? (best effort: a failed search just means a possible duplicate issue)
  try {
    const q = encodeURIComponent(`repo:${repo} label:early-access in:title "${entry.email}"`);
    const s = await fetch(`https://api.github.com/search/issues?q=${q}`, { headers });
    if (s.ok && (await s.json()).total_count > 0) return true;
  } catch { /* create anyway */ }
  const body = [`**Email:** ${entry.email}`, `**Company:** ${entry.company || "-"}`, `**Country:** ${entry.country || "-"}`,
                "", "**What are you building?**", "", entry.use_case || "-"].join("\n");
  const r = await fetch(`https://api.github.com/repos/${repo}/issues`, { method: "POST", headers,
    body: JSON.stringify({ title: `Early access: ${entry.email}${entry.company ? ` (${entry.company})` : ""}`,
                           body, labels: ["early-access"] }) });
  return r.ok;
}

async function gateway(entry) {
  const base = env("LEGATO_GATEWAY_URL").replace(/\/$/, "");
  if (!base) return null;
  const r = await fetch(`${base}/waitlist`, { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify(entry) });
  return r.ok;
}

export default async function handler(req) {
  if (req.method !== "POST") return fail(405, "method", "POST only");
  let d;
  try { d = await req.json(); } catch { return fail(400, "bad_json", "invalid JSON"); }
  const email = String(d.email || "").trim();
  if (!EMAIL.test(email) || email.length > 256) return fail(400, "bad_email", "a valid email is required");
  const entry = { email, name: String(d.name || "").slice(0, 200), company: String(d.company || "").slice(0, 200),
                  use_case: String(d.use_case || "").slice(0, 1000), at: new Date().toISOString(),
                  country: req.headers.get("x-vercel-ip-country") || "" };
  const [gh, gw] = await Promise.all([github(entry).catch(() => false), gateway(entry).catch(() => false)]);
  return gh || gw ? Response.json({ ok: true }) : fail(502, "not_saved", "the signup could not be saved");
}
