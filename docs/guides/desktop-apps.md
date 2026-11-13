# Test a desktop app with your coding agent

For a web page, a browser tool such as Playwright's MCP server is often enough. For an Electron, Tauri, Qt or GTK app,
or anything outside a browser, the agent needs a real desktop. Legato runs one in a container on your machine, with
your app installed and your project mounted, and gives your coding agent the screen through MCP.

1. **An image with your app.** Start from Legato's desktop image and add what your app needs:

   ```dockerfile
   FROM ghcr.io/legatolabs/desktop
   USER root
   RUN apt-get update && apt-get install -y --no-install-recommends libgtk-3-0 libnss3 && rm -rf /var/lib/apt/lists/*
   USER user
   ```

   `docker build -t my-app-desktop .`

2. **The MCP server on that image, with your project mounted.**

   ```bash
   claude mcp add legato -e LEGATO_DOCKER_ARGS="-v $PWD:/home/user/project" -- legato mcp --sandbox docker:my-app-desktop
   ```

3. **Ask.** "Open a terminal on the Legato desktop, start ~/project/dist/my-app, and check that the settings window
   saves the theme."

- **Saving files.** The desktop's user is uid 1000. On Linux, files you mount must be writable by that uid for the app
  to save them.
- **Electron and other Chromium-based apps** usually need `--no-sandbox` inside a container.
- **Your dev server** is at `http://host.docker.internal:PORT` from inside the desktop.
- To see what the agent did afterwards, add `--record DIR` to `legato mcp` ([MCP server](../mcp.md)).
