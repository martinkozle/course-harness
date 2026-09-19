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
	await page.getByRole("button", { name: "Replace key" }).click();
	await page
		.getByLabel("New API key for My OpenRouter")
		.fill("rotated-deterministic-test-key");
	await page.getByRole("button", { name: "Save new key" }).click();
	await expect(page.getByText("Planning model")).toBeVisible();
	await expect(
		page.getByLabel("New API key for My OpenRouter"),
	).not.toBeVisible();
	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	await expect(
		page.getByRole("heading", { name: "Build the Lecture" }),
	).toBeVisible();
	await page
		.getByLabel("Message the Course Agent")
		.fill(
			"Create a practical causal inference Course for applied researchers.",
		);
	await page.getByRole("button", { name: "Send message" }).click();

	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	await page.getByRole("button", { name: "Course Plan" }).click();
	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
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

	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	await page
		.getByRole("button", { name: "Conversations", exact: true })
		.click();
	await page.getByRole("button", { name: "New conversation" }).click();
	await expect(
		page.getByRole("button", { name: "New conversation" }),
	).toBeDisabled();
	await expect(page.locator(".conversation-list li")).toHaveCount(1);
	await expect(
		page.getByText("Send a message to save this new conversation."),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "What should we work on?" }),
	).toBeVisible();
	await page.reload();
	await page.getByRole("button", { name: "Authoring", exact: true }).click();
	await page
		.getByRole("button", { name: "Conversations", exact: true })
		.click();
	await expect(page.locator(".conversation-list li")).toHaveCount(1);
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	await page.getByRole("button", { name: "New conversation" }).click();
	await page
		.getByLabel("Message the Course Agent")
		.fill("Review the lecture sequence.");
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(page.locator(".conversation-list li")).toHaveCount(2);
	await expect(
		page.getByText("The lecture sequence is ready for review."),
	).toBeVisible();
	await page
		.locator(".conversation-list li:not(.is-active) .conversation-select")
		.first()
		.click();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const savedConversation = page
		.locator(".conversation-list li:not(.is-active)")
		.first();
	await savedConversation.getByRole("button", { name: "Archive" }).click();
	await expect(savedConversation.getByText("Archived")).toBeVisible();
	await savedConversation.getByRole("button", { name: "Restore" }).click();
	await page
		.getByRole("button", { name: "Compact current conversation" })
		.click();
	const summary = page.getByLabel(
		/Review the summary that will replace model context/,
	);
	await expect(summary).toHaveValue(/Conversation so far:/);
	await summary.fill(
		"We drafted a two-Lecture course plan for applied researchers.",
	);
	await page.getByRole("button", { name: "Use this summary" }).click();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	await page
		.locator(".conversation-list li.is-active")
		.getByRole("button", { name: "Delete", exact: true })
		.click();
	await page.getByRole("button", { name: "Delete now" }).click();
	await expect(
		page.getByText("The lecture sequence is ready for review."),
	).toBeVisible();
	await page
		.locator(".conversation-list li.is-active")
		.getByRole("button", { name: "Delete", exact: true })
		.click();
	await page.getByRole("button", { name: "Delete now" }).click();
	await expect(
		page.getByRole("heading", { name: "What should we work on?" }),
	).toBeVisible();

	await page.getByRole("button", { name: "Models", exact: true }).click();
	await page.getByRole("button", { name: "Delete", exact: true }).click();
	await expect(
		page.getByText("This will also delete 1 attached Model Preset."),
	).toBeVisible();
	await page.getByRole("button", { name: "Delete account" }).click();
	await expect(page.getByText("No Provider Accounts saved yet.")).toBeVisible();
	await expect(page.getByText("Add a Model Preset after")).toBeVisible();

	await page.getByRole("button", { name: "All courses" }).click();
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: /playwright-workspace/ }).first(),
	).toBeVisible();
});
