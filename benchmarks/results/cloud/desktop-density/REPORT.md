# Runtime autotune: generation 22

Self-iterating search over app-agnostic runtime knobs (README.md in autoresearch/autotune). Tier E: scripted replays, no model; the settle rule is fitted per candidate on training operations and checked on held-out operations.

previous state: 20261104T041918Z-311; spend by day (est.): {"2026-11-03": 5.0, "2026-11-04": 8.28}

## latency champion (since gen 3)

- diff from baseline: `{}`
- s/action 1.537; container-s/episode 10.46; reset 1.59 s; success 30/30; held-out early 0.0196078431372549
- fitted rule: `{"key": [1.0, 0.75], "type": [0.75, 0.05], "click": [0.0, 0.1], "double_click": [0.0, 0.05]}`

## throughput champion (since gen 16)

- diff from baseline: `{"park_pointer": true, "desktops": 16}`
- s/action 1.486; container-s/episode 0.67; reset 2.34 s; success 29/30; held-out early 0.0
- fitted rule: `{"key": [0.0, 1.0], "type": [1.25, 0.05], "click": [1.25, 0.05], "double_click": [0.0, 0.05]}`

## Where the latency champion's time goes (what is worth an experiment)

- settle: fitted policy waits 0.999 s per action; a perfect 'done' signal would wait 0.29 s. That gap is reachable only through a better signal (code), not a knob.
- smallest effect a generation detects: ~12.9%

| component | share of cost |
|---|---|
| wait:key | 58.0% |
| act:click | 19.1% |
| wait:type | 7.9% |
| act:double_click | 6.3% |
| act:key | 4.5% |
| wait:click | 1.5% |
| grab | 1.2% |
| poll | 1.0% |

## Is the harness trustworthy?

- A/A control (the champion against itself, every generation): 19 run, 3 false 'significant' differences; latest ratio 0.973 [0.856, 1.105]
- A/A band (fit noise the task bootstrap does not see): 0.126; a win needs CI hi below 0.874
- promotions need two independent paired wins (win-unconfirmed, then confirm)
- proposer: working
- pending confirmation: []

## Progress

| gen | latency champion s/action | throughput champion container-s/episode | est. $ |
|---|---|---|---|
| 1 | 1.331 | 9.74 | 0.23 |
| 2 | 1.265 | 9.79 | 0.25 |
| 3 | 1.267 | 12.07 | 0.24 |
| 4 | 1.144 | None | 1.07 |
| 5 | 0.996 | None | 0.42 |
| 6 | 1.241 | None | 0.46 |
| 7 | 1.063 | 1.95 | 0.93 |
| 8 | 1.311 | 1.13 | 0.68 |
| 9 | 1.453 | 1.17 | 0.68 |
| 10 | 1.803 | 1.57 | 0.43 |
| 11 | 1.368 | 1.3 | 0.81 |
| 12 | 1.292 | 1.13 | 0.75 |
| 13 | 1.396 | 1.0 | 0.55 |
| 14 | 1.259 | 0.9 | 0.61 |
| 15 | 1.223 | 1.13 | 0.57 |
| 16 | 1.301 | 0.72 | 0.29 |
| 17 | 1.169 | 0.77 | 0.65 |
| 18 | 1.372 | 1.06 | 0.60 |
| 19 | 1.216 | 0.64 | 0.64 |
| 20 | 1.438 | 0.82 | 0.83 |
| 21 | 1.204 | 0.71 | 0.80 |
| 22 | 1.537 | 0.67 | 0.63 |

## Ledger (latest 40)

