/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standalone dev server. The app runs entirely on client-side data today; the
// `src/api` seam is where the FastAPI verification backend wires in later.
//
// `base` is "/" for dev and the `attest serve` bundle (so build_spa.py's asset
// inliner keeps matching "/assets/…"). GitHub Pages serves the app from a repo
// subpath, so the Pages workflow sets PAGES_BASE="/investor-relations-harness/"
// to make the hashed assets resolve there.
export default defineConfig({
  base: process.env.PAGES_BASE || "/",
  plugins: [react()],
  server: { host: true, port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
