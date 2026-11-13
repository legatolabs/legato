# H2H-2: Legato vs reference harnesses (anthropic/claude-sonnet-5.5, qwen/qwen3.8-27b)

Legato Office Bench on real Ubuntu desktops (LibreOffice Calc and Impress), 2 attempts per task per arm, all arms at once on one host, every model through OpenRouter. Agent time runs from the first screenshot to the model's last turn (no reset, no grading).

- **qwen-osworld**: OSWorld's QwenAgent scaffold (mm_agents/qwen: keeps 20 screenshots, drops 10 at a time) with OSWorld's runner default of 0 s wait
- **qwen-legato**: legato.Harness on the same open model

| arm | rollouts | passed | success | agent s (mean) | median | calls/task | actions/call | model s/call | out tok/call | in tok/call | cached | wait s/action | $/task | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen-osworld | 48 | 27 | 56% | 121.2 | 112.8 | 23.0 | 1.35 | 4.76 | 169 | 24039 | 77% | 0.00 | 0.028 | 0 |
| qwen-legato | 48 | 35 | 73% | 115.5 | 100.4 | 19.7 | 0.87 | 5.06 | 257 | 12695 | 55% | 0.63 | 0.025 | 0 |

Against **qwen-osworld** (ratio of summed per-task mean agent time; below 1 is faster):

- **qwen-legato**: 0.953 [0.798, 1.134] over 24 tasks (1.05x faster); tasks both solved every time: 0.760 over 5; success +17 pts

| task | qwen-osworld passed | qwen-osworld s | qwen-legato passed | qwen-legato s |
|---|---|---|---|---|
| office-00-copy_total | 1/2 | 88.7 | 2/2 | 80.0 |
| office-01-copy_total | 1/2 | 105.9 | 2/2 | 72.2 |
| office-02-copy_title | 2/2 | 120.2 | 2/2 | 70.0 |
| office-03-copy_title | 0/2 | 149.7 | 2/2 | 82.0 |
| office-04-top_region | 2/2 | 122.6 | 1/2 | 100.6 |
| office-05-top_region | 2/2 | 133.5 | 2/2 | 54.3 |
| office-06-top3_list | 2/2 | 127.2 | 1/2 | 72.4 |
| office-07-compute_total | 2/2 | 93.6 | 2/2 | 96.8 |
| office-08-compute_total | 1/2 | 106.4 | 2/2 | 212.7 |
| office-09-region_table | 0/2 | 142.8 | 0/2 | 249.1 |
| office-10-next_steps | 1/2 | 110.4 | 0/2 | 112.8 |
| office-11-paste_chart | 1/2 | 114.0 | 2/2 | 108.5 |
| office-12-copy_total | 0/2 | 166.2 | 2/2 | 79.6 |
| office-13-copy_total | 0/2 | 93.2 | 2/2 | 61.5 |
| office-14-copy_title | 2/2 | 60.6 | 2/2 | 103.0 |
| office-15-copy_title | 0/2 | 87.6 | 2/2 | 137.1 |
| office-16-top_region | 2/2 | 146.5 | 1/2 | 170.0 |
| office-17-top_region | 0/2 | 104.2 | 2/2 | 122.8 |
| office-18-top3_list | 2/2 | 125.2 | 1/2 | 180.0 |
| office-19-compute_total | 2/2 | 128.8 | 2/2 | 84.1 |
| office-20-compute_total | 2/2 | 203.8 | 1/2 | 146.4 |
| office-21-region_table | 0/2 | 106.7 | 1/2 | 110.1 |
| office-22-next_steps | 0/2 | 139.9 | 1/2 | 104.5 |
| office-23-paste_chart | 2/2 | 131.5 | 0/2 | 161.5 |
