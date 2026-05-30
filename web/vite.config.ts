/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standalone dev server. The app runs entirely on client-side data today; the
// `src/api` seam is where the FastAPI verification backend wires in later.
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
