import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
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
	await page
		.getByRole("button", { name: "Usage and where messages are sent" })
		.click();
	await expect(
		page.getByText(
			/When you send a message, it, any Source excerpts, and any images/,
		),
	).toBeVisible();
	await expect(
		page.getByText("This conversation has not used any tokens yet."),
	).toBeVisible();

	// The first fake-agent turn creates the two-Lecture Course Plan through chat.
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	await expect(page.getByText("Estimated cost")).toBeVisible();

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

	// Sources: discovery disclosure, a PDF read quickly on this device, preview, and inclusion.
	await navigator.getByRole("button", { name: /Sources/ }).click();
	await expect(
		page.getByRole("heading", { name: "Sources", exact: true }),
	).toBeVisible();
	await page.getByRole("tab", { name: "Discover" }).click();
	await expect(
		page.getByText(/Searching sends only your query to these public services/),
	).toBeVisible();
	await page.getByRole("tab", { name: /Library/ }).click();
	const libraryUpload = page.getByLabel("Add files to Library");
	await libraryUpload.setInputFiles(
		resolve("packages/harness/tests/fixtures/resources/teaching.pdf"),
	);
	// No model download stands between a PDF and the Library.
	const pdfRow = page
		.getByRole("list", { name: "Library" })
		.getByRole("listitem")
		.filter({ hasText: "teaching.pdf" });
	await expect(pdfRow.getByText("Read by Quick local")).toBeVisible();
	await expect(pdfRow.getByText("Searchable")).toBeVisible();
	await expect(pdfRow.getByText("May be incomplete")).toHaveCount(0);

	// Documents settings explain the optional local extra and ask before sending files.
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: "Documents" })
		.click();
	await expect(settings.getByText(/course-harness\[docling\]/)).toBeVisible();
	await settings
		.getByLabel("Offer to reprocess it with")
		.selectOption("mistral-ocr");
	await expect(
		settings.getByRole("checkbox", {
			name: "Reprocess it automatically, without asking",
		}),
	).toBeDisabled();
	await settings
		.getByRole("button", { name: "Allow sending documents to Mistral OCR" })
		.click();
	const consent = page.getByRole("dialog", {
		name: "Send documents to Mistral OCR?",
	});
	await expect(
		consent.getByText(/The whole file leaves this computer/),
	).toBeVisible();
	await consent.getByRole("button", { name: "Allow" }).click();
	await expect(consent).toBeHidden();
	await expect(
		settings.getByRole("button", { name: "Stop sending" }),
	).toBeVisible();
	await settings.getByRole("button", { name: "Stop sending" }).click();
	await expect(
		settings.getByRole("button", { name: "Stop sending" }),
	).toHaveCount(0);
	await settings.getByRole("button", { name: "Close" }).click();

	await libraryUpload.setInputFiles(
		resolve("packages/harness/tests/fixtures/search/chapter_causal.md"),
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

	// The fake agent writes a cited Quiz; the Lecture canvas shows its answer key read-only.
	await composer.fill(
		`Write a Quiz for lecture ${firstLecture.id} from source ${source.id}`,
	);
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByText(
			"I wrote a Quiz with an Explanation for every wrong Option.",
		),
	).toBeVisible();
	await page
		.getByRole("list", { name: "Changes from this reply" })
		.last()
		.getByRole("button", { name: /Quiz · Open/ })
		.click();
	await expect(
		page.getByRole("tab", { name: /Quiz/, selected: true }),
	).toBeVisible();
	const quizView = page.getByRole("region", { name: "Quiz" });
	await expect(
		quizView.getByRole("heading", { name: "Question 1" }),
	).toBeVisible();
	// Markdown renders as formatting, not as asterisks.
	await expect(quizView.locator(".quiz-prompt strong").first()).toHaveText(
		"single person's",
	);
	const firstOptions = quizView.getByRole("list", {
		name: "Options for Question 1",
	});
	await expect(firstOptions.locator(".quiz-option")).toHaveCount(3);
	await expect(firstOptions.locator(".quiz-option.is-correct")).toContainText(
		"Only one potential outcome is ever observed for each person",
	);
	await expect(
		firstOptions.getByText(/Noise is not the obstacle/),
	).toBeVisible();
	await expect(
		quizView
			.getByRole("list", { name: "Options for Question 2" })
			.locator(".quiz-option.is-correct"),
	).toContainText("The assumptions about what causes what");
	// A Quiz Citation opens its Evidence and returns to the Quiz.
	await firstOptions.locator(".citation-chip").first().click();
	await expect(page.getByRole("heading", { name: source.label })).toBeVisible();
	await page.getByRole("button", { name: "Back to Quiz" }).click();
	await expect(
		page.getByRole("tab", { name: /Quiz/, selected: true }),
	).toBeVisible();

	// Exercises lists the Lectures with Quizzes and opens one.
	await navigator.getByRole("button", { name: /Exercises/ }).click();
	await expect(
		page.getByRole("heading", { name: "Exercises", level: 1 }),
	).toBeVisible();
	const quizList = page.getByRole("list", { name: "Quizzes" });
	await expect(quizList.getByRole("listitem")).toHaveCount(1);
	await expect(quizList).toContainText(firstLecture.title);
	await expect(quizList).toContainText("2 Questions");
	await expect(
		page.getByRole("list", { name: "Lectures without a Quiz" }),
	).toContainText(secondLecture.title);
	await quizList
		.getByRole("button", { name: `Open the Quiz for ${firstLecture.title}` })
		.click();
	await expect(
		quizView.getByRole("heading", { name: "Question 2" }),
	).toBeVisible();
	await page.getByRole("tab", { name: /Slides/ }).click();
	await expect(
		filmstrip.getByRole("button", { name: /^Slide \d+:/ }),
	).toHaveCount(2);
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
			.setInputFiles(
				resolve(
					"packages/harness/tests/fixtures/templates/python-pptx-test.pptx",
				),
			),
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
	// The original file is one switch away on the same page.
	await expect(
		page.getByRole("tab", { name: "Extracted text", selected: true }),
	).toBeVisible();
	await page.getByRole("tab", { name: "Original file" }).click();
	await expect(page.locator(".reader-line.is-cited")).toHaveCount(0);
	await expect(page.getByText(/potential outcomes/).first()).toBeVisible();
	await page.getByRole("tab", { name: "Extracted text" }).click();
	await expect(page.locator(".reader-line.is-cited").first()).toBeVisible();
	// The way back stays in view however far the text scrolls.
	await page
		.locator(".canvas-scroll")
		.evaluate((scroller) => scroller.scrollTo(0, scroller.scrollHeight));
	await expect(
		page.getByRole("button", { name: "Back to Slides" }),
	).toBeInViewport();
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

	// The header says what Publish and Sync would do, without visiting History.
	const sharing = page.locator(".canvas-bar .sharing-controls");
	await expect(sharing.getByText("Not released yet")).toBeVisible();
	const unsetSync = sharing.getByRole("button", {
		name: "Sync: connect a remote first",
	});
	await expect(unsetSync).toBeVisible();
	// Sync without a remote opens the remote setup.
	await unsetSync.click();
	const setup = page.getByRole("dialog", {
		name: /^Connect a (GitHub repository|Git remote)$/,
	});
	await expect(setup.getByText("Checking GitHub CLI…")).toHaveCount(0);
	const existingInSetup = setup.getByLabel("Use a repository you already have");
	if (await existingInSetup.isVisible()) await existingInSetup.check();
	await expect(setup.getByLabel("Repository address")).toBeVisible();
	await setup.getByRole("button", { name: "Cancel" }).click();
	await expect(setup).toBeHidden();

	// Publish only the first Lecture and its PowerPoint; the second remains planned.
	await sharing.getByRole("button", { name: "Publish", exact: true }).click();
	const publish = page.getByRole("dialog", { name: "Publish a release" });
	await expect(publish.getByLabel("Name")).toHaveValue(
		/^Release 1 · \d{1,2} \w{3} \d{4}$/,
	);
	await expect(publish.getByLabel("What changed")).toHaveValue(
		/Review cited Presentation/,
	);
	await publish.getByText(/^Includes 2 Lectures of 2/).click();
	await publish.getByRole("checkbox", { name: secondLecture.title }).uncheck();
	const powerPoint = publish.getByRole("checkbox", {
		name: /PowerPoint · 2 Slides/,
	});
	await expect(powerPoint).toBeChecked();
	await expect(
		publish.getByText("Includes 1 Lecture of 2 · 1 PowerPoint file"),
	).toBeVisible();
	// The uploaded Source has no public locator: a note that does not block publishing.
	await expect(
		publish.getByText(
			"Source “chapter_causal.md” has no public link, so its Citations show only their label in a Classroom.",
		),
	).toBeVisible();
	await expect(
		publish.getByText("Note · does not block publishing"),
	).toBeVisible();
	await publish.getByLabel("Name").fill("Causal Foundations Preview");

	// Push to a local bare repository, which the smoke server accepts for development.
	const bareRemote = join(
		mkdtempSync(join(tmpdir(), "course-remote-")),
		"course.git",
	);
	execFileSync("git", ["init", "--quiet", "--bare", bareRemote]);
	const share = publish.getByRole("checkbox", { name: "Push to a Git remote" });
	await expect(share).not.toBeChecked();
	await share.check();
	await expect(publish.getByText("Checking GitHub CLI…")).toHaveCount(0);
	const existing = publish.getByLabel("Use a repository you already have");
	if (await existing.isVisible()) await existing.check();
	await publish.getByLabel("Repository address").fill(bareRemote);
	await publish.getByRole("button", { name: "Save" }).click();
	await expect(
		publish.getByRole("checkbox", { name: `Push to ${bareRemote}` }),
	).toBeChecked();
	await publish.getByRole("button", { name: "Publish", exact: true }).click();
	await expect(
		publish.getByRole("heading", {
			name: "Published Causal Foundations Preview",
		}),
	).toBeVisible();
	await expect(
		publish.getByText(/Classrooms attached to this repository pick it up/),
	).toBeVisible();
	await publish.getByRole("button", { name: "Done" }).click();

	await expect(sharing.getByText("Released · up to date")).toBeVisible();
	await expect(
		sharing.getByRole("button", { name: `In sync with ${bareRemote}` }),
	).toBeDisabled();
	expect(
		execFileSync("git", [
			"--git-dir",
			bareRemote,
			"for-each-ref",
			"--format=%(refname)",
		])
			.toString()
			.trim()
			.split("\n"),
	).toEqual([
		"refs/course-harness/release-files/causal-foundations-preview",
		"refs/heads/main",
		"refs/tags/course-release/causal-foundations-preview",
	]);

	// History keeps the record, with what changed.
	await page.getByRole("tab", { name: /Releases/ }).click();
	await page
		.getByRole("button", { name: /Causal Foundations Preview/ })
		.click();
	const releaseDetail = page.locator(".release-detail").first();
	await expect(releaseDetail).toContainText("1 lectures included");
	await expect(releaseDetail).toContainText("1 still planned");
	await expect(releaseDetail).toContainText("chapter_causal.md");
	await expect(releaseDetail).toContainText("Review cited Presentation");
});
