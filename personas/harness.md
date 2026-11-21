# Harness personas (`pip install legatolabs`, `agent.run(task, desktop)`)

The people most likely to use it, most important first.

## 1. Lab / RL researcher
- **Who:** trains or evaluates computer-use models at an AI lab or a research group.
- **Wants:** thousands of graded rollouts, run fast and cheaply on their own VMs or sandboxes, with their own model.
- **Uses:** `legato rl`, `attach(shell=...)`, vLLM.
- **Cares about:** rollout speed, reproducibility, and success measured against a reference agent.
- **Gap:** proof on their own tasks: before/after numbers from a design partner.

## 2. AI product engineer
- **Who:** works at a startup adding a "do it on the computer" feature, such as an ops agent, RPA or data entry.
- **Wants:** `agent.run()` inside their backend, with their own model and E2B or Docker.
- **Cares about:** reliability, cost per task, a typed API, and timeouts so a stuck call can't hang their service.
- **Gap:** guidance for production: retries, logging, many tasks at once.

## 3. Local-first / open-model developer
- **Who:** runs Qwen or another open model on their own GPU; cares about privacy and cost.
- **Wants:** nothing leaving the machine, on their own screen or in background mode.
- **Uses:** `legato init` with the local server option.
- **Gap:** a list of models that work, with the settings for each.

## 4. QA / test engineer
- **Who:** tests a web or desktop app end to end, in CI.
- **Wants:** a script that clicks through the app in a Docker desktop on every PR.
- **Gap:** a CI example and documented pass/fail exit codes.

## 5. Automation tinkerer
- **Who:** a developer automating their own Mac chores (forms, spreadsheets, reports) from a script or cron job.
- **Wants:** `agent.py` in background mode while they keep working.
- **Gap:** apps beyond Safari, Chrome and TextEdit (Firefox is untested); drags and copy/paste in background mode.
