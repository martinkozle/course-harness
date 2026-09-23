import { expect, type Page, test } from "@playwright/test";

const PNG_BASE64 =
	"iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAAD0lEQVR4nGPkzythYGAAAAReAPOY+IFEAAAAAElFTkSuQmCC";

async function configureModel(page: Page) {
	await page.getByRole("button", { name: "Settings" }).click();
	const settings = page.getByRole("dialog", { name: "Settings" });
	await settings
		.getByRole("navigation", { name: "Settings sections" })
		.getByRole("button", { name: "Models" })
		.click();
	if (await settings.getByLabel("API key").isVisible()) {
		await settings.getByLabel("API key").fill("deterministic-test-key");
		await settings
			.getByRole("button", { name: "Save Provider Account" })
			.click();
		await expect(settings.getByLabel("Preset name")).toBeVisible();
	}
	if (await settings.getByLabel("Preset name").isVisible()) {
		await settings.getByLabel("Preset name").fill("Planning model");
		await settings.getByLabel("Model ID").fill("openai/gpt-oss-20b:free");
		await settings.getByRole("button", { name: "Save Model Preset" }).click();
	}
	await page.keyboard.press("Escape");
	await expect(settings).toBeHidden();
}

test("Course Author pastes an image into chat and the Course Agent puts it on a Slide", async ({
	page,
}) => {
	await page.request.post("/api/workspace/close");
	await page.goto("/");
	await page.getByRole("button", { name: "New course" }).click();
	await configureModel(page);

	const composer = page.getByLabel("Message the Course Agent");
	await composer.fill(
		"Create a practical causal inference Course for applied researchers.",
	);
	await composer.press("Enter");
	await expect(
		page.getByText("I created a two-Lecture Course Plan."),
	).toBeVisible();

	// Pasting an image attaches it to the next message without adding it to the Course.
	await composer.focus();
	await composer.evaluate((element, base64) => {
		const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
		const transfer = new DataTransfer();
		transfer.items.add(
			new File([bytes], "causal-graph.png", { type: "image/png" }),
		);
		element.dispatchEvent(
			new ClipboardEvent("paste", {
				clipboardData: transfer,
				bubbles: true,
				cancelable: true,
			}),
		);
	}, PNG_BASE64);
	const attached = page.getByRole("list", { name: "Attached images" });
	await expect(
		attached.getByRole("img", { name: "causal-graph.png" }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Send message" }),
	).toBeEnabled();
	expect(await (await page.request.get("/api/sources")).json()).toEqual([]);

	await composer.fill(
		"Use the attached image on a Slide in the first Lecture.",
	);
	await composer.press("Enter");
	await expect(attached).toBeHidden();
	await expect(
		page.getByText("I placed the attached image on a new Slide."),
	).toBeVisible();
	const sentImage = page
		.locator(".message.is-user")
		.getByRole("img", { name: "causal-graph.png" });
	await expect(sentImage).toBeVisible();
	await expect(sentImage).toHaveJSProperty("naturalWidth", 2);

	// The Course Agent admitted the image as a Source and placed it on an Image Slide.
	const sources = (await (await page.request.get("/api/sources")).json()) as {
		id: string;
		label: string;
	}[];
	expect(sources.map((source) => source.label)).toEqual(["causal-graph.png"]);
	const course = (await (await page.request.get("/api/course")).json()) as {
		lectures: { id: string }[];
	};
	const presentation = (await (
		await page.request.get(`/api/presentations/${course.lectures[0].id}`)
	).json()) as { slides: { layout: string; image_source_id?: string }[] };
	expect(presentation.slides.at(-1)).toMatchObject({
		layout: "image",
		image_source_id: sources[0].id,
	});
	const image = await page.request.get(`/api/sources/${sources[0].id}/image`);
	expect(image.headers()["content-type"]).toBe("image/png");

	// The Slide editor offers admitted image Sources.
	await page
		.getByRole("navigation", { name: "Course" })
		.getByRole("button", { name: /From association to intervention/ })
		.click();
	await page.getByRole("button", { name: "Edit Slide" }).click();
	const picker = page.getByLabel("Image", { exact: true });
	await expect(picker).toHaveValue(sources[0].id);
	await expect(picker.getByRole("option")).toHaveText([
		"No image",
		"causal-graph.png",
	]);
});
