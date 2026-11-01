import type { CSSProperties } from "react";

import { type PreviewSlot, type Slide, slideSummary } from "./slides";

/** The Slide fields a template slot can show; anything else is never drawn. */
type ContentField =
	| "title"
	| "subtitle"
	| "statement"
	| "bullets"
	| "left_content"
	| "right_content"
	| "text"
	| "code"
	| "quote"
	| "attribution"
	| "caption";

const CONTENT_FIELDS: ContentField[] = [
	"title",
	"subtitle",
	"statement",
	"bullets",
	"left_content",
	"right_content",
	"text",
	"code",
	"quote",
	"attribution",
	"caption",
];

function filled(value: string | string[] | undefined): boolean {
	return Array.isArray(value) ? value.length > 0 : Boolean(value);
}

/** What a slot shows, and which of the Slide's fields that uses up. */
function slotContent(
	slide: Slide,
	slot: string,
	imageUrl: string | null | undefined,
): { text: string; fields: ContentField[] } | null {
	const field = (name: ContentField) => {
		const value = slide[name];
		if (!filled(value)) return null;
		return {
			text: Array.isArray(value) ? value.join("\n") : (value as string),
			fields: [name],
		};
	};
	switch (slot) {
		case "title":
		case "subtitle":
		case "statement":
			return field(slot);
		case "left":
			return field("left_content");
		case "right":
			return field("right_content");
		case "body": {
			// Like the PPTX export: the first of these the Slide has, a quote with its attribution.
			if (filled(slide.quote))
				return {
					text: slide.attribution
						? `${slide.quote}\n— ${slide.attribution}`
						: (slide.quote as string),
					fields: ["quote", "attribution"],
				};
			return field("bullets") ?? field("code") ?? field("text");
		}
		case "image":
			return (
				field("caption") ??
				(imageUrl ? null : { text: "Image", fields: [] as ContentField[] })
			);
		default:
			return null;
	}
}

/** The Slide with only the fields no slot of its layout shows, or null when every one is shown. */
function leftoverSlide(slide: Slide, shown: Set<ContentField>): Slide | null {
	const rest: Slide = { ...slide };
	let left = false;
	for (const name of CONTENT_FIELDS) {
		if (shown.has(name) || !filled(slide[name])) delete rest[name];
		else left = true;
	}
	return left ? rest : null;
}

/**
 * Where content without a slot goes: under the title when one is shown and there is room,
 * otherwise centred on the slide. It takes the colour and typeface of the title slot, or of
 * another slot with a colour, so it reads the same way over the template's background.
 */
function leftoverStyle(
	slots: Record<string, PreviewSlot>,
	onBackground: boolean,
	titleShown: boolean,
): { style: CSSProperties; centred: boolean } {
	const title = slots.title;
	const titleBottom = title ? title.top + title.height : 1;
	const below = title !== undefined && titleShown && titleBottom <= 0.7;
	const reference = title ?? Object.values(slots).find((slot) => slot.color);
	const color = onBackground ? reference?.color : null;
	const box = below
		? {
				left: title.left,
				top: titleBottom + 0.04,
				width: title.width,
				height: 0.92 - titleBottom - 0.04,
			}
		: { left: 0.1, top: 0.15, width: 0.8, height: 0.7 };
	return {
		centred: !below,
		style: {
			...(color ? ({ "--slot-color": color } as CSSProperties) : {}),
			left: `${box.left * 100}%`,
			top: `${box.top * 100}%`,
			width: `${box.width * 100}%`,
			height: `${box.height * 100}%`,
			fontFamily: reference?.font_family,
		},
	};
}

/**
 * Where and how a slot's text sits. The template's text colour applies only over its rendered
 * background: without one the slide is plain white, where light template text would vanish.
 */
function slotStyle(slot: PreviewSlot, onBackground: boolean): CSSProperties {
	return {
		...(onBackground && slot.color
			? ({ "--slot-color": slot.color } as CSSProperties)
			: {}),
		left: `${slot.left * 100}%`,
		top: `${slot.top * 100}%`,
		width: `${slot.width * 100}%`,
		height: `${slot.height * 100}%`,
		fontFamily: slot.font_family,
		fontWeight: slot.bold ? 700 : 400,
		textAlign:
			slot.alignment === "center" || slot.alignment === "right"
				? slot.alignment
				: "left",
		fontSize: `clamp(4px, ${slot.font_size / 9.6}cqw, ${slot.font_size}px)`,
	};
}

function lines(items: string[] | undefined) {
	return items?.length ? (
		<ul>
			{items.map((item, index) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: list items may repeat
				<li key={index}>{item}</li>
			))}
		</ul>
	) : null;
}

