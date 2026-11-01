import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [react()],
	build: {
		emptyOutDir: true,
		// The Python package serves, and ships, the committed production bundle.
		outDir: "../../packages/harness/src/course_harness/static",
	},
	server: {
		proxy: {
			"/api": "http://127.0.0.1:8765",
		},
	},
});
