import { defineConfig } from "@playwright/test";

export default defineConfig({
	testDir: "./tests/e2e",
	outputDir: "test-results",
	workers: 1,
	reporter: "list",
	use: {
		baseURL: "http://127.0.0.1:18765",
		launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
			? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
			: undefined,
		trace: "retain-on-failure",
		// Axe must never measure a half-faded dialog; the app honours reduced motion.
		contextOptions: { reducedMotion: "reduce" },
	},
	webServer: {
		command: "uv run python tests/e2e/run_smoke_server.py",
		url: "http://127.0.0.1:18765/api/health",
		env: { ...process.env, UV_CACHE_DIR: ".cache/uv" },
		reuseExistingServer: false,
		timeout: 60_000,
	},
});
