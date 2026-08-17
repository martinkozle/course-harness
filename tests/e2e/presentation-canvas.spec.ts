import { expect, test } from "@playwright/test";

test("Course Author views Presentation canvas and slide outline", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");

	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await page.getByRole("button", { name: "New course" }).click();

	await expect(
		page.getByRole("heading", { name: "Give the course a clear shape." }),
	).toBeVisible();
	await page.getByRole("button", { name: "Models", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Models" })).toBeVisible();
	if (await page.getByLabel("API key").isVisible()) {
		await page.getByLabel("API key").fill("deterministic-test-key");
		await page.getByRole("button", { name: "Save Provider Account" }).click();
		await expect(page.getByLabel("Preset name")).toBeVisible();
	}
	if (await page.getByLabel("Preset name").isVisible()) {
		await page.getByLabel("Preset name").fill("Planning model");
		await page.getByLabel("Model ID").fill("openai/gpt-oss-20b:free");
		await page.getByRole("button", { name: "Save Model Preset" }).click();
	}

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

	// The template manager shares the page shell and exposes one built-in option.
	await page.getByRole("button", { name: "Templates" }).click();
	await expect(
		page.getByRole("heading", { name: "Templates", level: 1 }),
	).toBeVisible();
	await expect(
		page
			.getByLabel("Inspect template")
			.getByRole("option", { name: "Built-in default" }),
	).toHaveCount(1);
	await expect(
		page.getByText("The built-in template has predefined mappings."),
	).toBeVisible();

	// The merged Author workspace keeps the Presentation and Course Agent together.
	await page.getByRole("button", { name: "Course Plan" }).click();
	await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
	expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
	await page.getByRole("button", { name: "Authoring" }).click();
	await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
	await expect(
		page.getByRole("heading", { name: "Build the Lecture" }),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "Course Agent" }),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "Presentation" }),
	).toBeVisible();
	await expect(page.locator(".slide-list > li")).toHaveCount(18);
	await expect(page.locator(".semantic-preview")).toHaveCount(18);
	await expect(
		page.getByText("Updating 18 high-fidelity previews…"),
	).toBeVisible();
	await expect(page.locator(".preview-freshness").first()).toHaveText(
		"Updating preview…",
	);
	await expect(page.locator(".slide-list > li").first()).toHaveScreenshot(
		"presentation-preview-chrome.png",
		{
			mask: [page.locator(".visual-slide-preview").first()],
			maxDiffPixelRatio: 0.01,
		},
	);
	await expect(
		page.getByText("High-fidelity thumbnails are ready."),
	).toBeVisible();
	expect(renderRequests).toBe(1);
	await expect(page.locator(".preview-freshness")).toHaveCount(0);
	await page.locator(".slide-card").first().click();
	await expect(
		page.getByRole("button", { name: "Introduction to causal inference" }),
	).toBeVisible();
	await page.getByRole("button", { name: "Close slide detail" }).click();
	await expect(page.locator(".authoring-body")).toHaveCSS("display", "block");
	await expect(page.locator(".context-text")).toContainText(
		"From association to intervention",
	);
	const desktopOverflow = await page.evaluate(() => {
		const documentScroller = document.scrollingElement;
		const slides = document.querySelector(".slide-canvas");
		return {
			documentClientHeight: documentScroller?.clientHeight ?? 0,
			documentScrollHeight: documentScroller?.scrollHeight ?? 0,
			slideClientHeight: slides?.clientHeight ?? 0,
			slideScrollHeight: slides?.scrollHeight ?? 0,
		};
	});
	expect(desktopOverflow.documentScrollHeight).toBeLessThanOrEqual(
		desktopOverflow.documentClientHeight + 1,
	);
	expect(desktopOverflow.slideScrollHeight).toBeGreaterThan(
		desktopOverflow.slideClientHeight,
	);
	const desktopComposerBefore = await page
		.locator(".chat-composer")
		.boundingBox();
	await page.locator(".slide-canvas").evaluate((element) => {
		element.scrollTop = element.scrollHeight;
	});
	const desktopComposerAfter = await page
		.locator(".chat-composer")
		.boundingBox();
	expect(desktopComposerAfter?.y).toBe(desktopComposerBefore?.y);

	const primaryPane = page.locator(".authoring-primary-pane");
	const primaryWidthBefore = (await primaryPane.boundingBox())?.width ?? 0;
	const separator = page.getByRole("separator", {
		name: "Resize Presentation and Course Agent",
	});
	const separatorBox = await separator.boundingBox();
	expect(separatorBox).not.toBeNull();
	await page.mouse.move(
		(separatorBox?.x ?? 0) + (separatorBox?.width ?? 0) / 2,
		(separatorBox?.y ?? 0) + 120,
	);
	await page.mouse.down();
	await page.mouse.move(
		(separatorBox?.x ?? 0) - 70,
		(separatorBox?.y ?? 0) + 120,
	);
	await page.mouse.up();
	const primaryWidthAfterDrag = (await primaryPane.boundingBox())?.width ?? 0;
	expect(primaryWidthAfterDrag).toBeLessThan(primaryWidthBefore);
	await separator.focus();
	await separator.press("ArrowRight");
	const primaryWidthAfter = (await primaryPane.boundingBox())?.width ?? 0;
	expect(primaryWidthAfter).toBeGreaterThan(primaryWidthAfterDrag);
	expect(
		await page.evaluate(() =>
			localStorage.getItem("course-harness:authoring-agent-width"),
		),
	).not.toBeNull();
	await page.evaluate(() =>
		localStorage.setItem("course-harness:authoring-agent-width", "25"),
	);
	await page.reload();
	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	await page.setViewportSize({ width: 1250, height: 844 });
	await expect
		.poll(
			async () =>
				(await page.locator(".authoring-secondary-pane").boundingBox())
					?.width ?? 0,
		)
		.toBeGreaterThanOrEqual(339);

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(page.locator(".presentation-layout")).toHaveCSS(
		"display",
		"grid",
	);
	const hasHorizontalOverflow = await page.evaluate(
		() =>
			document.documentElement.scrollWidth >
			document.documentElement.clientWidth,
	);
	expect(hasHorizontalOverflow).toBe(false);
	await expect(
		page.getByRole("button", { name: "Presentation", exact: true }),
	).toBeVisible();
	await page.getByRole("button", { name: "Course Agent", exact: true }).click();

	// The agent remains in the same view and retains the active Lecture context.
	await expect(page.locator(".chat-context-indicator")).toBeVisible();
	await expect(page.locator(".context-text")).toContainText(
		"From association to intervention",
	);
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const portraitComposer = await page.locator(".chat-composer").boundingBox();
	expect(portraitComposer).not.toBeNull();
	expect(portraitComposer?.x).toBeGreaterThanOrEqual(0);
	expect(
		(portraitComposer?.x ?? 0) + (portraitComposer?.width ?? 0),
	).toBeLessThanOrEqual(390);

	await page.setViewportSize({ width: 844, height: 390 });
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const landscapeComposer = await page.locator(".chat-composer").boundingBox();
	expect(landscapeComposer).not.toBeNull();
	expect(
		(landscapeComposer?.x ?? 0) + (landscapeComposer?.width ?? 0),
	).toBeLessThanOrEqual(844);
	await page.setViewportSize({ width: 390, height: 844 });

	// Clear the context
	await page.getByRole("button", { name: "Remove" }).click();
	await expect(page.locator(".chat-context-indicator")).not.toBeVisible();
	// Lecture selection and Presentation review remain usable in the same view.
	const lectureButtons = page.locator(
		".lecture-selector-list .lecture-selector-row > button:first-child",
	);
	await page.getByRole("button", { name: "Presentation", exact: true }).click();
	await expect(lectureButtons).toHaveCount(2);

	await lectureButtons.first().click();
	await expect(page.locator(".slide-list > li")).toHaveCount(18);
	expect(renderRequests).toBe(1);

	// Navigate back to Course Plan
	await page.getByRole("button", { name: "Course Plan" }).click();
	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();

	// Go to Files and verify course.yaml exists
	await page.getByRole("button", { name: "Files" }).click();
	await expect(
		page
			.getByRole("region", { name: "Course files" })
			.getByText(/course\.yaml/),
	).toBeVisible();

	await page.getByRole("button", { name: "All courses" }).click();
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
});
