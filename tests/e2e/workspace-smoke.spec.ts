import { expect, test } from "@playwright/test";

test("Course Author creates a Course through chat and revises its Syllabus", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Your courses" })).toBeVisible();
  await page.getByRole("button", { name: "New course" }).click();

  await expect(page.getByRole("heading", { name: "Give the course a clear shape." })).toBeVisible();
  await page.getByLabel("API key").fill("deterministic-test-key");
  await page.getByRole("button", { name: "Save connection" }).click();
  await page
    .getByLabel("Message the Course Agent")
    .fill("Create a practical causal inference Course for applied researchers.");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByRole("heading", { name: "Apply this Course Plan?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Causal Inference in Practice" })).toHaveCount(0);
  await page.getByRole("button", { name: "Approve plan" }).click();

  await expect(page.getByRole("heading", { name: "Causal Inference in Practice" })).toBeVisible();
  await expect(page.getByText("I created a two-Lecture Course Plan.")).toBeVisible();
  const lectureSpine = page.getByRole("region", { name: "Lecture spine" });
  await expect(lectureSpine.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("region", { name: "Course files" }).getByText(/course\.yaml/)).toBeVisible();

  await page.getByRole("button", { name: "Edit syllabus" }).click();
  await page.getByLabel("Lecture 1 title").fill("Interventions, not associations");
  await page.getByRole("button", { name: "Move Confounding and adjustment earlier" }).click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(lectureSpine.getByRole("listitem").first()).toContainText("Confounding and adjustment");
  await expect(lectureSpine.getByRole("listitem").nth(1)).toContainText(
    "Interventions, not associations",
  );

  await page.getByRole("button", { name: "All courses" }).click();
  await expect(page.getByRole("heading", { name: "Your courses" })).toBeVisible();
  await expect(page.getByRole("button", { name: /playwright-workspace/ })).toBeVisible();
});
