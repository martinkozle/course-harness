import { lineRangeLabel, type SlideCitation } from "./slides";

/** How one Citation opens: a callback, a link, or neither when it cannot be opened. */
export type CitationOpening = {
	onOpen?: () => void;
	href?: string;
	title?: string;
};

function linkTo(citation: SlideCitation): CitationOpening {
	return { href: citation.url, title: citation.url };
}

/** A numbered chip naming a cited Source and, when known, the cited lines. */
export function CitationChip({
	citation,
	number,
	onOpen,
	href,
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
	if (!onOpen && href)
		return (
			<a
				className="citation-chip"
				href={href}
				target="_blank"
				rel="noopener noreferrer"
				title={title}
			>
				{content}
			</a>
		);
	return (
		<button
			type="button"
			className="citation-chip"
			disabled={!onOpen}
			title={title}
			onClick={onOpen}
		>
			{content}
		</button>
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
