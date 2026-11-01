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
	webServer: [
		{
			command: "uv run python tests/e2e/run_smoke_server.py",
			url: "http://127.0.0.1:18765/api/health",
			env: {
				...process.env,
				UV_CACHE_DIR: ".cache/uv",
				// Lets the Publish journey share to a local bare repository.
				COURSE_HARNESS_ALLOW_LOCAL_REMOTES: "1",
			},
			reuseExistingServer: false,
			timeout: 60_000,
		},
		{
			// A Classroom serving a fixture Course, for classroom-learner.spec.ts.
			command: "uv run python tests/e2e/run_classroom_server.py",
			url: "http://127.0.0.1:18766/healthz",
			env: { ...process.env, UV_CACHE_DIR: ".cache/uv" },
			reuseExistingServer: false,
			timeout: 120_000,
		},
		{
			// An empty Classroom with an Administrator token, for classroom-admin.spec.ts.
			command: "uv run python tests/e2e/run_classroom_server.py --admin",
			url: "http://127.0.0.1:18767/healthz",
			env: { ...process.env, UV_CACHE_DIR: ".cache/uv" },
			reuseExistingServer: false,
			timeout: 120_000,
		},
	],
});