/** Everything a Slide says, in reading order, for when there is no layout geometry. */
function SlideContent({
	slide,
	imageUrl,
	headingLevel,
}: {
	slide: Slide;
	imageUrl: string | null | undefined;
	headingLevel: 2 | 3 | 4;
}) {
	const Heading = `h${headingLevel}` as const;
	return (
		<>
			{slide.title ? <Heading>{slide.title}</Heading> : null}
			{slide.subtitle ? (
				<p className="slide-subtitle">{slide.subtitle}</p>
			) : null}
			{slide.statement ? (
				<p className="slide-statement">{slide.statement}</p>
			) : null}
			{lines(slide.bullets)}
			{slide.left_content?.length || slide.right_content?.length ? (
				<div className="slide-columns">
					{lines(slide.left_content) ?? <div />}
					{lines(slide.right_content)}
				</div>
			) : null}
			{slide.text ? <p>{slide.text}</p> : null}
			{slide.code ? (
				<pre>
					<code>{slide.code}</code>
				</pre>
			) : null}
			{slide.quote ? (
				<figure>
					<blockquote>{slide.quote}</blockquote>
					{slide.attribution ? (
						<figcaption>{slide.attribution}</figcaption>
					) : null}
				</figure>
			) : null}
			{imageUrl || slide.caption ? (
				<figure>
					{imageUrl ? <img src={imageUrl} alt="" /> : null}
					{slide.caption ? <figcaption>{slide.caption}</figcaption> : null}
				</figure>
			) : null}
		</>
	);
}

const EMPTY_CONTENT = Object.fromEntries(
	CONTENT_FIELDS.map((name) => [name, undefined]),
) as Partial<Slide>;

/**
 * A Slide drawn as its template's rendered background with selectable HTML text placed in the
 * layout's slots (ADR 0010). Content the layout has no slot for is laid out in a free area of
 * the Slide instead of being dropped. Without slots it lays out the Slide's content in reading
 * order, or only its headline when `fallback` is "summary".
 */
export function SlidePreview({
	slide,
	slots,
	backgroundUrl,
	imageUrl,
	thumbnailUrl,
	aspectRatio,
	updating = false,
	fallback = "content",
	headingLevel = 3,
}: {
	slide: Slide;
	/** Slot geometry of the Slide's template layout, by semantic slot name. */
	slots: Record<string, PreviewSlot> | null;
	/** The rendered empty layout behind the text. */
	backgroundUrl?: string | null;
	/** The picture for the image slot of an Image Slide. */
	imageUrl?: string | null;
	/**
	 * An authoritative rendering of the whole Slide. Its text stays on top, invisible but
	 * selectable and read by screen readers.
	 */
	thumbnailUrl?: string | null;
	/** Slide width divided by height. */
	aspectRatio: number;
	/** Marks the preview as about to change. */
	updating?: boolean;
	/** What to show without slot geometry: all of the Slide's content, or only its headline. */
	fallback?: "content" | "summary";
	/** The level of the Slide title's heading in the content fallback. */
	headingLevel?: 2 | 3 | 4;
}) {
	const entries = Object.entries(slots ?? {});
	const rendered = Boolean(thumbnailUrl);
	const onBackground = Boolean(backgroundUrl);
	// Every field a slot shows; whatever is left is drawn in a free area, never dropped.
	const shown = new Set<ContentField>();
	const placed = entries.map(([name, slot]) => {
		const content =
			name === "image" && imageUrl ? null : slotContent(slide, name, imageUrl);
		for (const field of content?.fields ?? []) shown.add(field);
		return { name, slot, content };
	});
	const imagePlaced = Boolean(imageUrl && slots?.image);
	const leftover = entries.length > 0 ? leftoverSlide(slide, shown) : null;
	const leftoverImage =
		entries.length > 0 && Boolean(imageUrl) && !imagePlaced && !rendered;
	const leftoverPlacement =
		slots && (leftover || leftoverImage)
			? leftoverStyle(slots, onBackground, shown.has("title"))
			: null;
	return (
		<div
			className={`slide-preview ${rendered ? "is-rendered" : "is-approximate"}`}
			style={{
				aspectRatio,
				...(backgroundUrl
					? { backgroundImage: `url("${backgroundUrl}")` }
					: {}),
			}}
		>
			{thumbnailUrl ? <img src={thumbnailUrl} alt="" /> : null}
			{placed.map(({ name, slot, content }) => {
				if (name === "image" && imageUrl)
					return rendered ? null : (
						<img
							key={name}
							className="slide-slot slide-slot-image"
							style={slotStyle(slot, onBackground)}
							src={imageUrl}
							alt=""
						/>
					);
				return content ? (
					<span
						key={name}
						className="slide-slot"
						style={slotStyle(slot, onBackground)}
					>
						{content.text}
					</span>
				) : null;
			})}
			{leftoverPlacement ? (
				<div
					className={`slide-leftover${leftoverPlacement.centred ? " is-centred" : ""}`}
					style={leftoverPlacement.style}
					data-testid="slide-leftover"
				>
					<SlideContent
						slide={leftover ?? { ...slide, ...EMPTY_CONTENT }}
						imageUrl={leftoverImage ? imageUrl : null}
						headingLevel={headingLevel}
					/>
				</div>
			) : null}
			{entries.length > 0 ? null : rendered ? (
				<div className="visually-hidden">
					<SlideContent
						slide={slide}
						imageUrl={null}
						headingLevel={headingLevel}
					/>
				</div>
			) : fallback === "summary" ? (
				<span className="slide-fallback">{slideSummary(slide)}</span>
			) : (
				<div className="slide-content">
					<SlideContent
						slide={slide}
						imageUrl={imageUrl}
						headingLevel={headingLevel}
					/>
				</div>
			)}
			{updating ? (
				<span className="slide-updating" aria-hidden="true">
					Updating…
				</span>
			) : null}
		</div>
	);
}
