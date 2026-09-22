/**
 * vite.config.ts
 *
 * Dev server proxies /api to the FastAPI backend (http://localhost:8000)
 * so the frontend can call relative paths (e.g. fetch("/api/model"))
 * without hardcoding a host, per SDS Section 10.3 (Frontend <-> Backend
 * synchronization). /geometry is proxied the same way so GeometryRef.url
 * values (e.g. "/geometry/<uuid>.stl", served by main.py's StaticFiles
 * mount) are fetchable by THREE.STLLoader without hardcoding a host.
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
      "/geometry": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
