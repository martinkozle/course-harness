import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [react()],
	build: {
		emptyOutDir: true,
		// Not committed: `bun run build` builds it for development, and the Docker image builds
		// it too. The Python package serves it (CLASSROOM_STATIC_DIR).
		outDir: "dist",
	},
	server: {
		port: 5174,
		// The API and Share Link exchange live in Python (`uv run course-classroom serve`).
		proxy: {
			"/api": "http://127.0.0.1:8000",
			"/healthz": "http://127.0.0.1:8000",
			"/i/": "http://127.0.0.1:8000",
			"/l/": "http://127.0.0.1:8000",
		},
	},
});
