import path from "node:path";

import { expect, test } from "@playwright/test";

import { expectNoAccessibilityViolations } from "./axe";

// The empty Classroom started by `tests/e2e/run_classroom_server.py --admin`, which publishes the
// fixture Course to a bare remote under .cache/classroom-e2e-admin and accepts local paths.
const CLASSROOM = "http://127.0.0.1:18767";
const ADMIN_TOKEN = "e2e-administrator-token-0123456789abcdef";
const FIXTURE_REMOTE = path.resolve(
	".cache/classroom-e2e-admin/author/remote.git",
);

test("the Administrator attaches a course, creates its links, and a Learner opens one", async ({
	page,
	browser,
}) => {
	// A wrong token is answered after a deliberate delay, and attaching fetches and renders.
	test.setTimeout(120_000);
	await page.goto(`${CLASSROOM}/admin`);
	await expect(
		page.getByRole("heading", { name: "Sign in as the Administrator" }),
	).toBeVisible();
	await page.getByLabel("Administrator token").fill("not-the-token");
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByText("That token is not correct.")).toBeVisible();
	await page.getByLabel("Administrator token").fill(ADMIN_TOKEN);
	await page.getByRole("button", { name: "Sign in" }).click();

	await expect(page.getByRole("heading", { name: "Courses" })).toBeVisible();
	await expect(page.getByText("No courses yet.")).toBeVisible();
	await expectNoAccessibilityViolations(page, "The empty Administrator page");

	await page.getByLabel("Repository address").fill(FIXTURE_REMOTE);
	await page.getByLabel("Course address (optional)").fill("causal");
	await page.getByRole("button", { name: "Attach", exact: true }).click();

	const card = page.locator(".course-card");
	await expect(
		card.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible({ timeout: 60_000 });
	await expect(card.getByText("Up to date")).toBeVisible();
	await expect(
		card.getByText("Autumn 2026 (course-release/autumn-2026)"),
	).toBeVisible();

	await card.getByRole("button", { name: "Create Learner link" }).click();
	const learnerField = card.getByLabel("New Learner link");
	await expect(learnerField).toHaveValue(
		new RegExp(`^${CLASSROOM}/l/[A-Za-z0-9_-]+$`),
	);
	const learnerLink = await learnerField.inputValue();
	await expectNoAccessibilityViolations(page, "A course with a new link");
	await card.getByRole("button", { name: "Done" }).click();
	await expect(learnerField).toBeHidden();
	await expect(
		card.getByRole("button", { name: "Rotate Learner link" }),
	).toBeVisible();

	await card.getByRole("button", { name: "Create Instructor link" }).click();
	const instructorField = card.getByLabel("New Instructor link");
	await expect(instructorField).toHaveValue(
		new RegExp(`^${CLASSROOM}/i/[A-Za-z0-9_-]+$`),
	);
	await card.getByRole("button", { name: "Done" }).click();
	await expect(
		card.getByRole("button", { name: "Rotate Instructor link" }),
	).toBeVisible();

	// A Learner opens the link in their own browser: the token becomes a cookie and a clean URL.
	const learner = await browser.newContext();
	const learnerPage = await learner.newPage();
	await learnerPage.goto(learnerLink);
	await expect(learnerPage).toHaveURL(`${CLASSROOM}/c/causal`);
	await expect(
		learnerPage.getByRole("heading", { name: "Before you start" }),
	).toBeVisible();
	await learner.close();
});
