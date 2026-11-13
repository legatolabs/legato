# Frame-delta KV reuse, E1: 20261102T025952Z-288

## ByteDance-Seed/UI-TARS-1.5-7B

133 steps (130 with a parseable reference action); frame 1334 tokens, context 5514; changed patches median 6.3%, mean 8.8%

exact prefill (median): whole conversation 158 ms; prefix-cached full frame 70 ms; vision encoder 93 ms per frame

| policy | recomputed tokens | KL mean | KL p95 | top-1 agree | same action | same text | frame prefill ms | vs prefix-cached | vs no cache |
|---|---|---|---|---|---|---|---|---|---|
| none | 0.0% | 0.8026 | 3.0973 | 62.4% | 26.2% | 16.5% | 20 | 3.48x | 7.79x |
| d0 | 8.8% | 0.4723 | 2.2581 | 72.9% | 46.9% | 29.3% | 23 | 3.01x | 6.73x |
| d1 | 14.6% | 0.3227 | 1.4949 | 74.4% | 53.1% | 31.6% | 25 | 2.84x | 6.36x |
| d2 | 19.8% | 0.2672 | 1.3751 | 76.7% | 56.2% | 38.3% | 26 | 2.71x | 6.06x |
| all | 100.0% | 0.0006 | 0.0019 | 99.2% | 96.2% | 90.2% | 73 | 0.96x | 2.15x |

## Qwen/Qwen2.5-VL-32B-Instruct

133 steps (133 with a parseable reference action); frame 1334 tokens, context 5514; changed patches median 6.3%, mean 8.8%

exact prefill (median): whole conversation 676 ms; prefix-cached full frame 255 ms; vision encoder 82 ms per frame

| policy | recomputed tokens | KL mean | KL p95 | top-1 agree | same action | same text | frame prefill ms | vs prefix-cached | vs no cache |
|---|---|---|---|---|---|---|---|---|---|
| none | 0.0% | 1.0976 | 4.6632 | 59.4% | 21.1% | 12.0% | 67 | 3.82x | 10.14x |
| d0 | 8.8% | 0.6029 | 2.9446 | 67.7% | 37.6% | 18.8% | 73 | 3.48x | 9.24x |
| d1 | 14.6% | 0.4023 | 2.2272 | 75.9% | 48.1% | 30.8% | 80 | 3.20x | 8.48x |
| d2 | 19.8% | 0.3547 | 1.8083 | 77.4% | 51.9% | 28.6% | 85 | 3.00x | 7.95x |
| all | 100.0% | 0.0007 | 0.0044 | 98.5% | 94.0% | 69.9% | 284 | 0.90x | 2.38x |

## Qwen/Qwen2.5-VL-7B-Instruct

133 steps (133 with a parseable reference action); frame 1334 tokens, context 5514; changed patches median 6.3%, mean 8.8%

exact prefill (median): whole conversation 158 ms; prefix-cached full frame 70 ms; vision encoder 93 ms per frame

| policy | recomputed tokens | KL mean | KL p95 | top-1 agree | same action | same text | frame prefill ms | vs prefix-cached | vs no cache |
|---|---|---|---|---|---|---|---|---|---|
| none | 0.0% | 0.4264 | 1.3385 | 66.2% | 30.8% | 29.3% | 20 | 3.49x | 7.84x |
| d0 | 8.8% | 0.2284 | 0.8512 | 73.7% | 46.6% | 39.8% | 25 | 2.78x | 6.25x |
| d1 | 14.6% | 0.1923 | 0.7233 | 76.7% | 49.6% | 43.6% | 28 | 2.48x | 5.56x |
| d2 | 19.8% | 0.1725 | 0.6665 | 78.2% | 48.9% | 43.6% | 31 | 2.28x | 5.13x |
| all | 100.0% | 0.0006 | 0.0019 | 98.5% | 94.7% | 80.5% | 73 | 0.96x | 2.15x |

