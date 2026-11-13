# ghcr.io/legatolabs/desktop

A small Linux desktop for computer-use agents: Xvfb, fluxbox, a terminal (open at start) and Firefox, plus the three
tools Legato's in-sandbox helper uses (python3, xdotool, scrot). Built for `linux/amd64` and `linux/arm64`.

```bash
claude mcp add legato -- legato mcp --sandbox docker     # Legato starts and stops containers from this image
```

- Your apps: build `FROM ghcr.io/legatolabs/desktop`, switch to `USER root` to install, then back to `USER user`, and
  use `--sandbox docker:your-image` ([guide](https://legato.computer/docs/guides/desktop-apps)).
- Screen size: `-e RESOLUTION=1280x800x24` (Legato sets it from `desktop_open`).
- Apps on the host are at `http://host.docker.internal:PORT` (Legato adds the host-gateway mapping).
