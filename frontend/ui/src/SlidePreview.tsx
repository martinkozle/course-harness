import type { CSSProperties } from "react";

import { type PreviewSlot, type Slide, slideSummary } from "./slides";

function slotContent(slide: Slide, slot: string): string | null {
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
									? (slide.caption ?? (slide.image_source_id ? null : "Image"))
									: undefined;
	return value ?? null;
}

function slotStyle(slot: PreviewSlot): CSSProperties {
	return {
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

/**
 * A Slide drawn as its template's rendered background with selectable HTML text placed in the
 * layout's slots (ADR 0010). Without slots it falls back to the Slide's headline.
 */
export function SlidePreview({
	slide,
	slots,
	backgroundUrl,
	imageUrl,
	thumbnailUrl,
	aspectRatio,
	updating = false,
}: {
	slide: Slide;
	/** Slot geometry of the Slide's template layout, by semantic slot name. */
	slots: Record<string, PreviewSlot> | null;
	/** The rendered empty layout behind the text. */
	backgroundUrl?: string | null;
	/** The picture for the image slot of an Image Slide. */
	imageUrl?: string | null;
	/** An authoritative rendering of the whole Slide; replaces the text overlay when present. */
	thumbnailUrl?: string | null;
	/** Slide width divided by height. */
	aspectRatio: number;
	/** Marks the preview as about to change. */
	updating?: boolean;
}) {
	const entries = Object.entries(slots ?? {});
	return (
		<div
			className={`slide-preview ${thumbnailUrl ? "is-rendered" : "is-approximate"}`}
			style={{
				aspectRatio,
				...(backgroundUrl
					? { backgroundImage: `url("${backgroundUrl}")` }
					: {}),
			}}
		>
			{thumbnailUrl ? (
				<img src={thumbnailUrl} alt="" />
			) : (
				entries.map(([name, slot]) => {
					if (name === "image" && imageUrl)
						return (
							<img
								key={name}
								className="slide-slot slide-slot-image"
								style={slotStyle(slot)}
								src={imageUrl}
								alt=""
							/>
						);
					const content = slotContent(slide, name);
					return content ? (
						<span key={name} className="slide-slot" style={slotStyle(slot)}>
							{content}
						</span>
					) : null;
				})
			)}
			{!thumbnailUrl && entries.length === 0 ? (
				<span className="slide-fallback">{slideSummary(slide)}</span>
			) : null}
			{updating ? (
				<span className="slide-updating" aria-hidden="true">
					Updating…
				</span>
			) : null}
		</div>
	);
}
