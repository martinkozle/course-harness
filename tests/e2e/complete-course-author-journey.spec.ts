import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

import { clearSmokeModelCatalog } from "./smokeState";

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
	const navigator = page.getByRole("navigation", { name: "Course" });
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();

	// Workspace creation and provider setup use the smoke server's local picker and validators.
	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Start with your material" }),
	).toBeVisible();
	await clearSmokeModelCatalog(page);

	// A draft survives adding a model from the composer.
	const composer = page.getByLabel("Message the Course Agent");
	await composer.fill(
		"Create a practical causal inference Course for applied researchers.",
	);
	await page.getByRole("button", { name: "Add a model to chat" }).click();
	const modelDialog = page.getByRole("dialog", { name: "Add a model" });
	await expect(modelDialog).toBeVisible();
	await expect(
		modelDialog.getByText(
			/Saving sends this key to https:\/\/openrouter\.ai\/api\/v1/,
		),
	).toBeVisible();
	await modelDialog.getByLabel("API key").fill("deterministic-test-key");
	await modelDialog
		.getByRole("button", { name: "Save Provider Account" })
		.click();
	await expect(modelDialog.getByLabel("Preset name")).toBeVisible();
	await expect(
		modelDialog.getByText(
			/Saving sends this Model ID and uses the saved credential for My OpenRouter to contact https:\/\/openrouter\.ai\/api\/v1/,
		),
	).toBeVisible();
	await modelDialog.getByLabel("Preset name").fill("Planning model");
	await modelDialog.getByLabel("Model ID").fill("deterministic/course-agent");
	await modelDialog.getByRole("button", { name: "Save Model Preset" }).click();
	await expect(modelDialog).toBeHidden();
	await expect(
		page.getByRole("button", { name: "Model: Planning model" }),
	).toBeVisible();
	await expect(composer).toHaveValue(
		"Create a practical causal inference Course for applied researchers.",
	);
	await page.getByRole("button", { name: "Where messages are sent" }).click();
	await expect(
		page.getByText(
			/When you send a message, it and any Source excerpts needed/,
		),
	).toBeVisible();

	// The first fake-agent turn creates the two-Lecture Course Plan through chat.
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

	// Sources: discovery disclosure, PDF model consent, upload, preview, and inclusion.
	await navigator.getByRole("button", { name: /Sources/ }).click();
	await expect(
		page.getByRole("heading", { name: "Sources", exact: true }),
	).toBeVisible();
	await page.getByRole("tab", { name: "Discover" }).click();
	await expect(
		page.getByText(/Searching sends only your query to these public services/),
	).toBeVisible();
	await page.getByRole("tab", { name: /Library/ }).click();
	let modelDownloadStarted = false;
	let modelDownloadCompleted = false;
	await page.route("**/api/resources/parser-models", async (route) => {
		if (route.request().method() === "POST") {
			modelDownloadStarted = true;
			await new Promise((resolve) => setTimeout(resolve, 1200));
			modelDownloadCompleted = true;
			await route.fulfill({
				json: {
					ready: true,
					downloading: false,
					stage: "Ready",
					completed_steps: 3,
					total_steps: 3,
					error: null,
				},
			});
			return;
		}
		await route.fulfill({
			json: {
				ready: modelDownloadCompleted,
				downloading: modelDownloadStarted && !modelDownloadCompleted,
				stage: modelDownloadCompleted
					? "Ready"
					: modelDownloadStarted
						? "Downloading layout models"
						: null,
				completed_steps: modelDownloadCompleted ? 3 : 0,
				total_steps: 3,
				error: null,
			},
		});
	});
	const libraryUpload = page.getByLabel("Add files to Library");
	await libraryUpload.setInputFiles(
		resolve("tests/fixtures/resources/teaching.pdf"),
	);
	const consent = page.getByRole("dialog", {
		name: "Download document processing models?",
	});
	await expect(consent).toBeVisible();
	await consent.getByRole("button", { name: "Cancel" }).click();
	await expect(consent).toBeHidden();
	// Cancelling leaves a recoverable pending item rather than a false upload.
	const uploads = page.getByRole("list", { name: "Files being added" });
	await expect(uploads).toContainText("teaching.pdf");
	await expect(uploads).toContainText("Needs PDF support");
	await page.getByRole("button", { name: "Dismiss teaching.pdf" }).click();
	await expect(uploads).toBeHidden();

	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: "Diagnostics" })
		.click();
	await settings.getByRole("button", { name: "Download PDF models" }).click();
	await consent.getByRole("button", { name: "Download models" }).click();
	await expect(
		consent.getByRole("progressbar", {
			name: "Model download stages completed",
		}),
	).toBeVisible();
	await expect(consent.getByText("Downloading layout models")).toBeVisible();
	await expect(consent).toBeHidden();
	await expect(settings.getByText("PDF support is ready")).toBeVisible();
	await settings.getByRole("button", { name: "Close" }).click();
	await page.unroute("**/api/resources/parser-models");

	await libraryUpload.setInputFiles(
		resolve("tests/fixtures/search/chapter_causal.md"),
	);
	const libraryRow = page
		.getByRole("list", { name: "Library" })
		.getByRole("listitem")
		.filter({ hasText: "chapter_causal.md" });
	await expect(libraryRow).toBeVisible();
	await expect(libraryRow.getByText("Searchable")).toBeVisible();
	await expect(
		libraryRow.getByRole("button", { name: "Add to course" }),
	).toBeVisible();
	await libraryRow
		.getByRole("button", { name: "chapter_causal.md", exact: true })
		.click();
	await expect(
		page.getByRole("heading", { name: "chapter_causal.md" }),
	).toBeVisible();
	await expect(page.getByText(/counterfactual/i).first()).toBeVisible();
	await page.getByRole("button", { name: "Back to Sources" }).click();
	await page.getByRole("tab", { name: /Library/ }).click();
	await page.route(
		"**/api/sources",
		async (route) => {
			await route.fulfill({
				status: 503,
				contentType: "application/json",
				body: JSON.stringify({
					detail: "Source admission is temporarily unavailable.",
				}),
			});
		},
		{ times: 1 },
	);
	await libraryRow.getByRole("button", { name: "Add to course" }).click();
	await expect(page.getByRole("alert")).toHaveText(
		"Source admission is temporarily unavailable.",
	);
	await libraryRow.getByRole("button", { name: "Add to course" }).click();
	await expect(libraryRow.getByText("In this course")).toBeVisible();
	await expect(page.getByRole("alert")).toHaveCount(0);

	const sourcesResponse = await page.request.get("/api/sources");
	expect(sourcesResponse.ok()).toBe(true);
	const sources = (await sourcesResponse.json()) as Array<{
		id: string;
		label: string;
	}>;
	expect(sources).toHaveLength(1);
	const source = sources[0];
	await expect(
		navigator.getByRole("button", { name: /Sources/ }),
	).toContainText("1");

	// Search sits at the top of the Sources it searches.
	await page.getByRole("tab", { name: /This course/ }).click();
	await page
		.getByRole("searchbox", { name: "Search inside this course's Sources" })
		.fill("counterfactual");
	await page.getByRole("button", { name: "Search", exact: true }).click();
	const results = page.getByRole("region", { name: "Search results" });
	await expect(
		results.getByRole("button", { name: /chapter_causal\.md/ }),
	).toBeVisible();
	await results.locator(".sources-passage").first().click();
	await expect(page.locator(".reader-line.is-cited").first()).toBeVisible();
	await expect(
		page.getByRole("list", { name: "Text of chapter_causal.md" }),
	).toContainText("potential outcomes");

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
	await composer.fill(
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

	// The reply links to the Slides it created.
	await page
		.getByRole("list", { name: "Changes from this reply" })
		.getByRole("button", {
			name: new RegExp(`Lecture 1 · ${firstLecture.title}`),
		})
		.click();
	await expect(
		page.getByRole("heading", { name: firstLecture.title, level: 1 }),
	).toBeVisible();
	const filmstrip = page.getByRole("navigation", { name: "Slides" });
	await expect(
		filmstrip.getByRole("button", { name: /^Slide \d+:/ }),
	).toHaveCount(2);
	await expect(page.locator(".slide-preview.is-approximate")).toHaveCount(3);

	// Onboard and inspect the checked-in template in Settings.
	await page.getByRole("button", { name: "Settings" }).click();
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: "Templates" })
		.click();
	const [templateResponse] = await Promise.all([
		page.waitForResponse((response) =>
			response.url().endsWith("/api/templates/upload"),
		),
		settings
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
		settings.getByRole("heading", {
			name: templateBody.profile.name,
			level: 3,
		}),
	).toBeVisible();
	await expect(
		settings.getByText("Layout mappings", { exact: true }),
	).toBeVisible();
	await settings.getByRole("button", { name: "Check mappings" }).click();
	await expect(
		settings.getByText("Mapping check", { exact: true }),
	).toBeVisible();
	await settings.getByRole("button", { name: "Close" }).click();

	// Choose the template for the Course from the Presentation toolbar.
	await page
		.getByRole("button", { name: /^Template: Default template/ })
		.click();
	const gallery = page.getByRole("dialog", { name: /template/i });
	await gallery
		.locator("label")
		.filter({ hasText: templateBody.profile.name })
		.click();
	await expect(
		gallery.getByRole("radio", { name: new RegExp(templateBody.profile.name) }),
	).toBeChecked();
	await gallery.getByRole("button", { name: "Use template" }).click();
	await expect(gallery).toBeHidden();
	await expect(
		page.getByRole("button", {
			name: new RegExp(`^Template: ${templateBody.profile.name}`),
		}),
	).toBeVisible();
	const pinned = (await (await page.request.get("/api/course")).json()) as {
		template_profile_id: string | null;
	};
	expect(pinned.template_profile_id).toBe(templateBody.profile.id);

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
	expect(download.suggestedFilename()).toBe(
		"from-association-to-intervention.pptx",
	);

	// UX-08: a long Slide title is fully editable and saves.
	const longTitle =
		"The counterfactual question, and why it cannot be observed directly for any single person in a study group";
	expect(longTitle.length).toBeGreaterThan(100);
	await filmstrip.getByRole("button", { name: /^Slide 2:/ }).click();
	await expect(page.getByText("Slide 2 of 2")).toBeVisible();
	await page.getByRole("button", { name: "Edit Slide" }).click();
	const titleField = page.getByLabel("Slide title");
	await titleField.fill(longTitle);
	const titleBox = await titleField.boundingBox();
	expect(titleBox?.width ?? 0).toBeGreaterThan(500);
	await page.getByRole("button", { name: "Save changes" }).click();
	await expect(page.getByRole("button", { name: "Save changes" })).toBeHidden();
	const edited = (await (
		await page.request.get(`/api/presentations/${firstLecture.id}`)
	).json()) as { slides: Array<{ title?: string }> };
	expect(edited.slides[1].title).toBe(longTitle);

	// UX-07: a Citation opens the exact Evidence, and returning restores the Slide.
	await page.locator(".citation-chip").first().click();
	await expect(page.getByRole("heading", { name: source.label })).toBeVisible();
	await expect(page.locator(".reader-line.is-cited").first()).toBeVisible();
	await page.getByRole("button", { name: "Back to Slides" }).click();
	await expect(page.getByText("Slide 2 of 2")).toBeVisible();
	await expect(
		filmstrip.getByRole("button", { name: /^Slide 2:/ }),
	).toHaveAttribute("aria-current", "true");

	// Save a clean Course Revision before Release validation.
	await navigator.getByRole("button", { name: /History/ }).click();
	await expect(
		page.getByRole("heading", { name: "History", exact: true }),
	).toBeVisible();
	await expect(page.getByLabel("Revision summary")).toBeEnabled();
	await page.getByLabel("Revision summary").fill("Review cited Presentation");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(
		page.getByText("Review cited Presentation", { exact: true }),
	).toBeVisible();
	await page.getByRole("tab", { name: /Current changes/ }).click();
	await expect(
		page.getByRole("heading", { name: "No changes since the last revision." }),
	).toBeVisible();

	// Publish only the first Lecture and its PowerPoint; the second remains planned.
	await page.getByRole("button", { name: "Publish release" }).first().click();
	await expect(
		page.getByRole("heading", { name: "Publish a Course Release" }),
	).toBeVisible();
	await page.getByRole("checkbox", { name: firstLecture.title }).check();
	const powerPoint = page.getByRole("checkbox", {
		name: /PowerPoint · 2 Slides/,
	});
	await expect(powerPoint).toBeChecked();
	await expect(page.getByText("1 Lecture · 1 PowerPoint file")).toBeVisible();
	await powerPoint.uncheck();
	await expect(
		page.getByText(
			"No Presentation files included — this release records the plan only.",
		),
	).toBeVisible();
	await powerPoint.check();
	await expect(page.getByText("No Presentation files included")).toHaveCount(0);
	await page.getByLabel("Release name").fill("Causal Foundations Preview");
	await expect(page.getByLabel("Release ID")).toHaveValue(
		"causal-foundations-preview",
	);
	await page.getByRole("button", { name: "Check release" }).click();
	await expect(page.getByText("No issues found.")).toBeVisible();
	await page.getByRole("button", { name: "Publish release" }).click();
	await expect(
		page.getByRole("heading", { name: "Published Causal Foundations Preview" }),
	).toBeVisible();
	const releaseDetail = page.locator(".release-detail").first();
	await expect(releaseDetail).toContainText("1 lectures included");
	await expect(releaseDetail).toContainText("1 still planned");
	await expect(releaseDetail).toContainText("chapter_causal.md");
});
