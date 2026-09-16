import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

type PresentationPayload = {
	profile_id: string;
	profile_version: number;
	slide_width: number;
	slide_height: number;
	renderer: { available: boolean; name: string; detail: string };
	render_key: string;
	slides: Array<{
		slide_id: string;
		layout: string;
		render_key: string;
		background_url: null;
		thumbnail_url: null;
		slots: Record<
			string,
			{
				left: number;
				top: number;
				width: number;
				height: number;
				font_family: string;
				font_size: number;
				bold: boolean;
				alignment: "left";
			}
		>;
	}>;
};

function previewFor(
	presentation: { slides: Array<{ id: string; layout: string }> },
	profileId: string,
	profileVersion: number,
): PresentationPayload {
	return {
		profile_id: profileId,
		profile_version: profileVersion,
		slide_width: 12_192_000,
		slide_height: 6_858_000,
		renderer: {
			available: false,
			name: "deterministic-browser-fixture",
			detail: "Semantic previews are available for this deterministic journey.",
		},
		render_key: "stable-browser-fixture",
		slides: presentation.slides.map((slide) => ({
			slide_id: slide.id,
			layout: slide.layout,
			render_key: `render-${slide.id}`,
			background_url: null,
			thumbnail_url: null,
			slots: {
				title: {
					left: 0.08,
					top: 0.08,
					width: 0.84,
					height: 0.2,
					font_family: "Aptos",
					font_size: 30,
					bold: true,
					alignment: "left",
				},
			},
		})),
	};
}

