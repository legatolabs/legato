# A small Linux desktop for Legato: an X display, a window manager, a terminal and a browser, plus the three tools
# Legato's in-sandbox helper uses (python3, xdotool, scrot). A terminal is open at start; right-click the desktop for
# the menu.
#
#   docker run -d --name desk ghcr.io/legatolabs/desktop
#   legato mcp --sandbox docker          # or: legato run "..." --sandbox docker
#
# Add your own apps in a Dockerfile that starts FROM this image (switch to USER root to apt-get install).
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:0 RESOLUTION=1280x800x24
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11-utils xdotool scrot python3 fluxbox feh xterm firefox-esr \
        fonts-dejavu-core ca-certificates dbus-x11 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash user
USER user
WORKDIR /home/user
RUN mkdir -p .fluxbox && echo "background: none" > .fluxbox/overlay
LABEL org.opencontainers.image.source="https://github.com/legatolabs/legato" \
      org.opencontainers.image.description="A small Linux desktop for computer-use agents, ready for Legato" \
      org.opencontainers.image.licenses="Apache-2.0"
CMD ["sh", "-c", "Xvfb :0 -screen 0 \"$RESOLUTION\" -nolisten tcp & sleep 1; fluxbox >/dev/null 2>&1 & sleep 0.5; xterm -geometry 100x28+24+24 >/dev/null 2>&1 & exec sleep infinity"]
