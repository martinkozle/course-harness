import { expect, type Page, test } from "@playwright/test";

async function openSettings(page: Page, section: string) {
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await expect(settings).toBeVisible();
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: section })
		.click();
	return settings;
}

test("Course Author creates a Course through chat and revises its Course Plan", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");

	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Start with your material" }),
	).toBeVisible();

	// Provider and Model Preset management happens in Settings.
	const settings = await openSettings(page, "Models");
	if (await settings.getByLabel("API key").isVisible()) {
		await settings.getByLabel("API key").fill("deterministic-test-key");
		await settings
			.getByRole("button", { name: "Save Provider Account" })
			.click();
		await expect(settings.getByLabel("Preset name")).toBeVisible();
	}
	if (await settings.getByLabel("Preset name").isVisible()) {
		await settings.getByLabel("Preset name").fill("Planning model");
		await settings.getByLabel("Model ID").fill("openai/gpt-oss-20b:free");
		await settings.getByRole("button", { name: "Save Model Preset" }).click();
	}
	await settings.getByRole("button", { name: "Replace key" }).click();
	await settings
		.getByLabel("New API key for My OpenRouter")
		.fill("rotated-deterministic-test-key");
	await settings.getByRole("button", { name: "Save new key" }).click();
	await expect(settings.getByText("Planning model")).toBeVisible();
	await expect(
		settings.getByLabel("New API key for My OpenRouter"),
	).not.toBeVisible();
	await settings.getByRole("button", { name: "Close" }).click();
	await expect(settings).toBeHidden();

	// The first chat turn creates the Course Plan, which opens beside the conversation.
	await page
		.getByLabel("Message the Course Agent")
		.fill(
			"Create a practical causal inference Course for applied researchers.",
		);
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();
	const lectureSequence = page.getByRole("list", { name: "Lecture sequence" });
	await expect(lectureSequence.getByRole("listitem")).toHaveCount(2);
	const navigator = page.getByRole("navigation", { name: "Course" });
	await expect(
		navigator.getByRole("button", { name: /From association to intervention/ }),
	).toBeVisible();

	// Workspace files are an advanced, read-only inventory in Settings.
	const workspaceSettings = await openSettings(page, "Workspace");
	await expect(
		workspaceSettings
			.getByRole("region", { name: "Course files" })
			.getByText(/course\.yaml/),
	).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(workspaceSettings).toBeHidden();

	// Lectures are renamed and reordered directly in the Course Plan.
	await page
		.getByRole("button", {
			name: "Actions for From association to intervention",
		})
		.click();
	await page.getByRole("menuitem", { name: "Rename" }).click();
	await page
		.getByLabel("Lecture 1 title")
		.fill("Interventions, not associations");
	await page.getByRole("button", { name: "Save", exact: true }).click();
	await expect(lectureSequence.getByRole("listitem").first()).toContainText(
		"Interventions, not associations",
	);
	await page
		.getByRole("button", { name: "Actions for Confounding and adjustment" })
		.click();
	await page.getByRole("menuitem", { name: "Move earlier" }).click();
	await expect(lectureSequence.getByRole("listitem").first()).toContainText(
		"Confounding and adjustment",
	);
	await expect(lectureSequence.getByRole("listitem").nth(1)).toContainText(
		"Interventions, not associations",
	);
	await expect(
		navigator.getByRole("button", { name: /Confounding and adjustment/ }),
	).toContainText("1");

	// A new Conversation stays a draft until its first message.
	const conversations = page.getByRole("list", { name: "Conversations" });
	const savedRows = conversations.locator(".conversation-row");
	await page.getByRole("button", { name: "New conversation" }).click();
	await expect(
		page.getByRole("heading", { name: "What should we work on?" }),
	).toBeVisible();
	await expect(conversations.getByText("New conversation")).toBeVisible();
	await expect(savedRows).toHaveCount(1);
	await page.reload();
	await expect(savedRows).toHaveCount(1);
	await expect(conversations.getByText("New conversation")).toHaveCount(0);
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();

	// Stopping a run leaves the conversation usable.
	await page.getByRole("button", { name: "New conversation" }).click();
	await page
		.getByLabel("Message the Course Agent")
		.fill("Wait until I stop you.");
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByRole("button", { name: "Stop response", exact: true }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "New conversation" }),
	).toBeDisabled();
	await page
		.getByRole("button", { name: "Stop response", exact: true })
		.click();
	await expect(page.locator("#agent-run-status")).toHaveText(
		"Course Agent stopped.",
	);
	await expect(
		page.getByText(/Stopped\. Changes the agent saved/),
	).toBeVisible();
	await page
		.getByLabel("Message the Course Agent")
		.fill("Review the lecture sequence.");
	await page.getByRole("button", { name: "Send message" }).click();
	await expect(
		page.getByText("The lecture sequence is ready for review."),
	).toBeVisible();
	await expect(savedRows).toHaveCount(2);

	// Switching Conversations keeps the transcript area intact.
	await savedRows
		.locator('.conversation-item:not([aria-current="true"])')
		.first()
		.click();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();
	const otherRow = savedRows.filter({
		has: page.locator('.conversation-item:not([aria-current="true"])'),
	});
	const otherTitle = (
		await otherRow.locator(".nav-label").textContent()
	)?.trim();
	expect(otherTitle).toBeTruthy();
	await otherRow.getByRole("button", { name: /^Actions for / }).click();
	await page.getByRole("menuitem", { name: "Archive" }).click();
	await expect(savedRows).toHaveCount(1);
	await page.getByRole("button", { name: "Archived (1)" }).click();
	await expect(
		page.getByRole("heading", { name: "Archived conversations" }),
	).toBeVisible();
	await savedRows.getByRole("button", { name: /^Actions for / }).click();
	await page.getByRole("menuitem", { name: "Restore" }).click();
	await expect(
		page.getByRole("heading", { name: "Conversations", exact: true }),
	).toBeVisible();
	await expect(savedRows).toHaveCount(2);

	// Summarizing earlier context keeps the full transcript visible.
	await page.getByRole("button", { name: "Conversation actions" }).click();
	await page
		.getByRole("menuitem", { name: /Compact conversation/ })
		.click();
	const summary = page.getByLabel(/Summary of \d+ earlier messages/);
	await expect(summary).toHaveValue(/Conversation so far:/);
	await summary.fill(
		"We drafted a two-Lecture course plan for applied researchers.",
	);
	await page.getByRole("button", { name: "Compact conversation" }).click();
	await expect(summary).toBeHidden();
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();

	// Deleting the active Conversation opens the next one.
	await page.getByRole("button", { name: "Conversation actions" }).click();
	await page.getByRole("menuitem", { name: "Delete" }).click();
	await page.getByRole("button", { name: "Delete conversation" }).click();
	await expect(
		page.getByText("The lecture sequence is ready for review."),
	).toBeVisible();
	await page.getByRole("button", { name: "Conversation actions" }).click();
	await page.getByRole("menuitem", { name: "Delete" }).click();
	await page.getByRole("button", { name: "Delete conversation" }).click();
	await expect(
		page.getByRole("heading", { name: "What should we work on?" }),
	).toBeVisible();

	// Deleting a Provider Account explains the attached Presets it removes.
	const modelSettings = await openSettings(page, "Models");
	await modelSettings
		.getByRole("button", { name: "Delete", exact: true })
		.click();
	await expect(
		modelSettings.getByText("This will also delete 1 attached Model Preset."),
	).toBeVisible();
	await modelSettings.getByRole("button", { name: "Delete account" }).click();
	await expect(
		modelSettings.getByText("No Provider Accounts saved yet."),
	).toBeVisible();
	await expect(
		modelSettings.getByText("Add a Model Preset after"),
	).toBeVisible();
	await page.keyboard.press("Escape");

	await page.getByRole("button", { name: "Course menu" }).click();
	await page.getByRole("menuitem", { name: "Open another course" }).click();
	await expect(
		page.getByRole("heading", { name: "Your courses" }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: /playwright-workspace/ }).first(),
	).toBeVisible();
});
