import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [react()],
	build: {
		emptyOutDir: true,
		outDir: "dist",
	},
	server: {
		port: 5174,
		proxy: {
			"/healthz": "http://127.0.0.1:8000",
		},
	},
});
