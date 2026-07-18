import { expect, test } from "@playwright/test";

test("Course Author can confirm the active Workspace", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Your course starts here" })).toBeVisible();
  const workspace = page.getByRole("region", { name: "Active Workspace" });
  await expect(workspace.getByText("playwright-workspace", { exact: true })).toBeVisible();
  await expect(workspace.getByText(/\.cache\/playwright-workspace$/)).toBeVisible();
});
