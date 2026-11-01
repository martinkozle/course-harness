import { type CitationOpening, safeHttpUrl } from "@course-harness/ui";

import type { ReaderTarget, SlideCitation, Source } from "../models";

/**
 * How a Citation opens in the canvas: the cited passage of a Course Source, or the Source's
 * address when it is no longer in the course.
 */
export function evidenceOpening(
	sources: Source[],
	onOpenEvidence: (target: ReaderTarget) => void,
): (citation: SlideCitation) => CitationOpening {
	return (citation) => {
		const source = sources.find((item) => item.id === citation.source_id);
		if (source)
			return {
				title: "Open the cited passage",
				onOpen: () =>
					onOpenEvidence({
						sourceId: citation.source_id,
						resourceId: null,
						label: source.label,
						lineStart: citation.line_start ?? null,
						lineEnd: citation.line_end ?? null,
					}),
			};
		const url = safeHttpUrl(citation.url);
		return {
			title: "This Source is no longer in the course",
			...(url
				? { onOpen: () => window.open(url, "_blank", "noopener,noreferrer") }
				: { disabled: true }),
		};
	};
}
