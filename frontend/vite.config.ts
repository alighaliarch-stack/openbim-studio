/**
 * vite.config.ts
 *
 * Dev server proxies /api to the FastAPI backend (http://localhost:8000)
 * so the frontend can call relative paths (e.g. fetch("/api/model"))
 * without hardcoding a host, per SDS Section 10.3 (Frontend <-> Backend
 * synchronization).
 */
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
