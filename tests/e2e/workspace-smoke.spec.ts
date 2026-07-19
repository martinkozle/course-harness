import { expect, test } from "@playwright/test";

test("Course Author creates and revises a Course from Courses", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Your courses" })).toBeVisible();
  await page.getByRole("button", { name: "New course" }).click();

  await expect(page.getByRole("heading", { name: "Give the course a clear shape." })).toBeVisible();
  await page.getByLabel("Course title").fill("Causal Inference in Practice");
  await page.getByLabel("Audience").fill("Applied researchers who know regression");
  await page
    .getByLabel("Lectures in teaching order")
    .fill("From association to intervention\nConfounding and adjustment");
  await page.getByRole("button", { name: "Create course" }).click();

  await expect(page.getByRole("heading", { name: "Causal Inference in Practice" })).toBeVisible();
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
