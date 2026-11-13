# H2H-1: Legato vs reference harnesses (anthropic/claude-sonnet-5.5, qwen/qwen3.8-27b)

Legato Office Bench on real Ubuntu desktops (LibreOffice Calc and Impress), 2 attempts per task per arm, all arms at once on one host, every model through OpenRouter. Agent time runs from the first screenshot to the model's last turn (no reset, no grading).

- **claude-ref**: Anthropic's computer-use reference loop re-implemented (every tool call runs, 2 s sleep before each screenshot, a screenshot per action, all screenshots kept, prompt caching, free-form replies)
- **claude-legato**: legato.Harness (ready-wait, up to 3 actions per turn and one screenshot after them, short replies, screenshots dropped 4 at a time past 8, prompt caching)
- **qwen-osworld**: OSWorld's QwenAgent scaffold (mm_agents/qwen: keeps 20 screenshots, drops 10 at a time) with OSWorld's runner default of 0 s wait
- **qwen-legato**: legato.Harness on the same open model

| arm | rollouts | passed | success | agent s (mean) | median | calls/task | actions/call | model s/call | out tok/call | in tok/call | cached | wait s/action | $/task | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen-osworld | 48 | 26 | 54% | 212.5 | 116.9 | 23.2 | 3.07 | 7.90 | 549 | 25852 | 76% | 0.00 | 0.058 | 0 |
| qwen-legato | 48 | 36 | 75% | 90.0 | 76.7 | 18.0 | 0.74 | 4.28 | 343 | 12267 | 54% | 0.67 | 0.034 | 0 |

Against **qwen-osworld** (ratio of summed per-task mean agent time; below 1 is faster):

- **qwen-legato**: 0.424 [0.299, 0.622] over 24 tasks (2.36x faster); tasks both solved every time: 0.463 over 7; success +21 pts

| task | qwen-osworld passed | qwen-osworld s | qwen-legato passed | qwen-legato s |
|---|---|---|---|---|
| office-00-copy_total | 2/2 | 73.4 | 2/2 | 53.8 |
| office-01-copy_total | 1/2 | 584.7 | 2/2 | 50.1 |
| office-02-copy_title | 2/2 | 110.2 | 2/2 | 60.0 |
| office-03-copy_title | 0/2 | 102.1 | 2/2 | 59.4 |
| office-04-top_region | 0/2 | 414.6 | 2/2 | 59.3 |
| office-05-top_region | 2/2 | 112.6 | 0/2 | 154.3 |
| office-06-top3_list | 1/2 | 170.8 | 1/2 | 225.5 |
| office-07-compute_total | 1/2 | 179.8 | 2/2 | 64.4 |
| office-08-compute_total | 2/2 | 96.7 | 1/2 | 78.1 |
| office-09-region_table | 0/2 | 134.1 | 0/2 | 131.9 |
| office-10-next_steps | 1/2 | 140.8 | 1/2 | 82.2 |
| office-11-paste_chart | 2/2 | 229.7 | 0/2 | 95.5 |
| office-12-copy_total | 2/2 | 106.6 | 2/2 | 49.9 |
| office-13-copy_total | 0/2 | 90.3 | 2/2 | 68.5 |
| office-14-copy_title | 2/2 | 108.9 | 2/2 | 76.8 |
| office-15-copy_title | 2/2 | 71.8 | 2/2 | 46.8 |
| office-16-top_region | 1/2 | 904.8 | 1/2 | 138.6 |
| office-17-top_region | 1/2 | 112.6 | 2/2 | 94.3 |
| office-18-top3_list | 0/2 | 168.6 | 2/2 | 52.5 |
| office-19-compute_total | 0/2 | 202.1 | 2/2 | 76.2 |
| office-20-compute_total | 2/2 | 194.0 | 2/2 | 102.0 |
| office-21-region_table | 0/2 | 193.0 | 1/2 | 116.2 |
| office-22-next_steps | 0/2 | 210.9 | 1/2 | 126.7 |
| office-23-paste_chart | 2/2 | 386.0 | 2/2 | 96.9 |
