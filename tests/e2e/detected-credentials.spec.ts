import { expect, type Page, test } from "@playwright/test";

import { clearSmokeModelCatalog } from "./smokeState";

async function openModelSettings(page: Page) {
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: "Models" })
		.click();
	return settings;
}

test("Course Author adds a detected AWS profile and answers the prompt caching question", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	await clearSmokeModelCatalog(page);

	const settings = await openModelSettings(page);
	const detected = settings.getByRole("region", {
		name: "Found on this computer",
	});
	await expect(detected.getByText("AWS profiles in")).toBeVisible();
	await expect(detected.getByLabel("AWS profile", { exact: true })).toHaveValue(
		"work",
	);
	await expect(detected.getByLabel("AWS region")).toHaveValue("eu-central-1");
	await detected.getByRole("button", { name: "Add", exact: true }).click();

	// The added account needs no key, and the first Model Preset form opens.
	await expect(settings.getByText("AWS profile work")).toBeVisible();
	await expect(detected).toBeHidden();
	await expect(
		settings.getByRole("button", { name: "Replace key" }),
	).toHaveCount(0);

	await settings.getByLabel("Preset name").fill("Claude via profile");
	await settings
		.getByLabel("Model ID")
		.fill(
			"arn:aws:bedrock:eu-central-1:123456789012:application-inference-profile/demo",
		);
	await settings.getByRole("button", { name: "Save Model Preset" }).click();
	await expect(
		settings.getByText("cannot tell from this model ID"),
	).toBeVisible();
	await settings.getByLabel("This model supports prompt caching").check();
	await settings.getByRole("button", { name: "Save Model Preset" }).click();
	await expect(settings.getByText("Claude via profile")).toBeVisible();
	await expect(
		settings.getByText("Prompt caching", { exact: true }),
	).toBeVisible();

	await clearSmokeModelCatalog(page);
});
