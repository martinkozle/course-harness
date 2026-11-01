// The canonical Slide schema of a Presentation, shared by every interface that shows Slides.

export type SlideLayout =
	| "title"
	| "section"
	| "bullets"
	| "two_column"
	| "big_statement"
	| "closing"
	| "code"
	| "image"
	| "quote";

export type SlideCitation = {
	source_id: string;
	label: string;
	url?: string;
	line_start?: number;
	line_end?: number;
};

export type Slide = {
	id: string;
	layout: SlideLayout;
	title?: string;
	subtitle?: string;
	bullets?: string[];
	left_content?: string[];
	right_content?: string[];
	statement?: string;
	text?: string;
	code?: string;
	language?: string;
	image_source_id?: string | null;
	image_url?: string;
	caption?: string;
	quote?: string;
	attribution?: string;
	speaker_notes?: string;
	purpose?: string;
	citations: SlideCitation[];
	archived: boolean;
};

/** Where a template layout places one semantic slot, as fractions of the slide size. */
export type PreviewSlot = {
	left: number;
	top: number;
	width: number;
	height: number;
	font_family: string;
	font_size: number;
	bold: boolean;
	alignment: string | null;
	/** The template's text colour as `#RRGGBB`; absent in previews made before it was resolved. */
	color?: string | null;
};

export function slideSummary(slide: Slide): string {
	return (
		slide.title ||
		slide.statement ||
		slide.quote ||
		slide.bullets?.[0] ||
		slide.text ||
		"Untitled Slide"
	);
}

/** A zero-based line range as Course Authors read it, such as "Lines 3–5". */
export function lineRangeLabel(
	lineStart: number | null,
	lineEnd: number | null,
): string | null {
	if (lineStart === null) return null;
	if (lineEnd === null || lineEnd === lineStart) return `Line ${lineStart + 1}`;
	return `Lines ${lineStart + 1}–${lineEnd + 1}`;
}
