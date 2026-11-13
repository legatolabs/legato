# Contributing

- `pip install -e ".[dev]"`, then `pytest -q`. The tests need no network, no model and no sandbox. One test needs
  Xvfb and xdotool, and skips without them.
- Results on new models or sandboxes are welcome. Open an issue with:
  - the model;
  - the provider;
  - the coordinate setting;
  - `result.report()` from a few runs.
- Benchmark claims in the docs come with their method and raw data. Please keep it that way: a number without a
  range or a reproduction command does not go in.
