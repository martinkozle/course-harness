import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, type Page, test } from "@playwright/test";

function navigator(page: Page) {
	return page.getByRole("navigation", { name: "Course" });
}

/** History reads Current State when it opens; reopen it after outside edits. */
async function reopenHistory(page: Page) {
	await navigator(page).getByRole("button", { name: "Course Plan" }).click();
	await navigator(page)
		.getByRole("button", { name: /History/ })
		.click();
	await expect(
		page.getByRole("heading", { name: "History", exact: true }),
	).toBeVisible();
	await expect(page.getByText("Reading history…")).toHaveCount(0);
}

test("Course Author can review History before and after creating a Course", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	await navigator(page)
		.getByRole("button", { name: /History/ })
		.click();
	await expect(
		page.getByRole("heading", { name: "History", exact: true }),
	).toBeVisible();
	await expect(
		page.getByRole("heading", {
			name: "History starts once the course has a Course Plan.",
		}),
	).toBeVisible();

	const response = await page.request.post("/api/course", {
		data: {
			title: "Causal Inference in Practice",
			audience: "Applied researchers",
			goals: [],
			outcomes: [],
			lectures: [{ title: "From association to intervention" }],
		},
	});
	expect(response.ok()).toBe(true);
	const createdCourse = (await response.json()) as {
		lectures: { id: string }[];
	};
	const lectureId = createdCourse.lectures[0].id;
	await page.reload();

	// The navigator shows how many changes are not yet in a Course Revision.
	await expect(
		navigator(page).getByRole("button", { name: /History/ }),
	).toContainText("1");
	await reopenHistory(page);
	await expect(
		page.getByRole("heading", { name: "History", exact: true }),
	).toBeInViewport();
	const changes = page.locator(".history-change");
	await expect(changes).toHaveCount(1);
	await expect(changes.first()).toContainText("Course Plan");
	await expect(changes.first()).toContainText("Added");
	await changes.first().getByText("Show file changes").click();
	await expect(
		changes.first().getByText("course.yaml", { exact: true }),
	).toBeVisible();
	await expect(changes.first().getByText(/· \d+ lines?/)).toBeVisible();
	await expect(page.getByText("Issues to review")).toHaveCount(0);

	await page.getByRole("button", { name: "Remove the Course Plan" }).click();
	const removeDialog = page.getByRole("dialog", {
		name: "Remove the Course Plan?",
	});
	await expect(removeDialog).toContainText("added since the last revision");
	await removeDialog.getByRole("button", { name: "Cancel" }).click();
	await expect(removeDialog).toBeHidden();

	await page.getByLabel("Revision summary").fill("Initial course");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(
		page.getByRole("tab", { name: /Course Revisions/, selected: true }),
	).toBeVisible();
	await expect(page.getByText("Initial course")).toBeVisible();

	await page.request.patch(`/api/course/lectures/${lectureId}`, {
		data: { title: "Edited outside History" },
	});
	await reopenHistory(page);
	await expect(changes.first()).toContainText("Changed");
	await page
		.getByRole("button", { name: "Undo changes to the Course Plan" })
		.click();
	await page
		.getByRole("dialog", { name: "Undo changes to the Course Plan?" })
		.getByRole("button", { name: "Undo changes" })
		.click();
	await expect(
		page.getByRole("heading", { name: "No changes since the last revision." }),
	).toBeVisible();

	await page.request.patch(`/api/course/lectures/${lectureId}`, {
		data: { title: "First revision title" },
	});
	await reopenHistory(page);
	await page.getByLabel("Revision summary").fill("First revision");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(page.getByText("First revision", { exact: true })).toBeVisible();

	await page.request.patch(`/api/course/lectures/${lectureId}`, {
		data: { title: "Second revision title" },
	});
	await reopenHistory(page);
	await page.getByLabel("Revision summary").fill("Second revision");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(
		page.getByText("Second revision", { exact: true }),
	).toBeVisible();

	const firstRevision = page
		.locator(".history-revisions > li")
		.filter({ hasText: "First revision" });
	await firstRevision.getByRole("button", { name: "Restore" }).click();
	const restoreDialog = page.getByRole("dialog", {
		name: "Restore this Course Revision?",
	});
	await expect(restoreDialog).toContainText("First revision");
	await restoreDialog.getByRole("button", { name: "Restore revision" }).click();
	await expect(restoreDialog).toBeHidden();
	const restoredCourse = await page.request.get("/api/course");
	expect((await restoredCourse.json()).lectures[0].title).toBe(
		"First revision title",
	);
	// The Course Plan everywhere reflects the restored revision.
	await expect(
		navigator(page).getByRole("button", { name: /First revision title/ }),
	).toBeVisible();
});

