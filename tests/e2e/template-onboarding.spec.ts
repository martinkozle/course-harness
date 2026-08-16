import { expect, test } from "@playwright/test";

const profile = {
	schema_version: 1,
	id: "tpl-abcdef123456",
	name: "Institutional template",
	version: 1,
	template_filename: "institutional.pptx",
	slide_width: 12_192_000,
	slide_height: 6_858_000,
	slide_count: 4,
	layouts: [
		"title",
		"section",
		"bullets",
		"two_column",
		"big_statement",
		"closing",
		"code",
		"image",
		"quote",
	].map((semantic_layout, index) => ({
		semantic_layout,
		template_layout_index: Math.min(index, 3),
		confidence: semantic_layout === "bullets" ? 0.4 : 0.9,
		rationale: `Heuristic mapping for ${semantic_layout}`,
	})),
};

const inspection = {
	slide_width: 12_192_000,
	slide_height: 6_858_000,
	slide_count: 4,
	layouts: [
		{ index: 0, name: "Title Slide", placeholders: [{ name: "Title 1" }] },
		{ index: 1, name: "Section Header", placeholders: [{ name: "Title 1" }] },
		{
			index: 2,
			name: "Title and Content",
			placeholders: [{ name: "Title 1" }, { name: "Content 2" }],
		},
		{
			index: 3,
			name: "Two Content",
			placeholders: [
				{ name: "Title 1" },
				{ name: "Content 2" },
				{ name: "Content 3" },
			],
		},
	],
	masters: [],
	theme: { name: "Office", colors: {} },
	example_slides: [],
};

test("Course Author reviews, improves, and validates template mappings", async ({
	page,
}) => {
	await page.route("**/api/models", async (route) => {
		await route.fulfill({
			json: {
				provider_accounts: [
					{
						id: "provider-1",
						name: "Faculty OpenRouter",
						kind: "openrouter",
						base_url: "https://openrouter.ai/api/v1",
					},
				],
				model_presets: [
					{
						id: "preset-1",
						name: "Planning model",
						provider_account_id: "provider-1",
						model: "example/free-model",
						capabilities: {
							tool_calling: true,
							structured_output: true,
							streaming: true,
							context_window: 32_000,
							vision: false,
						},
						diagnostics: [],
					},
				],
				selected_model_id: "preset-1",
			},
		});
	});
	await page.route("**/api/templates", async (route) => {
		if (route.request().method() === "GET") {
			await route.fulfill({
				json: [
					{
						id: profile.id,
						name: profile.name,
						version: profile.version,
						slide_count: profile.slide_count,
						mapped_layouts: profile.layouts.length,
					},
				],
			});
			return;
		}
		await route.fallback();
	});
	await page.route(`**/api/templates/${profile.id}`, async (route) => {
		if (route.request().method() === "PUT") {
			await route.fulfill({
				json: {
					...profile,
					version: 2,
					layouts: profile.layouts.map((mapping) =>
						mapping.semantic_layout === "bullets"
							? { ...mapping, template_layout_index: 3 }
							: mapping,
					),
				},
			});
			return;
		}
		await route.fulfill({ json: profile });
	});
	await page.route(
		`**/api/templates/${profile.id}/inspection`,
		async (route) => {
			await route.fulfill({ json: inspection });
		},
	);
	await page.route(
		`**/api/templates/${profile.id}/suggest-mappings`,
		async (route) => {
			expect(route.request().postDataJSON()).toEqual({ consent: true });
			await route.fulfill({
				json: {
					mappings: profile.layouts.map((mapping) =>
						mapping.semantic_layout === "bullets"
							? {
									...mapping,
									template_layout_index: 3,
									confidence: 0.95,
									rationale: "The model selected Two Content.",
								}
							: mapping,
					),
				},
			});
		},
	);
	await page.route(`**/api/templates/${profile.id}/validate`, async (route) => {
		await route.fulfill({
			json: [
				{
					level: "warning",
					message: "Bullets may need a BODY placeholder.",
				},
			],
		});
	});
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	await page.getByRole("button", { name: "Templates" }).click();
	await page.getByLabel("Inspect template").selectOption(profile.id);

	const bulletsMapping = page.getByLabel("Template layout for bullets");
	await expect(
		bulletsMapping.getByRole("option", {
			name: "3: Two Content — 3 placeholders",
		}),
	).toHaveCount(1);
	await expect(
		page.getByText(/Faculty OpenRouter.*Planning model/),
	).toBeVisible();
	await expect(
		page.getByText(/template metadata.*leave this device/i),
	).toBeVisible();

	const suggestButton = page.getByRole("button", {
		name: "Improve mappings with AI",
	});
	await expect(suggestButton).toBeDisabled();
	await page.getByLabel(/I agree to send this template metadata/).check();
	await suggestButton.click();
	await expect(bulletsMapping).toHaveValue("3");
	await expect(page.getByText("The model selected Two Content.")).toBeVisible();

	await page.getByRole("button", { name: "Save mapping corrections" }).click();
	await page.getByRole("button", { name: "Check mappings" }).click();
	await expect(
		page.getByText("Bullets may need a BODY placeholder."),
	).toBeVisible();
});
