# legato-step

Legato's in-sandbox helper, as a static Linux binary. It takes the same JSON as the Python `HELPER` in
`legato/env.py` and answers the same way: run the action's commands, wait until the screen is quiet by
`legato.settle`'s stop rule, return the settled frame as PNG.

- Each check of the screen reads the framebuffer through MIT-SHM (GetImage over the socket where the X server can't
  share memory), and hashes it in process.
- Once a frame has been seen twice, it is encoded to PNG on a second thread while the wait sits out its quiet time.

Build both CPUs into `legato/bin` (release wheels carry them): `../build.sh`, after
`rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl`. Tests: `cargo test --release` here, and
`pytest tests/test_native.py` at the repo root.

`LEGATO_HELPER_BIN=/path/to/legato-step` makes Legato use a build of your own; `LEGATO_HELPER=python` keeps the Python
helper. `LEGATO_NO_SHM=1` and `LEGATO_NO_SPECULATE=1`, set inside the sandbox, turn off the two fast paths.
