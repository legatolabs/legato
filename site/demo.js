// legato.computer: the demo under the hero. A task typed into the command bar, then the loop it runs on a desktop
// (observe, decide, act, settle) shown step by step: open Calendar, create a 10:00 standup tomorrow, invite the team.
// An illustration, not a recording. It plays only while on screen; with reduced motion it shows the finished task.
(() => {
  const root = document.querySelector(".demo");
  if (!root) return;
  const stage = root.querySelector(".stage");
  const $ = (s) => stage.querySelector(s);
  const bar = $(".bar-cmd"), typed = $(".typed"), hud = $(".hud"), line = $(".line"), trail = $(".trail");
  const cur = $(".cursor"), say = $(".say"), ripple = $(".ripple"), cal = $(".cal"), evt = $(".evt");
  const title = $(".evt .t"), pop = $(".pop"), ptitle = $(".ptitle"), pinv = $(".pinv");
  const dockCal = $(".d-cal"), slot = $(".slot"), app = $(".menubar .app");
  const phases = [...root.querySelectorAll(".phases span")];
  const TASK = "Add a standup tomorrow at 10 and invite the team";

  let visible = false, wake = null;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && wake) { wake(); wake = null; } },
    { threshold: 0.25 }).observe(stage);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const shown = () => (visible ? Promise.resolve() : new Promise((r) => { wake = r; }));
  const wait = async (ms) => { await shown(); await sleep(ms); };

  // where an element's point is, in percent of the stage
  const at = (el, fx = 0.5, fy = 0.5) => {
    const s = stage.getBoundingClientRect(), r = el.getBoundingClientRect();
    return [((r.left + r.width * fx - s.left) / s.width) * 100, ((r.top + r.height * fy - s.top) / s.height) * 100];
  };
  const phase = (p) => phases.forEach((x) => x.classList.toggle("on", p === "all" || x.dataset.p === p));
  const step = (t) => { const s = document.createElement("span"); s.textContent = t; trail.appendChild(s);
    while (trail.children.length > 3) trail.firstChild.remove(); };
  const type = async (el, text, ms = 42) => { for (const ch of text) { el.textContent += ch; await wait(ms); } };
  const move = async (el, fx, fy, caption) => {
    const [x, y] = at(el, fx, fy);
    say.textContent = caption; say.classList.add("on");
    cur.style.left = x + "%"; cur.style.top = y + "%";
    await wait(650);
  };
  const click = async (double = false) => {
    ripple.style.left = cur.style.left; ripple.style.top = cur.style.top;
    for (let i = 0; i < (double ? 2 : 1); i++) {
      ripple.classList.remove("go"); void ripple.offsetWidth; ripple.classList.add("go"); await wait(170);
    }
  };
  // one turn of the loop: look at the screen, think, then the action the caller runs
  const turn = async (thinking = 650) => {
    phase("observe"); line.textContent = "Looking at the screen…"; await wait(380);
    phase("decide"); line.textContent = "Thinking…"; await wait(thinking);
    phase("act");
  };
  const settle = async (ms = 360) => { phase("settle"); line.textContent = "Waiting for the screen…"; await wait(ms); };

  const reset = () => {
    [bar, hud, cur, cal, evt, pop, say].forEach((e) => e.classList.remove("on"));
    hud.classList.remove("done"); dockCal.classList.remove("hit"); slot.classList.remove("hit");
    typed.textContent = ""; trail.textContent = ""; title.textContent = ""; ptitle.textContent = ""; pinv.textContent = "";
    app.textContent = "Finder"; cur.style.left = "50%"; cur.style.top = "58%"; phase(null);
  };
  const finished = () => {   // the last frame, for reduced motion
    reset(); cal.classList.add("on"); evt.classList.add("on"); title.textContent = "Standup"; app.textContent = "Calendar";
    hud.classList.add("on", "done"); line.textContent = "Done";
    ["Opened Calendar", "Created an event at 10:00 tomorrow", "Invited the team"].forEach(step); phase("all");
  };
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finished(); return; }

  const play = async () => {
    reset();
    await wait(700);
    bar.classList.add("on"); await wait(500);                        // Fn: the command bar
    await type(typed, TASK);
    await wait(500);
    bar.classList.remove("on"); hud.classList.add("on"); cur.classList.add("on");

    await turn(700);                                                  // 1: open Calendar
    await move(dockCal, 0.5, 0.45, "open Calendar"); await click(); dockCal.classList.add("hit");
    await settle(200); cal.classList.add("on"); app.textContent = "Calendar"; dockCal.classList.remove("hit");
    await wait(260); step("Opened Calendar");

    await turn(600);                                                  // 2: a new event at 10:00 tomorrow
    await move(slot, 0.5, 0.45, "double-click 10:00, Fri"); await click(true); slot.classList.add("hit");
    await settle(220); evt.classList.add("on"); slot.classList.remove("hit");
    await type(title, "Standup", 60); step("Created an event at 10:00 tomorrow");

    await turn(550);                                                  // 3: invite the team
    pop.classList.add("on"); ptitle.textContent = "Standup";
    await move(pinv, 0.3, 0.5, "type the invitees"); await click();
    await type(pinv, "team@company.com", 38);
    await settle(260); step("Invited the team");

    say.classList.remove("on"); cur.classList.remove("on"); pop.classList.remove("on");
    hud.classList.add("done"); line.textContent = "Done"; phase("all");
    await wait(2600);
  };
  (async () => { for (;;) { await play(); } })();
})();
