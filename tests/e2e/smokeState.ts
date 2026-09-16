import { expect, type Page } from "@playwright/test";

export async function clearSmokeModelCatalog(page: Page) {
	const response = await page.request.get("/api/models");
	const catalog = (await response.json()) as {
		provider_accounts: Array<{ id: string }>;
	};
	for (const account of catalog.provider_accounts) {
		const removed = await page.request.delete(
			`/api/provider-accounts/${account.id}?delete_model_presets=true`,
		);
		expect(removed.ok()).toBe(true);
	}
	await page.reload();
}
