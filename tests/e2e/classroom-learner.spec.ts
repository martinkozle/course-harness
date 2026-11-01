import { expect, test } from "@playwright/test";

import { expectNoAccessibilityViolations } from "./axe";

// The Classroom started by tests/e2e/run_classroom_server.py, serving one fixture Course.
const CLASSROOM = "http://127.0.0.1:18766";
const LEARNER_LINK = `${CLASSROOM}/l/e2e-learner-share-link-token-0123456789abcdef`;
// A fresh student each run, so earlier answers never leak into this one.
const STUDENT_ID = `e2e-${Date.now().toString(36)}`;
const SECOND_STUDENT_ID = `${STUDENT_ID}-b`;
const INSTRUCTOR_LINK = `${CLASSROOM}/i/e2e-instructor-share-link-token-0123456789abcdef`;

test("a Learner identifies, reads a Lecture and retries a Quiz until correct", async ({
	page,
	browser,
}) => {
	await page.goto(LEARNER_LINK);
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal`);

	await expect(
		page.getByRole("heading", { name: "Before you start" }),
	).toBeVisible();
	await page.getByRole("button", { name: "Start" }).click();
	await expect(page.getByText("Enter your name.")).toBeVisible();
	await page.getByLabel("Your name").fill("Ada Lovelace");
	await page.getByLabel("Student ID").fill(STUDENT_ID);
	await page.getByRole("button", { name: "Start" }).click();

	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();

	// The Sources page lists every Source, linked where it has a public locator.
	await page.getByRole("tab", { name: /Sources/ }).click();
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal/sources`);
	await expect(
		page.getByRole("link", { name: /Pearl \(2009\), Causality/ }),
	).toHaveAttribute("href", "https://bayes.cs.ucla.edu/BOOK-2K/");
	await expect(page.getByText("Rubin (1974)")).toBeVisible();
	await expect(page.getByRole("link", { name: /Rubin \(1974\)/ })).toHaveCount(
		0,
	);
	await expect(page.getByText("No public link")).toBeVisible();
	await expectNoAccessibilityViolations(page, "The Sources page");
	await page.getByRole("tab", { name: /Lectures/ }).click();
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal`);

	await expect(
		page.getByRole("heading", { name: "Foundations" }),
	).toBeVisible();
	await expect(
		page.getByText("0 of 6 questions answered correctly"),
	).toBeVisible();
	await expectNoAccessibilityViolations(page, "The course page");

	await page
		.getByRole("link", { name: /From association to intervention/ })
		.click();
	await expect(page).toHaveURL(/\/lectures\/lecture-000000000001$/);
	await expect(page.getByText("Lecture 1 of 3 · Foundations")).toBeVisible();
	await expect(page.locator(".slide-item")).toHaveCount(5);
	// Slide text is real, selectable text over the rendered or plain background.
	await expect(
		page.locator(".slide-preview").getByText("Why randomise?"),
	).toBeVisible();
	await page.getByText("Speaker notes").nth(1).click();
	await expect(
		page.getByText("Stress the difference between bias"),
	).toBeVisible();
	const citation = page.getByRole("link", { name: /Pearl \(2009\)/ }).first();
	await expect(citation).toHaveAttribute(
		"href",
		"https://bayes.cs.ucla.edu/BOOK-2K/",
	);
	await expect(
		page.getByRole("link", { name: "Download slides (PPTX)" }),
	).toHaveAttribute("href", /\/artifacts\/presentation-000000000001\.pptx$/);

	const check = page.locator("#check-your-understanding");
	const first = check.locator(".quiz-question").first();
	await first.getByRole("button", { name: /Sampling error/ }).click();
	await expect(first.getByText("Not this one", { exact: true })).toBeVisible();
	await expect(
		first.getByText("small groups can still differ by chance"),
	).toBeVisible();
	// The picked Option keeps keyboard focus and cannot be picked again.
	const picked = first.getByRole("button", { name: /Sampling error/ });
	await expect(picked).toBeFocused();
	await expect(picked).toHaveAttribute("aria-disabled", "true");
	await first.getByRole("button", { name: /Confounding/ }).click();
	await expect(
		first.getByText("balances confounders between groups"),
	).toBeVisible();
	await expect(
		first.getByRole("button", { name: /Measurement error/ }),
	).toHaveAttribute("aria-disabled", "true");
	await expect(check.getByText("1 of 2 answered correctly")).toBeVisible();
	await expectNoAccessibilityViolations(
		page,
		"A Lecture with an answered Quiz",
	);

	// Another device with the same student ID gets its progress back from the Classroom.
	const device = await browser.newContext({
		viewport: { width: 375, height: 812 },
		reducedMotion: "reduce",
	});
	const phone = await device.newPage();
	await phone.goto(LEARNER_LINK);
	await phone.getByLabel("Your name").fill("Ada Lovelace");
	await phone.getByLabel("Student ID").fill(STUDENT_ID);
	await phone.getByRole("button", { name: "Start" }).click();
	await phone.getByRole("tab", { name: /Exercises/ }).click();
	await expect(phone).toHaveURL(`${CLASSROOM}/c/causal/exercises`);
	await phone
		.getByRole("link", { name: "Continue: From association to intervention" })
		.click();
	const restored = phone.locator(".quiz-question").first();
	await expect(restored.getByText("Correct", { exact: true })).toBeVisible();
	await expect(
		restored.getByText("Not this one", { exact: true }),
	).toBeVisible();
	const width = await phone.evaluate(
		() => document.documentElement.scrollWidth,
	);
	expect(width, "the phone layout never scrolls sideways").toBeLessThanOrEqual(
		375,
	);
	await device.close();
});

test("an Instructor sees where Learners get stuck after a Learner answers", async ({
	browser,
}) => {
	// A Learner gets the second Question of Lecture 2 right only on the second try.
	const learnerContext = await browser.newContext({ reducedMotion: "reduce" });
	const learner = await learnerContext.newPage();
	await learner.goto(LEARNER_LINK);
	await learner.getByLabel("Your name").fill("Grace Hopper");
	await learner.getByLabel("Student ID").fill(SECOND_STUDENT_ID);
	await learner.getByRole("button", { name: "Start" }).click();
	await learner.getByRole("link", { name: /Randomised experiments/ }).click();
	const question = learner
		.locator("#check-your-understanding .quiz-question")
		.nth(1);
	await question.getByRole("button", { name: /P\(Y \| X = x\)/ }).click();
	await expect(
		question.getByText("Not this one", { exact: true }),
	).toBeVisible();
	await question.getByRole("button", { name: /P\(Y \| do\(X = x\)\)/ }).click();
	await expect(question.getByText("Correct", { exact: true })).toBeVisible();
	await learnerContext.close();

	const instructorContext = await browser.newContext({
		viewport: { width: 1280, height: 900 },
		reducedMotion: "reduce",
	});
	const page = await instructorContext.newPage();
	await page.goto(INSTRUCTOR_LINK);
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal`);
	// Insights need no student identity.
	await page.getByRole("link", { name: "Open Insights" }).click();
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal/insights`);
	await expect(
		page.getByRole("heading", { name: "Where Learners get stuck" }),
	).toBeVisible();
	await expect(page.getByRole("tab", { name: "Insights" })).toHaveAttribute(
		"aria-selected",
		"true",
	);

	const stuck = page.getByRole("region", { name: "Where Learners get stuck" });
	const row = stuck.getByRole("row", {
		name: /Which expression describes an intervention/,
	});
	await expect(row.first()).toContainText("P(Y | X = x)");
	await expect(row.first()).toContainText("1 Learner");
	const learners = page.getByRole("region", { name: "Learners", exact: true });
	const grace = learners.getByRole("row", { name: /Grace Hopper/ });
	await expect(grace).toContainText(SECOND_STUDENT_ID);
	await expect(
		page.getByRole("link", { name: "Download CSV" }),
	).toHaveAttribute("href", "/api/c/causal/answers.csv");
	await expectNoAccessibilityViolations(page, "Instructor Insights");

	await page.getByRole("button", { name: "Sync now" }).click();
	await expect(
		page.getByText(/Last synced .* · serving Autumn 2026/),
	).toBeVisible({ timeout: 30_000 });
	await instructorContext.close();
});

test("an Instructor shows and copies the course's links without rotating them", async ({
	browser,
}) => {
	const context = await browser.newContext({ reducedMotion: "reduce" });
	await context.grantPermissions(["clipboard-read", "clipboard-write"], {
		origin: CLASSROOM,
	});
	const page = await context.newPage();
	await page.goto(INSTRUCTOR_LINK);
	await page.goto(`${CLASSROOM}/c/causal/insights`);

	const links = page.getByRole("region", { name: "Links to this course" });
	const learnerField = links.getByLabel("Learner link", { exact: true });
	await expect(learnerField).toHaveValue(LEARNER_LINK);
	await expect(
		links.getByLabel("Instructor link", { exact: true }),
	).toHaveValue(INSTRUCTOR_LINK);
	await expect(
		links.getByText("Only the Administrator can rotate the Instructor link."),
	).toBeVisible();
	await expect(
		links.getByRole("button", { name: "Rotate Instructor link" }),
	).toHaveCount(0);

	await links.getByRole("button", { name: "Copy Instructor link" }).click();
	expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
		INSTRUCTOR_LINK,
	);
	await links.getByRole("button", { name: "Copy Learner link" }).click();
	expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
		LEARNER_LINK,
	);

	// Rotating asks first; cancelling keeps the link everyone already has.
	await links.getByRole("button", { name: "Rotate Learner link" }).click();
	const confirm = page.getByRole("dialog", {
		name: "Rotate the Learner link?",
	});
	await expect(confirm).toContainText("stops working");
	await confirm.getByRole("button", { name: "Cancel" }).click();
	await expect(confirm).toBeHidden();
	await expect(learnerField).toHaveValue(LEARNER_LINK);
	await expectNoAccessibilityViolations(page, "Links to this course");
	await context.close();
});

test("an Instructor switches between the Instructor and Learner views", async ({
	browser,
}) => {
	const context = await browser.newContext({ reducedMotion: "reduce" });
	const page = await context.newPage();
	await page.goto(INSTRUCTOR_LINK);
	await page.getByLabel("Your name").fill("Instructor Ida");
	await page.getByLabel("Student ID").fill(`${STUDENT_ID}-i`);
	await page.getByRole("button", { name: "Start" }).click();

	const viewing = page.getByRole("group", { name: "Viewing as" });
	const asInstructor = viewing.getByRole("button", { name: "Instructor" });
	const asLearner = viewing.getByRole("button", { name: "Learner" });
	await expect(asInstructor).toHaveAttribute("aria-pressed", "true");
	await page.getByRole("tab", { name: "Insights" }).click();
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal/insights`);

	// The Learner view is the Learner's page: no Insights, and back on the course page.
	await asLearner.click();
	await expect(asLearner).toHaveAttribute("aria-pressed", "true");
	await expect(page).toHaveURL(`${CLASSROOM}/c/causal`);
	await expect(page.getByRole("tab", { name: "Lectures" })).toBeVisible();
	await expect(page.getByRole("tab", { name: "Insights" })).toHaveCount(0);
	await expectNoAccessibilityViolations(
		page,
		"An Instructor viewing as a Learner",
	);

	// The choice lasts for this tab, and the server still treats you as an Instructor.
	await page.reload();
	await expect(asLearner).toHaveAttribute("aria-pressed", "true");
	const status = await page.evaluate(
		async () => (await fetch("/api/c/causal/insights")).status,
	);
	expect(status).toBe(200);

	await asInstructor.click();
	await expect(page.getByRole("tab", { name: "Insights" })).toBeVisible();
	await context.close();
});

test("Learners cannot open Insights", async ({ page }) => {
	await page.goto(LEARNER_LINK);
	await expect(
		page.getByRole("heading", { name: "Before you start" }),
	).toBeVisible();
	// Learners never see the Instructor | Learner switch.
	await expect(page.getByRole("group", { name: "Viewing as" })).toHaveCount(0);
	await page.goto(`${CLASSROOM}/c/causal/insights`);
	await expect(
		page.getByText("Insights are for the course’s Instructors."),
	).toBeVisible();
	const status = await page.evaluate(
		async () => (await fetch("/api/c/causal/insights")).status,
	);
	expect(status).toBe(403);
	const links = await page.evaluate(
		async () => (await fetch("/api/c/causal/links")).status,
	);
	expect(links).toBe(403);
});
