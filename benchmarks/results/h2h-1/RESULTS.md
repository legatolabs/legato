# H2H-1: Legato vs reference harnesses (anthropic/claude-sonnet-5.5, qwen/qwen3.8-27b)

Legato Office Bench on real Ubuntu desktops (LibreOffice Calc and Impress), 2 attempts per task per arm, all arms at once on one host, every model through OpenRouter. Agent time runs from the first screenshot to the model's last turn (no reset, no grading).

- **claude-ref**: Anthropic's computer-use reference loop re-implemented (every tool call runs, 2 s sleep before each screenshot, a screenshot per action, all screenshots kept, prompt caching, free-form replies)
- **claude-legato**: legato.Harness (ready-wait, up to 3 actions per turn and one screenshot after them, short replies, screenshots dropped 4 at a time past 8, prompt caching)
- **qwen-osworld**: OSWorld's QwenAgent scaffold (mm_agents/qwen: keeps 20 screenshots, drops 10 at a time) with OSWorld's runner default of 0 s wait
- **qwen-legato**: legato.Harness on the same open model

| arm | rollouts | passed | success | agent s (mean) | median | calls/task | actions/call | model s/call | out tok/call | in tok/call | cached | wait s/action | $/task | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-ref | 48 | 48 | 100% | 96.9 | 89.0 | 10.6 | 2.20 | 4.17 | 339 | 18667 | 83% | 1.91 | 0.137 | 0 |
| claude-legato | 48 | 48 | 100% | 65.7 | 62.0 | 14.0 | 1.40 | 3.02 | 225 | 8603 | 71% | 0.71 | 0.126 | 0 |
| qwen-osworld | 48 | 26 | 54% | 212.5 | 116.9 | 23.2 | 3.07 | 7.90 | 549 | 25852 | 76% | 0.00 | 0.058 | 0 |
| qwen-legato | 48 | 36 | 75% | 90.0 | 76.7 | 18.0 | 0.74 | 4.28 | 343 | 12267 | 54% | 0.67 | 0.034 | 0 |

Against **claude-ref** (ratio of summed per-task mean agent time; below 1 is faster):

- **claude-legato**: 0.678 [0.604, 0.757] over 24 tasks (1.48x faster); tasks both solved every time: 0.678 over 24; success +0 pts
- **qwen-osworld**: 2.193 [1.530, 3.163] over 24 tasks (0.46x faster); tasks both solved every time: 1.501 over 10; success -46 pts
- **qwen-legato**: 0.929 [0.759, 1.167] over 24 tasks (1.08x faster); tasks both solved every time: 0.743 over 15; success -25 pts

| task | claude-ref passed | claude-ref s | claude-legato passed | claude-legato s | qwen-osworld passed | qwen-osworld s | qwen-legato passed | qwen-legato s |
|---|---|---|---|---|---|---|---|---|
| office-00-copy_total | 2/2 | 87.6 | 2/2 | 87.5 | 2/2 | 73.4 | 2/2 | 53.8 |
| office-01-copy_total | 2/2 | 106.0 | 2/2 | 65.0 | 1/2 | 584.7 | 2/2 | 50.1 |
| office-02-copy_title | 2/2 | 80.6 | 2/2 | 45.8 | 2/2 | 110.2 | 2/2 | 60.0 |
| office-03-copy_title | 2/2 | 57.9 | 2/2 | 66.9 | 0/2 | 102.1 | 2/2 | 59.4 |
| office-04-top_region | 2/2 | 73.1 | 2/2 | 66.0 | 0/2 | 414.6 | 2/2 | 59.3 |
| office-05-top_region | 2/2 | 72.4 | 2/2 | 67.5 | 2/2 | 112.6 | 0/2 | 154.3 |
| office-06-top3_list | 2/2 | 65.0 | 2/2 | 47.3 | 1/2 | 170.8 | 1/2 | 225.5 |
| office-07-compute_total | 2/2 | 99.9 | 2/2 | 47.5 | 1/2 | 179.8 | 2/2 | 64.4 |
| office-08-compute_total | 2/2 | 100.8 | 2/2 | 82.1 | 2/2 | 96.7 | 1/2 | 78.1 |
| office-09-region_table | 2/2 | 97.8 | 2/2 | 97.7 | 0/2 | 134.1 | 0/2 | 131.9 |
| office-10-next_steps | 2/2 | 128.5 | 2/2 | 68.6 | 1/2 | 140.8 | 1/2 | 82.2 |
| office-11-paste_chart | 2/2 | 153.4 | 2/2 | 81.4 | 2/2 | 229.7 | 0/2 | 95.5 |
| office-12-copy_total | 2/2 | 78.5 | 2/2 | 61.6 | 2/2 | 106.6 | 2/2 | 49.9 |
| office-13-copy_total | 2/2 | 89.8 | 2/2 | 65.9 | 0/2 | 90.3 | 2/2 | 68.5 |
| office-14-copy_title | 2/2 | 70.5 | 2/2 | 38.5 | 2/2 | 108.9 | 2/2 | 76.8 |
| office-15-copy_title | 2/2 | 124.0 | 2/2 | 39.3 | 2/2 | 71.8 | 2/2 | 46.8 |
| office-16-top_region | 2/2 | 68.7 | 2/2 | 57.2 | 1/2 | 904.8 | 1/2 | 138.6 |
| office-17-top_region | 2/2 | 87.6 | 2/2 | 51.2 | 1/2 | 112.6 | 2/2 | 94.3 |
| office-18-top3_list | 2/2 | 89.0 | 2/2 | 55.4 | 0/2 | 168.6 | 2/2 | 52.5 |
| office-19-compute_total | 2/2 | 91.5 | 2/2 | 45.5 | 0/2 | 202.1 | 2/2 | 76.2 |
| office-20-compute_total | 2/2 | 90.2 | 2/2 | 54.7 | 2/2 | 194.0 | 2/2 | 102.0 |
| office-21-region_table | 2/2 | 139.1 | 2/2 | 86.0 | 0/2 | 193.0 | 1/2 | 116.2 |
| office-22-next_steps | 2/2 | 138.6 | 2/2 | 116.0 | 0/2 | 210.9 | 1/2 | 126.7 |
| office-23-paste_chart | 2/2 | 134.5 | 2/2 | 81.1 | 2/2 | 386.0 | 2/2 | 96.9 |
