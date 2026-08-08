import { expect, test } from "@playwright/test";

test("Course Author creates a Course through chat and revises its Syllabus", async ({
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
	await expect(page.getByText("Causal Inference in Practice")).toBeVisible();
	await expect(
		page.getByText("From association to intervention"),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toHaveCount(0);
	await page.getByRole("button", { name: "Save Course Plan" }).click();

	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const lectureSpine = page.getByRole("region", { name: "Lecture spine" });
	await expect(lectureSpine.getByRole("listitem")).toHaveCount(2);
	await page.getByRole("button", { name: "Files" }).click();
	await expect(
		page
			.getByRole("region", { name: "Course files" })
			.getByText(/course\.yaml/),
	).toBeVisible();
	await page.getByRole("button", { name: "Course Plan" }).click();

	await page.getByRole("button", { name: "Edit syllabus" }).click();
	await page
		.getByLabel("Lecture 1 title")
		.fill("Interventions, not associations");
	await page
		.getByRole("button", { name: "Move Confounding and adjustment earlier" })
		.click();
	await page.getByRole("button", { name: "Save changes" }).click();
	await expect(lectureSpine.getByRole("listitem").first()).toContainText(
		"Confounding and adjustment",
	);
	await expect(lectureSpine.getByRole("listitem").nth(1)).toContainText(
		"Interventions, not associations",
	);

	await page.getByRole("button", { name: "All courses" }).click();
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: /playwright-workspace/ }).first(),
	).toBeVisible();
});
