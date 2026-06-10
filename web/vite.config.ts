/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server. The SPA calls its own origin (see src/api/client.ts), so forward
// the API surface to the FastAPI backend — `npm run dev` then ties figures out
// against a locally-running engine. Point at a different backend with
// ATTEST_DEV_API. (A pure static build with no backend still works: those calls
// reject and the store falls back to offline, honestly-untraced detection.)
//
// `base` stays at Vite's default "/" so build_spa.py's asset inliner keeps
// matching "/assets/…" in the served bundle.
const API_TARGET = process.env.ATTEST_DEV_API || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/tenants": { target: API_TARGET, changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
