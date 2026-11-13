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
        ctx.strokeStyle = `rgba(255, 255, 255, ${alpha.toFixed(3)})`;
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

  /* ---------- the hero note: drawn extruded on a canvas, tilts toward the cursor ---------- */
  const mark = $(".hero-mark");
  if (mark && mark.offsetParent !== null && window.Path2D) {
    fetch("/brand/mark.svg").then((r) => r.text()).then((svg) => {
      const d = new DOMParser().parseFromString(svg, "image/svg+xml").querySelector("path").getAttribute("d");
      const note = new Path2D(d);                      // in a 128 x 128 box, centred on (64, 64)
      const c = document.createElement("canvas");
      const g = c.getContext("2d");
      mark.replaceChildren(c);
      const LAYERS = 18, NEON = [215, 255, 58];
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const hover = window.matchMedia("(hover: hover)").matches;
      let size = 0, dpr = 1;
      const fit = () => {
        dpr = Math.min(window.devicePixelRatio || 1, 2); size = mark.clientWidth;
        c.width = c.height = Math.round(size * dpr);
      };
      const shade = (k) => `rgb(${NEON.map((v) => Math.round(v * k)).join(",")})`;
      const draw = (x, y) => {
        const s = (size * dpr) / 128;
        g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, c.width, c.height);
        g.setTransform(s, 0, 0, s, 0, 0);
        const dx = (0.4 - x * 0.75) * 0.62, dy = (0.5 - y * 0.75) * 0.62;   // depth runs away from the cursor
        for (let i = LAYERS; i >= 1; i--) {             // the side, back to front, darker at the back
          g.save(); g.translate(dx * i, dy * i);
          g.fillStyle = shade(0.22 + 0.36 * (1 - i / LAYERS)); g.fill(note, "evenodd"); g.restore();
        }
        g.fillStyle = shade(1); g.fill(note, "evenodd"); // the face
        const lx = 64 - x * 40 - 20, ly = 64 - y * 30 - 16;   // a soft highlight that follows the cursor
        const hl = g.createRadialGradient(lx, ly, 0, lx, ly, 70);
        hl.addColorStop(0, "rgba(255,255,255,0.45)"); hl.addColorStop(1, "rgba(255,255,255,0)");
        g.save(); g.clip(note, "evenodd"); g.fillStyle = hl; g.fillRect(0, 0, 128, 128); g.restore();
        c.style.transform = `translate3d(${(x * 10).toFixed(1)}px, ${(y * 8).toFixed(1)}px, 0) ` +
          `rotateX(${(10 - y * 14).toFixed(2)}deg) rotateY(${(-16 + x * 22).toFixed(2)}deg)`;
      };
      let tx = 0, ty = 0, x = 0, y = 0, t = 0, on = true, raf = 0;
      fit(); draw(0, 0);
      window.addEventListener("resize", () => { fit(); draw(x, y); });
      if (still) return;
      if (hover) window.addEventListener("pointermove", (e) => {
        tx = (e.clientX / window.innerWidth - 0.5) * 2;
        ty = (e.clientY / window.innerHeight - 0.5) * 2;
      }, { passive: true });
      const tick = () => {
        if (!on) return;
        if (!hover) { t += 0.01; tx = Math.sin(t) * 0.6; ty = Math.sin(t * 0.7) * 0.4; }   // touch: a slow sway
        if (Math.abs(tx - x) + Math.abs(ty - y) > 0.0005) { x += (tx - x) * 0.07; y += (ty - y) * 0.07; draw(x, y); }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      new IntersectionObserver(([e]) => {
        on = e.isIntersecting; cancelAnimationFrame(raf);
        if (on) raf = requestAnimationFrame(tick);
      }).observe(mark);
    }).catch(() => {});                                // keep the flat image
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
