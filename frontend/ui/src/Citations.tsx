import { lineRangeLabel, type SlideCitation } from "./slides";
import { safeHttpUrl } from "./urls";

/**
 * How one Citation opens: a callback, or a link to an http(s) address. A Citation with neither
 * is plain text, or a disabled button when `disabled` is set.
 */
export type CitationOpening = {
	onOpen?: () => void;
	href?: string | null;
	disabled?: boolean;
	title?: string;
};

function linkTo(citation: SlideCitation): CitationOpening {
	const href = safeHttpUrl(citation.url);
	return { href, title: href ?? undefined };
}

/** A numbered chip naming a cited Source and, when known, the cited lines. */
export function CitationChip({
	citation,
	number,
	onOpen,
	href,
	disabled = false,
	title,
}: CitationOpening & { citation: SlideCitation; number: number }) {
	const content = (
		<>
			<span className="citation-marker">{number}</span>
			<span className="citation-label" title={citation.label}>
				{citation.label}
			</span>
			{citation.line_start != null ? (
				<span className="citation-where">
					{lineRangeLabel(citation.line_start, citation.line_end ?? null)}
				</span>
			) : null}
		</>
	);
	const link = safeHttpUrl(href);
	if (onOpen && !disabled)
		return (
			<button
				type="button"
				className="citation-chip"
				title={title}
				onClick={onOpen}
			>
				{content}
			</button>
		);
	if (link && !disabled)
		return (
			<a
				className="citation-chip"
				href={link}
				target="_blank"
				rel="noopener noreferrer"
				title={title}
			>
				{content}
				<span className="visually-hidden"> (opens in a new tab)</span>
			</a>
		);
	if (disabled)
		return (
			<button type="button" className="citation-chip" disabled title={title}>
				{content}
			</button>
		);
	return (
		<span className="citation-chip" title={title}>
			{content}
		</span>
	);
}

/** A Slide's Citations, numbered in order. By default each one links to its `url`. */
export function CitationList({
	citations,
	opening = linkTo,
}: {
	citations: SlideCitation[];
	opening?: (citation: SlideCitation) => CitationOpening;
}) {
	return (
		<ul className="citation-list">
			{citations.map((citation, index) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: Citations have no identity of their own
				<li key={`${citation.source_id}-${index}`}>
					<CitationChip
						citation={citation}
						number={index + 1}
						{...opening(citation)}
					/>
				</li>
			))}
		</ul>
	);
}
