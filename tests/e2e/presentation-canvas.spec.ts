import { expect, test } from "@playwright/test";

test("Course Author views Presentation canvas and slide outline", async ({
	page,
}) => {
	await page.goto("/");

	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await page.getByRole("button", { name: "New course" }).click();

	await expect(
		page.getByRole("heading", { name: "Give the course a clear shape." }),
	).toBeVisible();
	await page.getByRole("button", { name: "Models", exact: true }).click();
	await page.getByLabel("API key").fill("deterministic-test-key");
	await page.getByRole("button", { name: "Save Provider Account" }).click();
	await page.getByLabel("Preset name").fill("Planning model");
	await page.getByLabel("Model ID").fill("openai/gpt-oss-20b:free");
	await page.getByRole("button", { name: "Save Model Preset" }).click();

	await page.getByRole("button", { name: "Course Plan" }).click();
	await page
		.getByLabel("Message the Course Agent")
		.fill(
			"Create a practical causal inference Course for applied researchers.",
		);
	await page.getByRole("button", { name: "Send message" }).click();

	await expect(
		page.getByRole("heading", { name: "Save this Course Plan?" }),
	).toBeVisible();
	await page.getByRole("button", { name: "Save Course Plan" }).click();

	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();

	// Navigate to Presentations view
	await page.getByRole("button", { name: "Presentations" }).click();
	await expect(
		page.getByRole("heading", { name: "Slide canvas" }),
	).toBeVisible();
	await expect(
		page.getByText("Select a Lecture to view its slide canvas."),
	).toBeVisible();

	const lectureItems = page
		.locator(".lecture-selector-list")
		.getByRole("button");
	await expect(lectureItems).toHaveCount(2);

	await lectureItems.first().click();
	await expect(
		page.getByText("No Presentation for this Lecture yet."),
	).toBeVisible();

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