test("Course Author accepts valid outside changes as a Course Revision", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	const createResponse = await page.request.post("/api/course", {
		data: {
			title: "Drift review course",
			audience: "Course Authors",
			goals: [],
			outcomes: [],
			lectures: [{ title: "Review the workspace" }],
		},
	});
	expect(createResponse.ok()).toBe(true);
	const workspaceResponse = await page.request.get("/api/workspace");
	const workspace = (await workspaceResponse.json()) as { path: string };
	const coursePath = join(workspace.path, "course.yaml");
	const course = await readFile(coursePath, "utf8");
	await writeFile(
		coursePath,
		course.replace(
			"Drift review course",
			"Drift review course, edited outside the app",
		),
		"utf8",
	);

	// Outside changes raise a persistent, actionable banner.
	await page.reload();
	await expect(
		page.getByText("Course files changed outside the app."),
	).toBeVisible();
	await page.getByRole("button", { name: "Review changes" }).click();
	const driftPanel = page.locator(".history-drift");
	await expect(
		driftPanel.getByRole("heading", {
			name: "Course files changed outside the app",
		}),
	).toBeVisible();
	await expect(driftPanel).toContainText("Course Plan");
	await page
		.getByLabel("Describe the outside changes")
		.fill("Accept external Course title edit");
	await page.getByRole("button", { name: "Accept outside changes" }).click();
	const acceptDialog = page.getByRole("dialog", {
		name: "Accept outside changes?",
	});
	await expect(acceptDialog).toContainText("Accept external Course title edit");
	const [acceptResponse] = await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().endsWith("/api/workspace/drift/accept") &&
				response.request().method() === "POST",
		),
		acceptDialog.getByRole("button", { name: "Accept changes" }).click(),
	]);
	expect(acceptResponse.ok()).toBe(true);
	await expect(driftPanel).toHaveCount(0);
	await expect(
		page.getByText("Course files changed outside the app."),
	).toHaveCount(0);
	await page.getByRole("tab", { name: /Course Revisions/ }).click();
	await expect(
		page.getByText("Accept external Course title edit"),
	).toBeVisible();
});

test("Course Author can hand inconsistent outside changes to the Course Agent", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	const createResponse = await page.request.post("/api/course", {
		data: {
			title: "Inconsistent drift course",
			audience: "Course Authors",
			goals: [],
			outcomes: [],
			lectures: [{ title: "Review the workspace" }],
		},
	});
	expect(createResponse.ok()).toBe(true);
	const workspaceResponse = await page.request.get("/api/workspace");
	const workspace = (await workspaceResponse.json()) as { path: string };
	const coursePath = join(workspace.path, "course.yaml");
	const course = await readFile(coursePath, "utf8");
	await writeFile(
		coursePath,
		course.replace("title: Inconsistent drift course", "title:"),
		"utf8",
	);

	await page.reload();
	await page.getByRole("button", { name: "Review changes" }).click();
	await expect(
		page.locator(".history-drift").getByRole("heading", {
			name: "Course files changed outside the app",
		}),
	).toBeVisible();
	await page.getByRole("button", { name: "Fix with the Course Agent" }).click();
	const composer = page.getByLabel("Message the Course Agent");
	await expect(composer).toBeVisible();
	await expect(composer).toBeFocused();
	await expect(composer).toHaveValue(/Course files changed outside the app/);
	await expect(page.locator(".context-chip")).toContainText("Outside changes");
	await expect(page.locator(".context-chip")).toHaveAttribute(
		"title",
		/Workspace Drift needs Reconciliation\./,
	);
});
