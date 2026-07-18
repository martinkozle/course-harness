import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: "frontend",
  plugins: [react()],
  build: {
    emptyOutDir: true,
    outDir: "../src/course_harness/static",
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
