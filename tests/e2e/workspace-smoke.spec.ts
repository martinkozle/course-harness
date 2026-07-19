import { expect, test } from "@playwright/test";

test("Course Author creates and revises a Course from the Workspace Launcher", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Choose where your Course lives." })).toBeVisible();
  await page.getByRole("button", { name: "New Course" }).click();

  await expect(page.getByRole("heading", { name: "Plan the shape before the slides." })).toBeVisible();
  await page.getByLabel("Course title").fill("Causal Inference in Practice");
  await page.getByLabel("Audience").fill("Applied researchers who know regression");
  await page.getByLabel("Goals").fill("Reason clearly about interventions");
  await page.getByLabel("Outcomes").fill("Draw and critique a causal graph");
  await page
    .getByLabel("Lectures in teaching order")
    .fill("From association to intervention\nConfounding and adjustment");
  await page.getByRole("button", { name: "Create Course Plan" }).click();

  await expect(page.getByRole("heading", { name: "Causal Inference in Practice" })).toBeVisible();
  const lectureSpine = page.getByRole("region", { name: "Lecture spine" });
  await expect(lectureSpine.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("region", { name: "Workspace" }).getByText(/course\.yaml/)).toBeVisible();

  await page.getByLabel("Lecture 1 title").fill("Interventions, not associations");
  await page.getByRole("button", { name: "Save title" }).first().click();
  await expect(page.getByLabel("Lecture 1 title")).toHaveValue("Interventions, not associations");

  await page.getByRole("button", { name: "Move Confounding and adjustment earlier" }).click();
  await expect(page.getByLabel("Lecture 1 title")).toHaveValue("Confounding and adjustment");
});
