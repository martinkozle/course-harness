import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { clearSmokeModelCatalog } from "./smokeState";

async function expectNoAccessibilityViolations(page: Page, surface: string) {
	const results = await new AxeBuilder({ page })
		.withTags(["wcag2a", "wcag2aa", "wcag21aa"])
		.analyze();

	expect(
		results.violations,
		`${surface} has no WCAG A or AA accessibility violations`,
	).toEqual([]);
}

test("major Course Author journey surfaces meet automated and keyboard accessibility checks", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await expectNoAccessibilityViolations(page, "Workspace Launcher");

	await page.getByRole("button", { name: "Runtime diagnostics" }).click();
	await expect(
		page.getByRole("dialog", { name: "Runtime diagnostics" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Runtime diagnostics");
	await page.keyboard.press("Escape");

	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Give the course a clear shape." }),
	).toBeVisible();
	await clearSmokeModelCatalog(page);
	await expectNoAccessibilityViolations(page, "Course setup");

	await page.getByRole("button", { name: "Models", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Models" })).toBeVisible();
	await expect(
		page.getByText(
			/Saving sends this key to https:\/\/openrouter\.ai\/api\/v1/,
		),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Provider and model setup");

	await page.getByLabel("API key").fill("deterministic-test-key");
	await page.route(
		"**/api/provider-accounts",
		async (route) => {
			await route.fulfill({
				status: 422,
				contentType: "application/json",
				body: JSON.stringify({
					detail: "The Provider Account could not be verified.",
				}),
			});
		},
		{ times: 1 },
	);
	await page.getByRole("button", { name: "Save Provider Account" }).click();
	await expect(page.getByRole("alert")).toHaveText(
		"The Provider Account could not be verified.",
	);
	await expectNoAccessibilityViolations(page, "Provider error recovery");

	// The form keeps its safe inputs and can be retried after a reported failure.
	await page.getByRole("button", { name: "Save Provider Account" }).click();
	await page.getByLabel("Preset name").fill("Planning model");
	await page.getByLabel("Model ID").fill("deterministic/course-agent");
	await page.getByRole("button", { name: "Save Model Preset" }).click();

	const authoringNav = page.getByRole("button", {
		name: "Authoring",
		exact: true,
	});
	await authoringNav.focus();
	await authoringNav.press("Enter");
	await expect(authoringNav).toHaveAttribute("aria-current", "page");
	await expect(
		page.getByRole("heading", { name: "Course Agent" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Course Agent authoring");

	const composer = page.getByLabel("Message the Course Agent");
	await composer.focus();
	await composer.press("Shift+Enter");
	await expect(composer).toHaveValue("\n");
	await composer.fill("Create a practical causal inference Course.");
	await composer.press("Enter");
	await expect(page.locator("#agent-run-status")).toHaveText(
		"Course Agent finished.",
	);
	const conversation = page.locator(".chat-messages");
	await conversation.evaluate((element) => {
		element.style.flex = "0 0 40px";
		element.style.height = "40px";
		element.style.maxHeight = "40px";
		element.style.overflowY = "auto";
		element.scrollTop = 0;
	});
	await conversation.focus();
	await conversation.press("End");
	await expect
		.poll(() => conversation.evaluate((element) => element.scrollTop))
		.toBeGreaterThan(0);
	const course = (await (await page.request.get("/api/course")).json()) as {
		lectures: Array<{ id: string }>;
	};
	const firstLecture = course.lectures[0];
	expect(firstLecture).toBeDefined();
	const createPresentation = await page.request.post(
		`/api/presentations/${firstLecture.id}`,
		{
			data: {
				slides: [
					{
						layout: "title",
						title: "Introduction to causal inference",
						subtitle: "From association to intervention",
					},
				],
			},
		},
	);
	expect(createPresentation.ok()).toBe(true);
	await page.getByRole("button", { name: "Course Plan" }).click();
	await authoringNav.click();
	await expect(
		page.getByRole("heading", { name: "Presentation" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Presentation canvas");
	const firstSlide = page.locator(".slide-card").first();
	await firstSlide.focus();
	await firstSlide.press("Enter");
	const slideDialog = page.getByRole("dialog", {
		name: "Introduction to causal inference",
	});
	await expect(slideDialog).toBeVisible();
	await expect(
		slideDialog.getByRole("heading", {
			name: "Introduction to causal inference",
		}),
	).toBeFocused();
	const selectionOverlay = slideDialog.locator(".preview-content-slot").first();
	await selectionOverlay.focus();
	await expect(selectionOverlay).toBeFocused();
	await selectionOverlay.press("Enter");
	await expect(page.locator(".context-text")).toContainText(
		"Introduction to causal inference",
	);
	await expectNoAccessibilityViolations(page, "Slide detail");
	await page.keyboard.press("Escape");
	await expect(firstSlide).toBeFocused();

	await page.getByRole("button", { name: "Library" }).click();
	await expect(
		page.getByRole("heading", { name: "Resources", exact: true }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Source Library");
	await page.route(
		"**/api/resources/cache",
		async (route) => {
			await route.fulfill({
				status: 503,
				contentType: "application/json",
				body: JSON.stringify({ detail: "Search index is temporarily unavailable." }),
			});
		},
		{ times: 1 },
	);
	await page.getByRole("button", { name: "Regenerate search index" }).click();
	await expect(page.getByRole("alert")).toHaveText(
		"Search index is temporarily unavailable.",
	);
	await page.getByRole("button", { name: "Regenerate search index" }).click();
	await expect(page.getByRole("alert")).toHaveCount(0);
	const discovery = page.getByRole("region", { name: "Remote discovery" });
	let discoveryAttempts = 0;
	await page.route(
		"**/api/discovery/search",
		async (route) => {
			discoveryAttempts += 1;
			await route.fulfill(
				discoveryAttempts === 1
					? {
						status: 503,
						contentType: "application/json",
						body: JSON.stringify({ detail: "Discovery is temporarily unavailable." }),
					}
					: { status: 200, contentType: "application/json", body: "[]" },
			);
		},
		{ times: 2 },
	);
	await discovery.getByRole("searchbox", { name: "Search remote resources" }).fill("causal inference");
	await discovery.getByRole("button", { name: "Search" }).click();
	await expect(discovery.getByRole("alert")).toHaveText(
		"Discovery is temporarily unavailable.",
	);
	await expect(
		discovery.getByRole("searchbox", { name: "Search remote resources" }),
	).toHaveValue("causal inference");
	await discovery.getByRole("button", { name: "Search" }).click();
	await expect(discovery.getByRole("alert")).toHaveCount(0);
	await expect(
		discovery.getByText("No remote resources found. Try another search."),
	).toBeVisible();

	await page.getByRole("button", { name: "Templates" }).click();
	await expect(
		page.getByRole("heading", { name: "Templates", level: 1 }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Template manager");

	await page.getByRole("button", { name: "Current State" }).click();
	await expect(
		page.getByRole("heading", { name: "Current State" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Current State review");

	await page.getByRole("button", { name: "Releases" }).click();
	await expect(page.getByRole("heading", { name: "Releases" })).toBeVisible();
	await expectNoAccessibilityViolations(page, "Release preparation");
});
