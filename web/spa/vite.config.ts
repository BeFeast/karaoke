import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The SPA is served by FastAPI under /app (StaticFiles mount), so all asset
// URLs must resolve relative to /app/. In dev, proxy API calls to the running
// coordinator so same-origin fetch("/config" | "/jobs" | "/me") just works.
const defaultRuntimeTarget = "http://127.0.0.1:13140";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const runtimeTarget =
    env.KARAOKE_SPA_PROXY_TARGET ||
    env.VITE_KARAOKE_PROXY_TARGET ||
    defaultRuntimeTarget;
  const apiProxy = {
    target: runtimeTarget,
    changeOrigin: true,
  };

  return {
    base: "/app/",
    plugins: [react()],
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
    server: {
      proxy: {
        "/config": apiProxy,
        "/me": apiProxy,
        "/jobs": apiProxy,
        "/health": apiProxy,
        "/share": apiProxy,
        "/tokens": apiProxy,
        "/ws": { ...apiProxy, ws: true },
      },
    },
    preview: {
      proxy: {
        "/config": apiProxy,
        "/me": apiProxy,
        "/jobs": apiProxy,
        "/health": apiProxy,
        "/share": apiProxy,
        "/tokens": apiProxy,
        "/ws": { ...apiProxy, ws: true },
      },
    },
  };
});
