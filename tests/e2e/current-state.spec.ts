import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

test("Course Author can inspect Current State before and after creating a Course", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	await page.getByRole("button", { name: "Current State" }).click();
	await expect(
		page.getByRole("heading", { name: "Current State" }),
	).toBeVisible();
	await expect(
		page.getByRole("heading", {
			name: "History starts when you create a Course Plan",
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
	await page.getByRole("button", { name: "Course Plan" }).click();
	await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
	await expect
		.poll(() => page.evaluate(() => window.scrollY))
		.toBeGreaterThan(0);
	await page.getByRole("button", { name: "Current State" }).click();
	await expect(
		page.getByRole("heading", { name: "Current State" }),
	).toBeInViewport();
	await expect(page.getByRole("status")).toContainText("up to date");
	const [refreshResponse] = await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().endsWith("/api/workspace/current-state") &&
				response.request().method() === "GET",
		),
		page.getByRole("button", { name: "Refresh" }).click(),
	]);
	expect(refreshResponse.ok()).toBe(true);
	await expect(page.getByText("Added")).toBeVisible();
	await expect(page.getByText("course.yaml", { exact: true })).toBeVisible();
	await expect(page.getByText(/total lines?/)).toBeVisible();
	await expect(page.getByText("Valid", { exact: true })).toBeVisible();
	await expect(page.getByRole("status")).toContainText("up to date");
	await page.getByRole("button", { name: "Revert" }).click();
	await expect(
		page.getByText("Remove this newly added file from Current State?"),
	).toBeVisible();
	await page.getByRole("button", { name: "Cancel" }).click();
	await page.getByLabel("Revision summary").fill("Initial course");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(page.getByText("Initial course")).toBeVisible();

	await page.request.patch(`/api/course/lectures/${lectureId}`, {
		data: { title: "Edited outside Current State" },
	});
	await page.getByRole("button", { name: "Refresh" }).click();
	await expect(page.getByText("Modified")).toBeVisible();
	await page.getByRole("button", { name: "Revert" }).click();
	await page.getByRole("button", { name: "Confirm revert" }).click();
	await expect(page.getByText("Clean", { exact: true })).toBeVisible();

	await page.request.patch(`/api/course/lectures/${lectureId}`, {
		data: { title: "First revision title" },
	});
	await page.getByRole("button", { name: "Refresh" }).click();
	await page.getByLabel("Revision summary").fill("First revision");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(page.getByText("First revision", { exact: true })).toBeVisible();

	await page.request.patch(`/api/course/lectures/${lectureId}`, {
		data: { title: "Second revision title" },
	});
	await page.getByRole("button", { name: "Refresh" }).click();
	await page.getByLabel("Revision summary").fill("Second revision");
	await page.getByRole("button", { name: "Create Course Revision" }).click();
	await expect(
		page.getByText("Second revision", { exact: true }),
	).toBeVisible();

	const firstRevision = page
		.locator(".revision-list > li")
		.filter({ hasText: "First revision" });
	await firstRevision.getByRole("button", { name: "Restore" }).click();
	await firstRevision.getByRole("button", { name: "Confirm restore" }).click();
	await expect(page.getByText("Current State is up to date.")).toBeVisible();
	const restoredCourse = await page.request.get("/api/course");
	expect((await restoredCourse.json()).lectures[0].title).toBe(
		"First revision title",
	);
});

test("Course Author accepts valid Workspace Drift as a Course Revision", async ({
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

	await page.getByRole("button", { name: "Current State" }).click();
	await expect(
		page.getByRole("heading", { name: "Workspace Drift detected" }),
	).toBeVisible();
	await expect(page.getByLabel("Workspace Drift paths")).toContainText(
		"course.yaml",
	);
	await page
		.getByLabel("Workspace Drift summary")
		.fill("Accept external Course title edit");
	await page.getByRole("button", { name: "Accept Workspace Drift" }).click();
	await expect(
		page.getByText("Create this Course Revision from Workspace Drift?"),
	).toBeVisible();
	const [acceptResponse] = await Promise.all([
		page.waitForResponse(
			(response) =>
				response.url().endsWith("/api/workspace/drift/accept") &&
				response.request().method() === "POST",
		),
		page.getByRole("button", { name: "Confirm acceptance" }).click(),
	]);
	expect(acceptResponse.ok()).toBe(true);
	await expect(
		page.getByText("Accept external Course title edit"),
	).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "No Workspace Drift" }),
	).toBeVisible();
});

test("Course Author can hand inconsistent Workspace Drift to the Course Agent", async ({
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

	await page.getByRole("button", { name: "Current State" }).click();
	await expect(
		page.getByRole("heading", { name: "Workspace Drift detected" }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Reconcile with Course Agent" }),
	).toBeVisible();
	await page
		.getByRole("button", { name: "Reconcile with Course Agent" })
		.click();
	await expect(page.getByLabel("Message the Course Agent")).toBeVisible();
	await expect(
		page.getByText("Workspace Drift needs Reconciliation.", { exact: false }),
	).toBeVisible();
});