test("Course Author completes a deterministic Course-to-Release journey", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();

	// Workspace creation and provider setup use the smoke server's local picker and validators.
	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Give the course a clear shape." }),
	).toBeVisible();
	await page.getByRole("button", { name: "Models", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Models" })).toBeVisible();
	await page.getByLabel("API key").fill("deterministic-test-key");
	await page.getByRole("button", { name: "Save Provider Account" }).click();
	await expect(page.getByLabel("Preset name")).toBeVisible();
	await page.getByLabel("Preset name").fill("Planning model");
	await page.getByLabel("Model ID").fill("deterministic/course-agent");
	await page.getByRole("button", { name: "Save Model Preset" }).click();
	await expect(page.getByText("Planning model")).toBeVisible();

	// The first fake-agent turn creates the two-Lecture Course Plan through chat.
	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	await page
		.getByLabel("Message the Course Agent")
		.fill(
			"Create a practical causal inference Course for applied researchers.",
		);
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();

	const courseResponse = await page.request.get("/api/course");
	expect(courseResponse.ok()).toBe(true);
	const course = (await courseResponse.json()) as {
		lectures: Array<{
			id: string;
			title: string;
			presentation_id: string | null;
		}>;
	};
	const [firstLecture, secondLecture] = course.lectures;
	expect(firstLecture).toBeDefined();
	expect(secondLecture).toBeDefined();

	// Upload, process, admit, search, and read a checked-in Source through Library UI.
	await page.getByRole("button", { name: "Library" }).click();
	await expect(
		page.getByRole("heading", { name: "Resources", exact: true }),
	).toBeVisible();
	await page
		.locator('input[type="file"]')
		.first()
		.setInputFiles(resolve("tests/fixtures/search/chapter_causal.md"));
	await expect(
		page.getByText("chapter_causal.md", { exact: true }),
	).toBeVisible();
	await expect(page.getByText("Ready", { exact: true })).toBeVisible();
	await expect(page.getByText("Indexed", { exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Use as course material" }).click();
	await expect(page.getByText("Admitted", { exact: true })).toBeVisible();

	const sourcesResponse = await page.request.get("/api/sources");
	expect(sourcesResponse.ok()).toBe(true);
	const sources = (await sourcesResponse.json()) as Array<{
		id: string;
		label: string;
	}>;
	expect(sources).toHaveLength(1);
	const source = sources[0];
	const sourceSearch = page.getByRole("region", { name: "Find in sources" });
	await sourceSearch.getByLabel("Search source content").fill("counterfactual");
	await sourceSearch
		.getByRole("button", { name: "Search", exact: true })
		.click();
	await expect(
		page.getByRole("button", { name: /chapter_causal\.md/ }),
	).toBeVisible();
	await page.getByRole("button", { name: /chapter_causal\.md/ }).click();
	await expect(page.locator(".source-content-body")).toContainText(
		"potential outcomes",
	);

	// Preview responses are a deterministic semantic canvas fixture; the exported PPTX remains real.
	let selectedProfileId = "_builtin-default";
	let selectedProfileVersion = 1;
	await page.route(/\/api\/presentations\/[^/]+\/preview$/, async (route) => {
		const path = new URL(route.request().url()).pathname.split("/");
		const lectureId = path[3];
		const presentationResponse = await page.request.get(
			`/api/presentations/${lectureId}`,
		);
		if (!presentationResponse.ok()) {
			await route.fallback();
			return;
		}
		await route.fulfill({
			json: previewFor(
				(await presentationResponse.json()) as {
					slides: Array<{ id: string; layout: string }>;
				},
				selectedProfileId,
				selectedProfileVersion,
			),
		});
	});

	// A second fake-agent turn emits replace_presentation with a citation to the admitted Source.
	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	await page
		.getByLabel("Message the Course Agent")
		.fill(
			`Create a cited presentation for lecture ${firstLecture.id} using source ${source.id}`,
		);
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByText(
			"I created a cited Presentation grounded in the admitted Source.",
		),
	).toBeVisible();
	await expect(page.locator("#agent-run-status")).toHaveText(
		"Course Agent finished.",
	);

	const presentationResponse = await page.request.get(
		`/api/presentations/${firstLecture.id}`,
	);
	expect(presentationResponse.ok()).toBe(true);
	const presentation = (await presentationResponse.json()) as {
		id: string;
		slides: Array<{
			id: string;
			layout: string;
			citations: Array<{ source_id: string }>;
		}>;
	};
	expect(presentation.slides).toHaveLength(2);
	expect(presentation.slides[1].citations[0].source_id).toBe(source.id);
	await expect(
		page.getByRole("heading", { name: "Presentation" }),
	).toBeVisible();
	await expect(page.locator(".slide-list > li")).toHaveCount(2);
	await expect(page.locator(".semantic-preview")).toHaveCount(2);

	// Onboard and inspect the checked-in template, then pin it to the Course.
	await page.getByRole("button", { name: "Templates" }).click();
	await expect(
		page.getByRole("heading", { name: "Templates", level: 1 }),
	).toBeVisible();
	const [templateResponse] = await Promise.all([
		page.waitForResponse((response) =>
			response.url().endsWith("/api/templates/upload"),
		),
		page
			.locator("#template-upload")
			.setInputFiles(resolve("tests/fixtures/templates/python-pptx-test.pptx")),
	]);
	expect(templateResponse.ok()).toBe(true);
	const templateBody = (await templateResponse.json()) as {
		profile: { id: string; name: string; version: number };
	};
	selectedProfileId = templateBody.profile.id;
	selectedProfileVersion = templateBody.profile.version;
	await expect(
		page.getByRole("heading", { name: templateBody.profile.name, level: 3 }),
	).toBeVisible();
	await expect(
		page.getByText("Layout mappings", { exact: true }),
	).toBeVisible();
	await page.getByRole("button", { name: "Check mappings" }).click();
	await expect(page.getByText("Mapping check", { exact: true })).toBeVisible();

	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	const templateSelect = page.getByLabel("Template profile");
	await expect(templateSelect).toBeVisible();
	await templateSelect.selectOption(templateBody.profile.id);
	await expect(templateSelect).toHaveValue(templateBody.profile.id);

	// Export the selected Presentation and verify the real response/download is a PPTX.
	const [exportResponse, download] = await Promise.all([
		page.waitForResponse(
			(response) =>
				response
					.url()
					.includes(`/api/presentations/${firstLecture.id}/export`) &&
				response.request().method() === "GET",
		),
		page.waitForEvent("download"),
		page.getByRole("button", { name: "Export PowerPoint" }).click(),
	]);
	expect(exportResponse.ok()).toBe(true);
	expect(exportResponse.headers()["content-type"]).toContain(
		"application/vnd.openxmlformats-officedocument.presentationml.presentation",
	);
	expect((await exportResponse.body()).subarray(0, 2).toString()).toBe("PK");
	expect(download.suggestedFilename()).toBe("presentation.pptx");

	// Make one deliberate UI edit, then save a clean Course Revision before Release validation.
	await page.locator(".slide-card").first().click();
	await page.getByRole("button", { name: "Edit", exact: true }).click();
	await page.getByLabel("Slide title").fill("Causal foundations, reviewed");
	await page.getByRole("button", { name: "Save", exact: true }).click();
	await page.getByRole("button", { name: "Current State" }).click();
	await expect(
		page.getByRole("heading", { name: "Current State" }),
	).toBeVisible();
	await expect(page.getByLabel("Revision summary")).toBeEnabled();
	await page.getByLabel("Revision summary").fill("Review cited Presentation");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(
		page.getByText("Review cited Presentation", { exact: true }),
	).toBeVisible();
	await expect(page.getByText("Clean", { exact: true })).toBeVisible();

	// Publish only the first Lecture and its artifact; the second remains planned.
	await page.getByRole("button", { name: "Releases" }).click();
	await expect(page.getByRole("heading", { name: "Releases" })).toBeVisible();
	const releaseLedger = page.getByRole("region", { name: "Prepare a Release" });
	const firstLectureToggle = releaseLedger
		.locator("label.release-lecture-toggle")
		.filter({ hasText: firstLecture.title });
	await firstLectureToggle.locator('input[type="checkbox"]').check();
	await releaseLedger
		.locator("fieldset.release-artifact-list")
		.first()
		.locator('input[type="checkbox"]')
		.check();
	await page.getByLabel("Release name").fill("Causal Foundations Preview");
	await page.getByLabel("Release slug").fill("causal-foundations-preview");
	await page.getByRole("button", { name: "Validate selection" }).click();
	await expect(
		page.getByText("No findings. This selection is ready to publish."),
	).toBeVisible();
	await page.getByRole("button", { name: "Publish Release" }).click();
	await expect(
		page.getByText(
			"Published Causal Foundations Preview; its immutable detail is now open.",
		),
	).toBeVisible();
	const releaseDetail = page.locator(".release-detail");
	await expect(releaseDetail).toContainText("1 lectures included");
	await expect(releaseDetail).toContainText("1 still planned");
	await expect(releaseDetail).toContainText("chapter_causal.md");
});
