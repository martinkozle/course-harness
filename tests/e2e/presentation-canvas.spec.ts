import { expect, type Page, test } from "@playwright/test";

async function openSettings(page: Page, section: string) {
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: section })
		.click();
	return settings;
}

test("Course Author views Presentation canvas and slide outline", async ({
	page,
}) => {
	const navigator = page.getByRole("navigation", { name: "Course" });
	await page.request.post("/api/workspace/close");
	await page.goto("/");

	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Start with your material" }),
	).toBeVisible();

	const modelSettings = await openSettings(page, "Models");
	if (await modelSettings.getByLabel("API key").isVisible()) {
		await modelSettings.getByLabel("API key").fill("deterministic-test-key");
		await modelSettings
			.getByRole("button", { name: "Save Provider Account" })
			.click();
		await expect(modelSettings.getByLabel("Preset name")).toBeVisible();
	}
	if (await modelSettings.getByLabel("Preset name").isVisible()) {
		await modelSettings.getByLabel("Preset name").fill("Planning model");
		await modelSettings.getByLabel("Model ID").fill("openai/gpt-oss-20b:free");
		await modelSettings
			.getByRole("button", { name: "Save Model Preset" })
			.click();
	}
	await page.keyboard.press("Escape");
	await expect(modelSettings).toBeHidden();

	await page.route("**/api/agent", async (route) => {
		const response = await route.fetch();
		await new Promise((resolve) => setTimeout(resolve, 350));
		await route.fulfill({ response });
	});
	const composer = page.getByLabel("Message the Course Agent");
	await composer.fill(
		"Create a practical causal inference Course for applied researchers.",
	);
	await composer.press("Shift+Enter");
	await expect(composer).toHaveValue(/\n$/);
	await composer.fill(
		"Create a practical causal inference Course for applied researchers.",
	);
	await composer.press("Enter");
	await expect(page.locator("#agent-run-status")).toHaveText(
		"Course Agent is working.",
	);
	await expect(page.getByText("Working…")).toBeVisible();

	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const course = (await (await page.request.get("/api/course")).json()) as {
		lectures: { id: string; title: string }[];
	};
	const lectureId = course.lectures[0].id;
	const createPresentation = await page.request.post(
		`/api/presentations/${lectureId}`,
		{
			data: {
				slides: [
					{
						layout: "title",
						title: "Introduction to causal inference",
						subtitle: "From association to intervention",
					},
					{
						layout: "section",
						title: "Why prediction is not enough",
					},
					{
						layout: "bullets",
						title: "The intervention question",
						bullets: [
							"What changes when treatment changes?",
							"Which assumptions identify the effect?",
						],
					},
					{
						layout: "big_statement",
						title: "A different question",
						statement: "Prediction observes. Causal inference intervenes.",
					},
					...Array.from({ length: 14 }, (_, index) => ({
						layout: "section",
						title: `Supporting idea ${String(index + 5)}`,
					})),
				],
			},
		},
	);
	expect(createPresentation.ok()).toBe(true);
	const createdPresentation = (await createPresentation.json()) as {
		slides: { id: string; layout: string }[];
	};
	const previewPayload = {
		profile_id: "_builtin-default",
		profile_version: 1,
		slide_width: 12192000,
		slide_height: 6858000,
		renderer: {
			available: true,
			name: "LibreOffice",
			detail: "High-fidelity thumbnails are available.",
		},
		render_key: "stable-browser-fixture",
		slides: createdPresentation.slides.map((slide) => ({
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
	const renderedPreviewPayload = {
		...previewPayload,
		slides: previewPayload.slides.map((slide) => ({
			...slide,
			thumbnail_url:
				"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X2NDWQAAAABJRU5ErkJggg==",
		})),
	};
	let renderRequests = 0;
	let renderComplete = false;
	await page.route(
		/\/api\/presentations\/[^/]+\/preview\/render$/,
		async (route) => {
			renderRequests += 1;
			await new Promise((resolve) => setTimeout(resolve, 500));
			renderComplete = true;
			await route.fulfill({ json: renderedPreviewPayload });
		},
	);
	await page.route(/\/api\/presentations\/[^/]+\/preview$/, (route) =>
		route.fulfill({
			json: renderComplete ? renderedPreviewPayload : previewPayload,
		}),
	);

	// Template management lives in Settings and always offers the built-in template.
	const templateSettings = await openSettings(page, "Templates");
	await expect(
		templateSettings
			.getByRole("list", { name: "Templates" })
			.getByRole("button", { name: /Built-in default/ }),
	).toHaveCount(1);
	await expect(
		templateSettings.getByText(
			/The standard Office theme\. It is always available/,
		),
	).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(templateSettings).toBeHidden();

	// Opening a Lecture shows its Slides beside the conversation.
	const lectureNav = navigator.getByRole("button", {
		name: new RegExp(course.lectures[0].title),
	});
	await lectureNav.focus();
	await lectureNav.press("Enter");
	await expect(lectureNav).toHaveAttribute("aria-current", "page");
	await expect(
		page.getByRole("heading", { name: course.lectures[0].title, level: 1 }),
	).toBeVisible();
	await expect(page.getByText("18 Slides", { exact: true })).toBeVisible();
	const filmstrip = page.getByRole("navigation", { name: "Slides" });
	const filmSlides = filmstrip.getByRole("button", { name: /^Slide \d+:/ });
	await expect(filmSlides).toHaveCount(18);
	await expect(page.locator(".stage-position")).toContainText("Slide 1 of 18");
	await expect(
		page.getByText("Rendering 18 previews with LibreOffice…"),
	).toBeVisible();
	await expect(page.locator(".stage .slide-updating")).toHaveText("Updating…");
	await expect(page.locator(".stage-bar")).toHaveScreenshot(
		"presentation-preview-chrome.png",
		{ maxDiffPixelRatio: 0.01 },
	);
	await expect(page.locator(".stage-note")).toHaveCount(0);
	expect(renderRequests).toBe(1);
	await expect(page.locator(".slide-updating")).toHaveCount(0);
	await expect(page.locator(".slide-preview.is-rendered")).toHaveCount(19);

	// Keyboard selection of a Slide updates the stage and the composer context.
	const thirdSlide = filmSlides.nth(2);
	await thirdSlide.focus();
	await thirdSlide.press("Enter");
	await expect(thirdSlide).toHaveAttribute("aria-current", "true");
	await expect(page.locator(".stage-position")).toContainText("Slide 3 of 18");
	await expect(page.locator(".context-chip")).toContainText(
		"Slide 3 · The intervention question",
	);

	// The stage arrows and arrow keys step through Slides without reordering them.
	await page.getByRole("button", { name: "Next Slide" }).click();
	await expect(page.locator(".stage-position")).toContainText("Slide 4 of 18");
	await page.keyboard.press("ArrowLeft");
	await expect(page.locator(".stage-position")).toContainText("Slide 3 of 18");
	await expect(filmSlides.nth(2)).toBeFocused();
	await expect(filmSlides.nth(2)).toHaveAccessibleName(
		"Slide 3: The intervention question",
	);

	// Reordering happens on the selected thumbnail: buttons, Alt+arrow, or dragging.
	await filmstrip.getByRole("button", { name: "Move Slide earlier" }).click();
	await expect(page.locator(".stage-position")).toContainText("Slide 2 of 18");
	await expect(filmSlides.nth(1)).toHaveAccessibleName(
		"Slide 2: The intervention question",
	);
	await filmSlides.nth(1).focus();
	await page.keyboard.press("Alt+ArrowRight");
	await expect(filmSlides.nth(2)).toHaveAccessibleName(
		"Slide 3: The intervention question",
	);
	await filmSlides.nth(2).dragTo(filmSlides.nth(1));
	await expect(filmSlides.nth(1)).toHaveAccessibleName(
		"Slide 2: The intervention question",
	);
	await expect(page.locator(".stage-position")).toContainText("Slide 2 of 18");

	// Archiving is reversible and keeps archived Slides reachable.
	await page.getByRole("button", { name: "Archive Slide" }).click();
	await expect(filmSlides).toHaveCount(17);
	await page.getByRole("button", { name: "Archived (1)" }).click();
	await filmstrip
		.getByRole("button", { name: "Archived Slide: The intervention question" })
		.click();
	await expect(page.locator(".stage-position")).toContainText("Archived Slide");
	await page.getByRole("button", { name: "Restore Slide" }).click();
	await expect(filmSlides).toHaveCount(18);

	// Only the canvas scrolls; the composer stays anchored.
	const desktopOverflow = await page.evaluate(() => {
		const documentScroller = document.scrollingElement;
		const canvas = document.querySelector(".canvas-scroll");
		return {
			documentClientHeight: documentScroller?.clientHeight ?? 0,
			documentScrollHeight: documentScroller?.scrollHeight ?? 0,
			canvasClientHeight: canvas?.clientHeight ?? 0,
			canvasScrollHeight: canvas?.scrollHeight ?? 0,
		};
	});
	expect(desktopOverflow.documentScrollHeight).toBeLessThanOrEqual(
		desktopOverflow.documentClientHeight + 1,
	);
	expect(desktopOverflow.canvasScrollHeight).toBeGreaterThan(
		desktopOverflow.canvasClientHeight,
	);
	const composerBox = page.locator(".composer");
	const desktopComposerBefore = await composerBox.boundingBox();
	await page.locator(".canvas-scroll").evaluate((element) => {
		element.scrollTop = element.scrollHeight;
	});
	const desktopComposerAfter = await composerBox.boundingBox();
	expect(desktopComposerAfter?.y).toBe(desktopComposerBefore?.y);

	// At 1440 × 900 an idle split conversation keeps generous reading space.
	const transcriptHeight =
		(await page.locator(".transcript").boundingBox())?.height ?? 0;
	expect(transcriptHeight).toBeGreaterThanOrEqual(400);

	// The canvas can expand; closing it or choosing a conversation brings the conversation back.
	const conversationPane = page.getByRole("region", {
		name: "Conversation",
		exact: true,
	});
	const canvasPane = page.getByRole("region", { name: "Canvas", exact: true });
	await page.getByRole("button", { name: "Expand canvas" }).click();
	await expect(conversationPane).toBeHidden();
	await expect(canvasPane).toBeVisible();
	expect(
		await page.evaluate(() => localStorage.getItem("course-harness:layout")),
	).toBe("canvas");
	await page.reload();
	await navigator
		.getByRole("button", { name: new RegExp(course.lectures[0].title) })
		.click();
	await expect(
		page.getByRole("button", { name: "Show conversation" }),
	).toHaveAttribute("aria-pressed", "true");
	await expect(conversationPane).toBeHidden();
	await navigator
		.getByRole("list", { name: "Conversations" })
		.getByRole("button")
		.first()
		.click();
	await expect(conversationPane).toBeVisible();
	await expect(canvasPane).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Expand canvas" }),
	).toHaveAttribute("aria-pressed", "false");
	await page.getByRole("button", { name: "Close canvas" }).click();
	await expect(canvasPane).toBeHidden();
	await expect(composer).toBeVisible();
	await navigator
		.getByRole("button", { name: new RegExp(course.lectures[0].title) })
		.click();
	await expect(canvasPane).toBeVisible();
	await page.setViewportSize({ width: 1250, height: 844 });
	await expect
		.poll(async () => (await conversationPane.boundingBox())?.width ?? 0)
		.toBeGreaterThanOrEqual(359);

	// On a phone, one surface shows at a time without horizontal scrolling.
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(canvasPane).toBeVisible();
	await expect(conversationPane).toBeHidden();
	const hasHorizontalOverflow = await page.evaluate(
		() =>
			document.documentElement.scrollWidth >
			document.documentElement.clientWidth,
	);
	expect(hasHorizontalOverflow).toBe(false);
	const backToConversation = page.getByRole("button", {
		name: "Conversation",
		exact: true,
	});
	await backToConversation.focus();
	await backToConversation.press("Enter");
	await expect(conversationPane).toBeVisible();
	await expect(canvasPane).toBeHidden();

	// The conversation keeps the Slide context.
	await expect(page.locator(".context-chip")).toBeVisible();
	await expect(page.locator(".context-chip")).toContainText(
		"Slide 1 · Introduction to causal inference",
	);
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const portraitComposer = await composerBox.boundingBox();
	expect(portraitComposer).not.toBeNull();
	expect(portraitComposer?.x).toBeGreaterThanOrEqual(0);
	expect(
		(portraitComposer?.x ?? 0) + (portraitComposer?.width ?? 0),
	).toBeLessThanOrEqual(390);

	await page.setViewportSize({ width: 844, height: 390 });
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const landscapeComposer = await composerBox.boundingBox();
	expect(landscapeComposer).not.toBeNull();
	expect(
		(landscapeComposer?.x ?? 0) + (landscapeComposer?.width ?? 0),
	).toBeLessThanOrEqual(844);
	await page.setViewportSize({ width: 390, height: 844 });

	// Clear the context.
	await page.getByRole("button", { name: /^Remove context: Slide 1/ }).click();
	await expect(page.locator(".context-chip")).toHaveCount(0);

	// Lectures remain reachable from the navigation drawer.
	await page.getByRole("button", { name: "Open navigation" }).click();
	await expect(
		navigator.getByRole("list").nth(1).getByRole("button"),
	).toHaveCount(2);
	await navigator
		.getByRole("button", { name: new RegExp(course.lectures[1].title) })
		.click();
	await expect(canvasPane).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "No Slides yet" }),
	).toBeVisible();
	await backToConversation.click();
	await page.getByRole("button", { name: "Open navigation" }).click();
	await navigator
		.getByRole("button", { name: new RegExp(course.lectures[0].title) })
		.click();
	await expect(filmSlides).toHaveCount(18);
	expect(renderRequests).toBe(1);

	// UX-14: deleting a Presentation keeps its Lecture.
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.getByRole("button", { name: "Delete Presentation" }).click();
	const deleteDialog = page.getByRole("dialog", {
		name: "Delete this Presentation?",
	});
	await expect(deleteDialog).toContainText(course.lectures[0].title);
	await deleteDialog
		.getByRole("button", { name: "Delete Presentation" })
		.click();
	await expect(deleteDialog).toBeHidden();
	await expect(
		page.getByRole("heading", { name: "No Slides yet" }),
	).toBeVisible();
	await expect(
		navigator.getByRole("button", {
			name: new RegExp(course.lectures[0].title),
		}),
	).toBeVisible();
	const afterDelete = (await (
		await page.request.get("/api/course")
	).json()) as {
		lectures: { id: string }[];
	};
	expect(afterDelete.lectures.map((lecture) => lecture.id)).toContain(
		lectureId,
	);

	// Navigate back to the Course Plan.
	await navigator.getByRole("button", { name: "Course Plan" }).click();
	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();

	// Course files are inspectable in Workspace details.
	const workspaceSettings = await openSettings(page, "Workspace");
	await expect(
		workspaceSettings
			.getByRole("region", { name: "Course files" })
			.getByText(/course\.yaml/),
	).toBeVisible();
	await page.keyboard.press("Escape");

	await page.getByRole("button", { name: "Course menu" }).click();
	await page.getByRole("menuitem", { name: "Open another course" }).click();
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
});
