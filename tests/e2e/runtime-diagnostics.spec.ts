import { expect, test } from "@playwright/test";

import { clearSmokeModelCatalog } from "./smokeState";

test("runtime diagnostics are available before and after selecting a Course Workspace", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");

	await page.getByRole("button", { name: "Runtime diagnostics" }).click();
	const dialog = page.getByRole("dialog", { name: "Runtime diagnostics" });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
	await expect(dialog.getByText("Runtime paths")).toBeVisible();
	await expect(dialog.getByText("Provider Account data")).toBeVisible();
	await expect(
		dialog.getByText(/Provider services are not\s+contacted/),
	).toBeVisible();
	// Focus stays inside the modal: the scrollable body, then back to Close.
	await page.keyboard.press("Tab");
	await expect(dialog.locator(".dialog-body")).toBeFocused();
	await page.keyboard.press("Tab");
	await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
	await page.keyboard.press("Escape");
	await expect(dialog).not.toBeVisible();
	await expect(
		page.getByRole("button", { name: "Runtime diagnostics" }),
	).toBeFocused();

	// After selecting a Workspace, diagnostics live in Settings.
	await page.getByRole("button", { name: "New course" }).click();
	await expect(
		page.getByRole("heading", { name: "Start with your material" }),
	).toBeVisible();
	await clearSmokeModelCatalog(page);
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await settings.getByRole("button", { name: "Diagnostics" }).click();
	await expect(settings.getByText("Presentation renderer")).toBeVisible();
	await expect(
		settings.getByText(/open Models to add a Provider Account/),
	).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(settings).not.toBeVisible();
});
