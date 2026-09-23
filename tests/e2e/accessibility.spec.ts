import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

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
	const navigator = page.getByRole("navigation", { name: "Course" });
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await expectNoAccessibilityViolations(page, "Workspace Launcher");

	await page.getByRole("button", { name: "Runtime diagnostics" }).click();
	await expect(
		page.getByRole("dialog", { name: "Runtime diagnostics" }),
	).toBeVisible();
	await expect(page.getByText("Runtime paths")).toBeVisible();
	await expectNoAccessibilityViolations(page, "Runtime diagnostics");
	await page.keyboard.press("Escape");

	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Start with your material" }),
	).toBeVisible();
	await clearSmokeModelCatalog(page);
	await expect(
		page.getByRole("heading", { name: "Start with your material" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Start surface");

	await page.getByRole("button", { name: "Add a model to chat" }).click();
	const modelDialog = page.getByRole("dialog", { name: "Add a model" });
	await expect(
		modelDialog.getByText(
			/Saving sends this key to https:\/\/openrouter\.ai\/api\/v1/,
		),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Provider and model setup");

	await modelDialog.getByLabel("API key").fill("deterministic-test-key");
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
	await modelDialog
		.getByRole("button", { name: "Save Provider Account" })
		.click();
	await expect(modelDialog.getByRole("alert")).toHaveText(
		"The Provider Account could not be verified.",
	);
	await expectNoAccessibilityViolations(page, "Provider error recovery");

	// The form keeps its safe inputs and can be retried after a reported failure.
	await modelDialog
		.getByRole("button", { name: "Save Provider Account" })
		.click();
	await modelDialog.getByLabel("Preset name").fill("Planning model");
	await modelDialog.getByLabel("Model ID").fill("deterministic/course-agent");
	await modelDialog.getByRole("button", { name: "Save Model Preset" }).click();
	await expect(modelDialog).toBeHidden();

	const composer = page.getByLabel("Message the Course Agent");
	await expect(composer).toBeFocused();
	await composer.press("Shift+Enter");
	await expect(composer).toHaveValue("\n");
	await composer.fill("Create a practical causal inference Course.");
	await composer.press("Enter");
	await expect(page.locator("#agent-run-status")).toHaveText(
		"Course Agent finished.",
	);
	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(
		page,
		"Course Plan beside conversation",
	);

	// The transcript is keyboard scrollable.
	const transcript = page.getByRole("region", {
		name: "Course Agent conversation",
	});
	await transcript.evaluate((element) => {
		element.style.flex = "0 0 40px";
		element.style.height = "40px";
		element.style.maxHeight = "40px";
		element.scrollTop = 0;
	});
	await transcript.focus();
	await transcript.press("End");
	await expect
		.poll(() => transcript.evaluate((element) => element.scrollTop))
		.toBeGreaterThan(0);
	await transcript.evaluate((element) => {
		element.removeAttribute("style");
	});

	const course = (await (await page.request.get("/api/course")).json()) as {
		lectures: Array<{ id: string; title: string }>;
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
	await page.reload();
	const lectureNav = navigator.getByRole("button", {
		name: new RegExp(firstLecture.title),
	});
	await lectureNav.focus();
	await lectureNav.press("Enter");
	await expect(lectureNav).toHaveAttribute("aria-current", "page");
	await expect(
		page.getByRole("heading", { name: firstLecture.title, level: 1 }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Lecture canvas");

	const firstSlide = page
		.getByRole("navigation", { name: "Slides" })
		.getByRole("button", { name: /^Slide 1:/ });
	await firstSlide.focus();
	await firstSlide.press("Enter");
	await expect(firstSlide).toHaveAttribute("aria-current", "true");
	await expect(page.locator(".context-chip")).toContainText(
		"Introduction to causal inference",
	);
	const editSlide = page.getByRole("button", { name: "Edit Slide" });
	await editSlide.focus();
	await editSlide.press("Enter");
	await expect(page.getByLabel("Slide title")).toBeFocused();
	await expectNoAccessibilityViolations(page, "Slide editor");
	await page.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect(page.getByLabel("Slide title")).toHaveCount(0);

	await page.getByRole("button", { name: /^Template:/ }).click();
	await expect(page.getByRole("dialog", { name: /template/i })).toBeVisible();
	await expectNoAccessibilityViolations(page, "Template gallery");
	await page.keyboard.press("Escape");

	// Sources: each scope passes automated checks and recovers from failures.
	await navigator.getByRole("button", { name: /Sources/ }).click();
	await expect(
		page.getByRole("heading", { name: "Sources", exact: true }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Sources: this course");
	await page.getByRole("tab", { name: /Library/ }).click();
	await expectNoAccessibilityViolations(page, "Sources: Library");
	await page.getByRole("tab", { name: "Discover" }).click();
	await expectNoAccessibilityViolations(page, "Sources: Discover");
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
							body: JSON.stringify({
								detail: "Discovery is temporarily unavailable.",
							}),
						}
					: { status: 200, contentType: "application/json", body: "[]" },
			);
		},
		{ times: 2 },
	);
	const discoverySearch = page.getByRole("searchbox", {
		name: "Search for papers and repositories",
	});
	await discoverySearch.fill("causal inference");
	await page.getByRole("button", { name: "Search", exact: true }).click();
	await expect(page.getByRole("alert")).toHaveText(
		"Discovery is temporarily unavailable.",
	);
	await expect(discoverySearch).toHaveValue("causal inference");
	await page.getByRole("button", { name: "Search", exact: true }).click();
	await expect(page.getByRole("alert")).toHaveCount(0);
	await expect(
		page.getByText("Nothing found. Try broader or different words."),
	).toBeVisible();
	// Switching scopes never sends a query to another scope.
	await page.getByRole("tab", { name: /This course/ }).click();
	await page.getByRole("tab", { name: "Discover" }).click();
	expect(discoveryAttempts).toBe(2);
	// Each scope keeps its own query and results across switching (UX-03).
	await expect(discoverySearch).toHaveValue("causal inference");
	await expect(
		page.getByText("Nothing found. Try broader or different words."),
	).toBeVisible();

	// Settings sections, including search index recovery.
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	const sections = settings.getByRole("navigation", {
		name: "Settings sections",
	});
	await expectNoAccessibilityViolations(page, "Settings: Models");
	await sections.getByRole("button", { name: "Templates" }).click();
	await expectNoAccessibilityViolations(page, "Settings: Templates");
	await sections.getByRole("button", { name: "Workspace" }).click();
	await expect(
		settings.getByRole("region", { name: "Course files" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Settings: Workspace");
	await sections.getByRole("button", { name: "Diagnostics" }).click();
	await expect(settings.getByText("Presentation renderer")).toBeVisible();
	await expectNoAccessibilityViolations(page, "Settings: Diagnostics");
	await page.route(
		"**/api/resources/cache",
		async (route) => {
			await route.fulfill({
				status: 503,
				contentType: "application/json",
				body: JSON.stringify({
					detail: "Search index is temporarily unavailable.",
				}),
			});
		},
		{ times: 1 },
	);
	await settings.getByRole("button", { name: "Rebuild search index" }).click();
	await expect(settings.getByRole("alert")).toHaveText(
		"Search index is temporarily unavailable.",
	);
	await settings.getByRole("button", { name: "Rebuild search index" }).click();
	await expect(settings.getByRole("alert")).toHaveCount(0);
	await page.keyboard.press("Escape");
	await expect(settings).toBeHidden();

	await navigator.getByRole("button", { name: /History/ }).click();
	await expect(
		page.getByRole("heading", { name: "History", exact: true }),
	).toBeVisible();
	await expect(page.getByText("Reading history…")).toHaveCount(0);
	await expectNoAccessibilityViolations(page, "History");

	await page.getByRole("button", { name: "Publish release" }).first().click();
	await expect(
		page.getByRole("heading", { name: "Publish a Course Release" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Release preparation");

	// The Course Plan canvas and its details dialog.
	await navigator.getByRole("button", { name: "Course Plan" }).click();
	await page.getByRole("button", { name: "Edit details" }).click();
	await expect(
		page.getByRole("dialog", { name: "Edit course details" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Course details");
	await page.keyboard.press("Escape");
});
