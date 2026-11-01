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
	const learnerField = card.getByLabel("Learner link", { exact: true });
	await expect(learnerField).toHaveValue(
		new RegExp(`^${CLASSROOM}/l/[A-Za-z0-9_-]+$`),
	);
	const learnerLink = await learnerField.inputValue();
	await expectNoAccessibilityViolations(page, "A course with a new link");
	await expect(
		card.getByRole("button", { name: "Rotate Learner link" }),
	).toBeVisible();

	await card.getByRole("button", { name: "Create Instructor link" }).click();
	const instructorField = card.getByLabel("Instructor link", { exact: true });
	await expect(instructorField).toHaveValue(
		new RegExp(`^${CLASSROOM}/i/[A-Za-z0-9_-]+$`),
	);
	const instructorLink = await instructorField.inputValue();

	// Links are shown again later, and copied without rotating them.
	await page.reload();
	await expect(learnerField).toHaveValue(learnerLink);
	await expect(instructorField).toHaveValue(instructorLink);
	await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
		origin: CLASSROOM,
	});
	await card.getByRole("button", { name: "Copy Learner link" }).click();
	await expect(
		card.getByRole("button", { name: "Copied Learner link" }),
	).toBeVisible();
	expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
		learnerLink,
	);
	await card.getByRole("button", { name: "Rotate Learner link" }).click();
	const confirm = page.getByRole("dialog", {
		name: "Rotate the Learner link?",
	});
	await expect(confirm).toBeVisible();
	await confirm.getByRole("button", { name: "Cancel" }).click();
	await expect(learnerField).toHaveValue(learnerLink);

	// The Administrator opens the course as an Instructor without any link.
	await card.getByRole("link", { name: "Open as Instructor" }).click();
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal`);
	await expect(
		page.getByText(
			"You are signed in as the Administrator, so you see this course as an Instructor.",
		),
	).toBeVisible();
	const viewing = page.getByRole("group", { name: "Viewing as" });
	await expect(
		viewing.getByRole("button", { name: "Instructor" }),
	).toHaveAttribute("aria-pressed", "true");
	await page.getByRole("link", { name: "Open Insights" }).click();
	const links = page.getByRole("region", { name: "Links to this course" });
	await expect(links.getByLabel("Learner link", { exact: true })).toHaveValue(
		learnerLink,
	);
	// Only the Administrator may rotate the Instructor link.
	await expect(
		links.getByRole("button", { name: "Rotate Instructor link" }),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "Insights for the Administrator");

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
