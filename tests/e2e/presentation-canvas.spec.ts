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

	await page.getByRole("button", { name: "Course Plan" }).click();
	await page
		.getByLabel("Message the Course Agent")
		.fill(
			"Create a practical causal inference Course for applied researchers.",
		);
	await page.getByRole("button", { name: "Send message" }).click();

	await expect(
		page.getByRole("heading", { name: "Apply this change?" }),
	).toBeVisible();
	await page.getByRole("button", { name: "Save Course Plan" }).click();

	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();

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

	// Navigate to Presentations view
	await page.getByRole("button", { name: "Presentations" }).click();
	await expect(
		page.getByRole("heading", { name: "Slide canvas" }),
	).toBeVisible();
	await expect(
		page.getByText("Select a Lecture to view its slide canvas."),
	).toBeVisible();

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

	// Click a lecture context button to set contextual chat
	await page
		.getByRole("button", { name: /Chat about lecture/ })
		.first()
		.click();

	// Verify the chat context indicator appears
	await expect(page.locator(".chat-context-indicator")).toBeVisible();
	await expect(page.locator(".context-text")).toContainText(
		"From association to intervention",
	);

	// Clear the context
	await page.getByRole("button", { name: "Clear" }).click();
	await expect(page.locator(".chat-context-indicator")).not.toBeVisible();

	// Click a lecture to select it
	const lectureButtons = page.locator(
		".lecture-selector-list .lecture-selector-row > button:first-child",
	);
	await expect(lectureButtons).toHaveCount(2);

	await lectureButtons.first().click();
	await expect(
		page.getByText("No Presentation for this Lecture yet."),
	).toBeVisible();

	// Verify "Create presentation" button sets context
	await page.getByRole("button", { name: "Create presentation" }).click();
	await expect(page.locator(".chat-context-indicator")).toBeVisible();
	await expect(page.locator(".context-text")).toContainText(
		"Create a presentation for the lecture",
	);

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
