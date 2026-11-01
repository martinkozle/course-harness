import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

// The Classroom started by tests/e2e/run_classroom_server.py, serving one fixture Course.
const CLASSROOM = "http://127.0.0.1:18766";
const LEARNER_LINK = `${CLASSROOM}/l/e2e-learner-share-link-token-0123456789abcdef`;

async function expectAccessible(page: Page, surface: string) {
	const results = await new AxeBuilder({ page })
		.withTags(["wcag2a", "wcag2aa", "wcag21aa"])
		.analyze();
	expect(
		results.violations,
		`${surface} has no WCAG A or AA accessibility violations`,
	).toEqual([]);
}

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
	await page.getByLabel("Student ID").fill("e2e-1001");
	await page.getByRole("button", { name: "Start" }).click();

	await expect(
		page.getByRole("heading", { name: "Causal Inference in Practice" }),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "Foundations" }),
	).toBeVisible();
	await expect(
		page.getByText("0 of 6 questions answered correctly"),
	).toBeVisible();
	await expectAccessible(page, "The course page");

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
	await expect(
		first.getByRole("button", { name: /Sampling error/ }),
	).toBeDisabled();
	await first.getByRole("button", { name: /Confounding/ }).click();
	await expect(
		first.getByText("balances confounders between groups"),
	).toBeVisible();
	await expect(
		first.getByRole("button", { name: /Measurement error/ }),
	).toBeDisabled();
	await expect(check.getByText("1 of 2 answered correctly")).toBeVisible();
	await expectAccessible(page, "A Lecture with an answered Quiz");

	// Another device with the same student ID gets its progress back from the Classroom.
	const phone = await browser.newPage({
		viewport: { width: 375, height: 812 },
	});
	await phone.goto(LEARNER_LINK);
	await phone.getByLabel("Your name").fill("Ada Lovelace");
	await phone.getByLabel("Student ID").fill("e2e-1001");
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
	await phone.close();
});
