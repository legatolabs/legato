// legato.computer: quickstart tabs, copy-to-clipboard, the beta waitlist (POST /api/waitlist), and the year.
(() => {
  const $ = (s, el = document) => el.querySelector(s);

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (t) => {
    for (const x of tabs) {
      const on = x === t;
      x.setAttribute("aria-selected", String(on));
      x.tabIndex = on ? 0 : -1;
      document.getElementById(x.getAttribute("aria-controls")).hidden = !on;
    }
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      select(n); n.focus();
    });
  });

  for (const b of document.querySelectorAll("[data-copy]")) {
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); } catch { return; }
      const tag = b.querySelector(".copied");
      if (tag) { tag.textContent = "copied"; setTimeout(() => { tag.textContent = "copy"; }, 1500); }
    });
  }

  const f = $("#wl"), msg = $("#wl-msg");
  if (f) f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f).entries());
    data.email = (data.email || "").trim();
    msg.className = "formmsg";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email)) {
      msg.className = "formmsg err"; msg.textContent = "Please enter a valid email address."; f.email.focus(); return;
    }
    const btn = f.querySelector("button"); btn.disabled = true; msg.textContent = "Sending…";
    let ok = false;
    try {
      const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 10000);
      const r = await fetch("/api/waitlist", { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify(data), signal: ctl.signal });
      clearTimeout(t); ok = r.ok;
    } catch { ok = false; }
    btn.disabled = false;
    if (ok) { msg.className = "formmsg ok"; msg.textContent = "Thanks. We will be in touch."; f.reset(); }
    else { msg.className = "formmsg err"; msg.textContent = "That did not go through. Please try again, or email hello@legato.computer."; }
  });

  const yr = $("#yr");
  if (yr) yr.textContent = String(new Date().getFullYear());
})();
