import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** Fails when automated checks find WCAG A or AA violations on the current page. */
export async function expectNoAccessibilityViolations(
	page: Page,
	surface: string,
) {
	const results = await new AxeBuilder({ page })
		.withTags(["wcag2a", "wcag2aa", "wcag21aa"])
		.analyze();

	expect(
		results.violations,
		`${surface} has no WCAG A or AA accessibility violations`,
	).toEqual([]);
}
