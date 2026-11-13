// legato.computer: the wave field behind the hero, quickstart tabs, copy-to-clipboard, the early-access form
// (POST /api/waitlist), and the year.
(() => {
  const $ = (s, el = document) => el.querySelector(s);

  /* ---------- waves: thin lines that drift as one ribbon ---------- */
  const cv = $("canvas.waves");
  if (cv && cv.getContext) {
    const ctx = cv.getContext("2d");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, dpr = 1, t = 0, running = true, raf = 0;
    const LINES = 26;
    const size = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = cv.clientWidth; h = cv.clientHeight;
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      const base = h * 0.74, amp = Math.min(90, h * 0.11), step = Math.max(6, w / 180);
      for (let i = 0; i < LINES; i++) {
        const f = i / (LINES - 1);                       // 0..1 across the ribbon
        const alpha = 0.06 + 0.16 * Math.sin(Math.PI * f);
        ctx.strokeStyle = `rgba(236, 232, 223, ${alpha.toFixed(3)})`;
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        for (let x = -20; x <= w + 20; x += step) {
          const u = x / w;
          const y = base
            + amp * Math.sin(u * 3.1 + t * 0.35 + f * 1.4)
            + amp * 0.45 * Math.sin(u * 7.3 - t * 0.22 + f * 2.6)
            + (f - 0.5) * amp * 1.1 * Math.cos(u * 2.2 + t * 0.15);
          x <= -20 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    };
    const loop = () => { if (!running) return; t += 0.012; draw(); raf = requestAnimationFrame(loop); };
    size(); draw();
    window.addEventListener("resize", () => { size(); draw(); });
    if (!still) {
      raf = requestAnimationFrame(loop);
      new IntersectionObserver(([e]) => {                // stop drawing once the hero is off screen
        running = e.isIntersecting;
        cancelAnimationFrame(raf);
        if (running) raf = requestAnimationFrame(loop);
      }).observe(cv);
    }
  }

  /* ---------- quickstart tabs ---------- */
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

  /* ---------- copy ---------- */
  for (const b of document.querySelectorAll("[data-copy]")) {
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); } catch { return; }
      const tag = b.querySelector(".copied");
      if (tag) { tag.textContent = "copied"; setTimeout(() => { tag.textContent = "copy"; }, 1500); }
    });
  }

  /* ---------- early access ---------- */
  const f = $("#wl"), msg = $("#wl-msg");
  if (f) f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f).entries());
    data.email = (data.email || "").trim();
    msg.className = "formmsg";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email)) {
      msg.className = "formmsg err"; msg.textContent = "Please enter a valid email address."; f.email.focus(); return;
    }
    const btn = f.querySelector("button"); btn.disabled = true; msg.textContent = "sending…";
    let ok = false;
    try {
      const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 10000);
      const r = await fetch("/api/waitlist", { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify(data), signal: ctl.signal });
      clearTimeout(timer); ok = r.ok;
    } catch { ok = false; }
    btn.disabled = false;
    if (ok) { msg.className = "formmsg ok"; msg.textContent = "Thank you. We will be in touch."; f.reset(); }
    else { msg.className = "formmsg err"; msg.textContent = "That did not go through. Please try again, or email hello@legato.computer."; }
  });

  const yr = $("#yr");
  if (yr) yr.textContent = String(new Date().getFullYear());
})();
