/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server. The app talks to the FastAPI backend (same origin in the served
// bundle; set VITE_ATTEST_API to point the :5173 dev server at a backend
// elsewhere). The `src/api` seam is the boundary.
export default defineConfig({
  plugins: [react()],
  server: { host: true, port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