| gen | track | source | diff | verdict | ratio [95%] | why / hypothesis |
|---|---|---|---|---|---|---|
| 14 | throughput | llm | `{"type_delay_ms": 0, "park_pointer": true, "desktops": 16}` | void | 0.509 [0.453, 0.569] | A/A control failed this generation |
| 14 | latency | llm | `{"poll_s": 0.015, "type_delay_ms": 0, "key_delay_ms": 0, "mousemove_sync": false, "park_po` | promising | 0.934 [0.740, 1.052] | Targets wait:key (55-70% of per-action cost) plus act:key (6%) on top of the gen-13 promising stack (0.878): key_delay_ms=0 (baseline 40, ne |
| 14 | throughput | mutate | `{"park_pointer": true, "desktops": 12}` | void | 0.725 [0.668, 0.801] | A/A control failed this generation |
| 15 | latency | retest | `{"poll_s": 0.015, "type_delay_ms": 0, "key_delay_ms": 0, "mousemove_sync": false, "park_po` | promising | 0.930 [0.756, 1.120] | retest: Targets wait:key (55-70% of per-action cost) plus act:key (6%) on top of the gen-13 promising stack (0.878): key_delay_ms=0 (baselin |
| 15 | throughput | ablate | `{"park_pointer": true}` | loss | 7.969 [7.602, 8.534] | ablation: is desktops=8 still pulling its weight? |
| 15 | throughput | mutate | `{"park_pointer": true, "desktops": 16}` | void | 0.648 [0.633, 0.663] | A/A control failed this generation |
| 15 | throughput | mutate | `{"park_pointer": true, "desktops": 4}` | loss | 2.019 [1.981, 2.057] | local move: {"desktops": 4} |
| 16 | throughput | confirm | `{"park_pointer": true, "desktops": 16}` | win | 0.608 [0.543, 0.671] | confirm: re-judged gen 15 (void -> win against A/A band 0.126) |
| 16 | throughput | llm | `{"poll_s": 0.06, "digest_reduce": 8, "type_delay_ms": 0, "mousemove_sync": false, "park_po` | win-unconfirmed | 0.671 [0.638, 0.715] | re-run on fresh tasks next generation; promoted only if it wins again |
| 16 | throughput | llm | `{"type_delay_ms": 0, "key_delay_ms": 0, "mousemove_sync": false, "park_pointer": true, "de` | promising | 0.981 [0.974, 0.987] | CI clears 1 but not the A/A band (needs hi < 0.874) |
| 17 | throughput | confirm | `{"poll_s": 0.06, "digest_reduce": 8, "type_delay_ms": 0, "mousemove_sync": false, "park_po` | loss | 1.018 [0.956, 1.099] | confirm: Targets the reset+amortization term of container-s/episode (desktops=8 confirmed 0.536/0.916, desktops=16 hit 0.597-0.62 three time |
| 17 | throughput | retest | `{"type_delay_ms": 0, "key_delay_ms": 0, "mousemove_sync": false, "park_pointer": true, "de` | loss | 1.569 [1.476, 1.706] | retest: Targets per-episode settle waits (key/type ~70% of per-action time) plus act:key serialization, which is amplified under 8-way core  |
| 17 | latency | llm | `{"poll_s": 0.015, "type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay_ms": 44, "mousemove` | loss | 0.987 [0.916, 1.107] | Targets act:double_click (6.7% of cost at 3.19 s/action, dominated by the 76 ms dclick delay) plus the confirmed per-action stack: the gen-1 |
| 17 | latency | mutate | `{"dclick_delay_ms": 44, "park_pointer": true}` | promising | 0.952 [0.859, 1.008] | local move: {"dclick_delay_ms": 44, "park_pointer": true} |
| 17 | throughput | mutate | `{"poll_s": 0.045, "desktops": 16}` | promising | 0.955 [0.916, 1.013] | local move: {"poll_s": 0.045, "park_pointer": false} |
| 17 | throughput | llm | `{"type_delay_ms": 0, "key_delay_ms": 0, "mousemove_sync": false, "park_pointer": true, "de` | skipped |  | its knobs hold 7.1% of the cost; detectable effect ~12.6% |
| 18 | latency | llm | `{"type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay_ms": 44, "park_pointer": true}` | promising | 0.831 [0.629, 1.098] | The setup_cmds/gsettings+env bundle is the likely poison in the failed latency stacks (0.858/0.934 promising but 1.14/0.987 on retest), so k |
| 18 | throughput | ablate | `{"park_pointer": true}` | loss | 8.645 [7.805, 9.610] | ablation: is desktops=16 still pulling its weight? |
| 18 | throughput | mutate | `{"digest_reduce": 4, "park_pointer": true, "desktops": 8}` | loss | 1.362 [1.220, 1.514] | local move: {"digest_reduce": 4, "desktops": 8} |
| 18 | throughput | mutate | `{"park_pointer": true, "desktops": 2}` | loss | 5.241 [4.784, 5.596] | local move: {"desktops": 2} |
| 18 | throughput | llm | `{"poll_s": 0.045, "park_pointer": true, "desktops": 16}` | skipped |  | its knobs hold 0.9% of the cost; detectable effect ~12.6% |
| 19 | latency | retest | `{"type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay_ms": 44, "park_pointer": true}` | promising | 0.901 [0.737, 0.998] | CI clears 1 but not the A/A band (needs hi < 0.874) |
| 19 | latency | llm | `{"type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay_ms": 44, "mousemove_sync": false, "p` | promising | 0.948 [0.782, 1.056] | Gen-18 pure stack (0.831) plus mousemove_sync=false, which drops the xdotool --sync round-trip before every click: act:click is 19.2% of per |
| 19 | throughput | llm | `{"park_pointer": true, "desktops": 16, "setup_cmds": ["gsettings set org.gnome.desktop.int` | promising | 0.976 [0.967, 0.986] | CI clears 1 but not the A/A band (needs hi < 0.874) |
| 19 | throughput | mutate | `{"dclick_delay_ms": 44, "park_pointer": true, "desktops": 12}` | loss | 1.256 [1.137, 1.352] | local move: {"dclick_delay_ms": 44, "desktops": 12} |
| 19 | throughput | mutate | `{"digest_reduce": 1, "desktops": 16}` | loss | 0.995 [0.864, 1.181] | local move: {"digest_reduce": 1, "park_pointer": false} |
| 20 | latency | retest | `{"type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay_ms": 44, "mousemove_sync": false, "p` | promising | 0.919 [0.742, 1.135] | retest: Gen-18 pure stack (0.831) plus mousemove_sync=false, which drops the xdotool --sync round-trip before every click: act:click is 19.2 |
| 20 | latency | llm | `{"poll_s": 0.005, "digest_reduce": 8, "type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay` | promising | 0.913 [0.746, 1.097] | Targets wait:key (67.8% of cost): the 0.75s key quiet window exists largely to ride out caret blink; killing cursor blink alone (isolated fr |
| 20 | throughput | mutate | `{"key_delay_ms": 34, "desktops": 16}` | loss | 1.013 [0.961, 1.098] | local move: {"key_delay_ms": 34, "park_pointer": false} |
| 20 | latency | mutate | `{"key_delay_ms": 28, "dclick_delay_ms": 92}` | promising | 0.888 [0.778, 1.004] | local move: {"key_delay_ms": 28, "dclick_delay_ms": 92} |
| 21 | latency | retest | `{"poll_s": 0.005, "digest_reduce": 8, "type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay` | promising | 0.908 [0.715, 1.114] | retest: Targets wait:key (67.8% of cost): the 0.75s key quiet window exists largely to ride out caret blink; killing cursor blink alone (iso |
| 21 | latency | llm | `{"type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay_ms": 44, "mousemove_sync": false, "p` | rejected |  | held-out stale screens 6 vs champion 0 (of 61) |
| 21 | throughput | llm | `{"park_pointer": true, "desktops": 16, "setup_cmds": ["gsettings set org.gnome.desktop.int` | rejected |  | held-out stale screens 3 vs champion 0 (of 61) |
| 21 | throughput | ablate | `{"park_pointer": true}` | loss | 9.855 [8.845, 11.208] | ablation: is desktops=16 still pulling its weight? |
| 21 | throughput | mutate | `{"poll_s": 0.06, "park_pointer": true, "desktops": 4}` | loss | 3.659 [3.341, 3.930] | local move: {"poll_s": 0.06, "desktops": 4} |
| 21 | throughput | mutate | `{"mousemove_sync": false, "desktops": 16}` | promising | 0.924 [0.910, 0.941] | CI clears 1 but not the A/A band (needs hi < 0.874) |
| 21 | throughput | llm | `{"mousemove_sync": false, "park_pointer": true, "desktops": 16}` | skipped |  | its knobs hold 1.5% of the cost; detectable effect ~12.6% |
| 22 | latency | llm | `{"poll_s": 0.005, "digest_reduce": 8, "type_delay_ms": 0, "key_delay_ms": 0, "dclick_delay` | promising | 0.720 [0.496, 0.979] | CI clears 1 but not the A/A band (needs hi < 0.874) |
| 22 | throughput | mutate | `{"poll_s": 0.015, "desktops": 16}` | loss | 1.127 [1.023, 1.304] | local move: {"poll_s": 0.015, "park_pointer": false} |
| 22 | latency | mutate | `{"type_delay_ms": 6, "park_pointer": true}` | promising | 0.726 [0.528, 0.957] | CI clears 1 but not the A/A band (needs hi < 0.874) |

## Backlog: ideas that need code (from the proposer)

- Per-operation settle prior: record each training operation's observed last-change time and set the next run's floor to (prior minus a safety margin), removing most of the ~0.66 s/action overwait between the fitted quiet windows and the perfect done signal (~40% of per-action cost on both tracks).
- Asynchronous action dispatch: fire the xdotool click/key command and start settle polling immediately without waiting for the tool process to exit, removing the act:click + act:double_click serialization (~27% of latency-track cost).
- Persistent input-injection daemon holding one X connection and issuing XTest fake key/button events, eliminating per-action xdotool process spawn and its --sync round-trip (removes act:click + act:key + act:type serialization, ~9% of latency cost and a larger share under multi-desktop CPU contention
- Use the X server's processed-request counter / XSync fence after each injected input event as a cheap 'input applied' signal, letting the settle rule start its react/quiet clock at the true event time instead of after tool exit (removes a large part of the ~0.66 s/action overwait, ~40% of per-action
- Cosmetic-change mask: per-desktop learned set of periodically recurring frame digests (cursor blink, caret, animation loops) treated as non-changes by the settle rule, shrinking the key/type quiet window from 0.75 s toward the 0.31 s perfect-done floor and removing ~40% of per-action cost on both tr
- Exponential-backoff poll schedule: poll at 5 ms right after the action (catch the first change fast) then back off to 100 ms during quiet, cutting poll+quantization cost of the wait and making fine polling affordable under 16-desktop CPU contention, worth ~5-10% of per-action time plus reduced conte
- Pre-forked idle desktop pool: keep N booted-but-idle Xvfb+WM sessions ready and atomically swap one in at reset instead of teardown+launch, removing most of the ~1.76 s reset term (a large share of container-s/episode at high desktops counts).
- Shared batch grabber: one process per container grabs all desktops' frames in a single scheduled pass and hands digests to each desktop's settle loop, cutting per-poll grab+hash cost (~2x inflated under contention) and letting fine poll_s stay affordable at desktops=16.
- Inject whole type strings as one XTest batch (single keystring command, no per-char delay) so the settle floor for type actions drops from 0.75 s toward the 0.31 s perfect-done floor, removing ~7% of per-action cost on both tracks.
- Read the Xvfb framebuffer via mmap of the server's shared memory segment instead of XGetImage grabs, cutting the per-poll grab cost (~1.1% latency, but 2x inflated and per-desktop multiplied under 16-way contention) without new polling machinery.
- XRecord-based settle signal: subscribe to the target window's client protocol traffic (key/map/property requests) via the X server's record extension and treat request-quiet as a done signal, shrinking the key/type quiet window from ~1.0 s toward the 0.32 s perfect-done floor (~30-40% of per-action 
- Page-cache pre-warm at desktop boot: prefault the app and toolkit shared libraries and font files (read-through of all mapped pages) so first-action act_s spikes and reset relaunch I/O disappear, cutting the reset term and the first few actions' serialization (~20-30% of reset container-seconds and 
- Root-window event settle: subscribe to Map/Unmap/Configure/PropertyNotify on the root window as a near-free coarse 'something happened / now quiet' signal for click actions, replacing part of the 2.0 s floor and the grab-based react detection (~20% of latency cost, larger under contention).
- Run each Xvfb at reduced framebuffer depth/resolution (e.g. 16-bit, smaller screen) so every poll's grab+hash is cheaper across all desktops, cutting the per-poll cost that is ~2x inflated under 16-way contention and making fine poll_s affordable (~5-10% of per-action time plus contention relief).
- Event-driven settle via the X Damage extension: subscribe to DamageNotify on the root window so the settle loop receives frame-change events with zero polling, ending the quiet window the instant damage stops and eliminating both the per-poll grab+hash cost (~2x inflated at desktops=16) and the poll
- CPU-affinity partitioning of desktops: pin each desktop's Xvfb, WM, and app processes to a dedicated core subset via cgroup/sched_setaffinity at boot so 16-way contention stops inflating act_s, grab_s, and stale_s (the throughput champion shows 0.089s/action of stale grabs), removing ~10-20% of per-
- Adaptive per-action-kind quiet-window shrinkage: fit the settle quiet window online per desktop from observed change-gap distributions during rollouts (capped by the 2% early-return budget) instead of one offline global fit, removing a large share of the ~0.66 s/action overwait (~40% of per-action c
- Batch consecutive key actions of one chord/sequence into a single injected XTest batch with one settle wait instead of per-key waits, removing the per-key settle quantization that dominates wait:key on short sequences (~10-15% of per-action cost).
- Suspend idle desktops' Xvfb/WM/app processes (SIGSTOP/SIGCONT or cgroup freezer) while another desktop in the container is mid-action-settle, removing the ~2x contention inflation of act_s/grab_s at desktops=16 and cutting ~10-20% of throughput per-action cost.
- Suppress the rendered cursor/caret from grabbed frames via XFixes (hide cursor during digest grabs) so pointer and caret pixel churn stops counting as screen change, shrinking the key/type quiet window from 0.75 s toward the 0.31 s perfect-done floor (~30-40% of wait cost on both tracks).

end of run: not chaining: no promotion on either track for 6 generations (converged)
