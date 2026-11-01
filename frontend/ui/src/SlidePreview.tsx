import type { CSSProperties } from "react";

import { type PreviewSlot, type Slide, slideSummary } from "./slides";

function slotContent(
	slide: Slide,
	slot: string,
	imageUrl: string | null | undefined,
): string | null {
	const value =
		slot === "title"
			? slide.title
			: slot === "subtitle"
				? slide.subtitle
				: slot === "body"
					? (slide.bullets?.join("\n") ??
						slide.code ??
						slide.quote ??
						slide.text)
					: slot === "left"
						? slide.left_content?.join("\n")
						: slot === "right"
							? slide.right_content?.join("\n")
							: slot === "statement"
								? slide.statement
								: slot === "image"
									? (slide.caption ?? (imageUrl ? null : "Image"))
									: undefined;
	return value ?? null;
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

/**
 * A Slide drawn as its template's rendered background with selectable HTML text placed in the
 * layout's slots (ADR 0010). Without slots it lays out the Slide's content in reading order, or
 * only its headline when `fallback` is "summary".
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
			{entries.map(([name, slot]) => {
				if (name === "image" && imageUrl)
					return rendered ? null : (
						<img
							key={name}
							className="slide-slot slide-slot-image"
							style={slotStyle(slot, Boolean(backgroundUrl))}
							src={imageUrl}
							alt=""
						/>
					);
				const content = slotContent(slide, name, imageUrl);
				return content ? (
					<span key={name} className="slide-slot" style={slotStyle(slot, Boolean(backgroundUrl))}>
						{content}
					</span>
				) : null;
			})}
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
