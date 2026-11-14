// POST /api/waitlist: the early-access form. Runs on Vercel, so a signup never depends on the Modal gateway being up.
// Each step runs only if its environment variables are set in the Vercel project:
//   1. store     KV_REST_API_URL + KV_REST_API_TOKEN  (Upstash Redis from the Vercel Marketplace sets both)
//                -> hash "waitlist", one field per lowercased email, value = the JSON entry
//   2. notify    RESEND_API_KEY + WAITLIST_NOTIFY_TO   (email to you; WAITLIST_FROM, default hello@legato.computer)
//                SLACK_WEBHOOK_URL                     (a message in a Slack channel)
//   3. forward   LEGATO_GATEWAY_URL                    (the gateway's own waitlist, as before; best effort)
// The visitor gets ok if the signup was kept somewhere: the store, a notification that went out, or the gateway.
export const config = { runtime: "edge" };

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const env = (k) => (process.env[k] || "").trim();
const fail = (status, code, message) => Response.json({ error: { code, message } }, { status });

async function store(entry) {
  const url = env("KV_REST_API_URL"), token = env("KV_REST_API_TOKEN");
  if (!url || !token) return null;                                   // not configured
  const r = await fetch(url.replace(/\/$/, ""), {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(["HSETNX", "waitlist", entry.email.toLowerCase(), JSON.stringify(entry)]),
  });
  if (!r.ok) throw new Error(`store ${r.status}`);
  return (await r.json()).result === 1;                              // true = new, false = already signed up
}

function notify(entry) {
  const lines = [`${entry.email}${entry.company ? ` (${entry.company})` : ""}`, entry.use_case || "(no use case)"];
  const jobs = [];
  if (env("RESEND_API_KEY") && env("WAITLIST_NOTIFY_TO")) {
    jobs.push(fetch("https://api.resend.com/emails", {
      method: "POST", headers: { Authorization: `Bearer ${env("RESEND_API_KEY")}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from: `Legato <${env("WAITLIST_FROM") || "hello@legato.computer"}>`,
        to: env("WAITLIST_NOTIFY_TO").split(",").map((s) => s.trim()), reply_to: entry.email,
        subject: `Early access: ${entry.email}`, text: lines.join("\n\n") }),
    }));
  }
  if (env("SLACK_WEBHOOK_URL")) {
    jobs.push(fetch(env("SLACK_WEBHOOK_URL"), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: `New early-access request\n${lines.join("\n")}` }) }));
  }
  return Promise.allSettled(jobs).then((rs) => rs.some((r) => r.status === "fulfilled" && r.value.ok));
}

async function forward(entry) {
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

  let stored = null;
  try { stored = await store(entry); } catch { stored = undefined; }  // undefined = configured but failed
  const gateway = forward(entry).catch(() => false);                 // in parallel with the rest
  const notified = stored === false ? false : await notify(entry);   // don't notify twice for the same email
  const forwarded = await gateway;

  // kept = it is in the store, or reached you by email/Slack, or reached the gateway
  const kept = stored === true || stored === false || notified || forwarded === true;
  return kept ? Response.json({ ok: true }) : fail(502, "not_saved", "the signup could not be saved");
}
