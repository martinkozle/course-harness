import { ChevronDown, Search } from "lucide-react";
import { useState } from "react";

import { CandidateRow } from "../canvas/SourcesCanvas";
import {
	type Candidate,
	candidateFromResearch,
	candidateResource,
	type ResearchCard,
	type ResourceState,
	type Source,
} from "../models";
import { Notice } from "../ui";
import type { Library } from "../useLibrary";

/** Candidates a research tool found during a run, shown beside the agent's reply. */
export function ResearchResults({
	cards,
	resources,
	sources,
	library,
}: {
	cards: ResearchCard[];
	resources: ResourceState[];
	sources: Source[];
	library: Library;
}) {
	const sourceByResource = new Map(
		sources.map((source) => [source.resource_id, source]),
	);
	return (
		<div className="research-results">
			{cards.map((card, index) => (
				<ResearchCardView
					// biome-ignore lint/suspicious/noArrayIndexKey: cards keep their order in a stored reply
					key={index}
					card={card}
					resources={resources}
					sourceByResource={sourceByResource}
					library={library}
				/>
			))}
		</div>
	);
}

function ResearchCardView({
	card,
	resources,
	sourceByResource,
	library,
}: {
	card: ResearchCard;
	resources: ResourceState[];
	sourceByResource: Map<string, Source>;
	library: Library;
}) {
	const [open, setOpen] = useState(false);
	const candidates: Candidate[] = card.candidates.map(candidateFromResearch);
	const included = candidates.filter((candidate) => {
		const resource = candidateResource(resources, candidate);
		return resource ? sourceByResource.has(resource.resource_id) : false;
	}).length;
	const errors = Object.entries(card.errors ?? {});
	return (
		<section className="research-card" aria-label={card.title}>
			<button
				type="button"
				className="research-summary"
				aria-expanded={open}
				onClick={() => setOpen((value) => !value)}
			>
				<Search aria-hidden="true" />
				<span className="research-title">{card.title}</span>
				<span className="research-count">
					{candidates.length} result{candidates.length === 1 ? "" : "s"}
					{included ? ` · ${included} in this course` : ""}
				</span>
				<ChevronDown aria-hidden="true" />
			</button>
			{open ? (
				<div className="research-body">
					<p className="meta">
						Results are not Sources until they are added to the course. Only
						added Sources can be cited.
					</p>
					{errors.map(([provider, message]) => (
						<Notice key={provider} tone="warning">
							{message}
						</Notice>
					))}
					{candidates.length ? (
						<ul className="row-list">
							{candidates.map((candidate) => (
								<CandidateRow
									key={candidate.url}
									candidate={candidate}
									resource={candidateResource(resources, candidate)}
									sourceByResource={sourceByResource}
									library={library}
									allowSave={false}
								/>
							))}
						</ul>
					) : null}
				</div>
			) : null}
		</section>
	);
}
