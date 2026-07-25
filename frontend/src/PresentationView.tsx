import { useCallback, useState } from "react";

import type { CoursePlan } from "./AgentPanel";
import { responseError } from "./api";
import type {
	Presentation,
	PresentationSummary,
	Slide,
	SlideCitation,
} from "./models";

type PresentationViewProps = {
	course: CoursePlan;
	busy: boolean;
	presentations: PresentationSummary[];
	onChange: () => Promise<void>;
	onChatContext: (instruction: string) => void;
};

function layoutLabel(layout: string): string {
	const labels: Record<string, string> = {
		title: "Title",
		section: "Section",
		bullets: "Bullets",
		two_column: "Two column",
		big_statement: "Big statement",
		closing: "Closing",
		code: "Code",
		image: "Image",
		quote: "Quote",
	};
	return labels[layout] ?? layout;
}

function slidePreview(slide: Slide): string {
	const content = slide.title ?? "";
	if (content) return content;
	if (slide.bullets && slide.bullets.length > 0) return slide.bullets[0];
	if (slide.quote) return `"${slide.quote.slice(0, 60)}…"`;
	if (slide.statement) return slide.statement.slice(0, 80);
	if (slide.code)
		return slide.code.split("\n")[0].slice(0, 60);
	if (slide.text) return slide.text.slice(0, 80);
	return "(empty)";
}

function citationDetail(citation: SlideCitation): string {
	const parts: string[] = [citation.source_id.slice(0, 12)];
	if (citation.line_start != null) {
		parts.push(`L${citation.line_start + 1}`);
		if (citation.line_end != null && citation.line_end !== citation.line_start) {
			parts.push(`–${citation.line_end}`);
		}
	}
	return parts.join(" ");
}

function SlideDetail({
	slide,
	onClose,
	onChatContext,
}: {
	slide: Slide;
	onClose: () => void;
	onChatContext: (instruction: string) => void;
}) {
	return (
		<section className="slide-detail" aria-label={`Slide ${slide.id} detail`}>
			<div className="slide-detail-header">
				<h3>
					{slide.title || "Untitled slide"}
				</h3>
				<button
					className="quiet-action compact-action"
					type="button"
					onClick={onClose}
					aria-label="Close slide detail"
				>
					Close
				</button>
			</div>
			<div className="slide-detail-meta">
				<span className="slide-layout-badge">{layoutLabel(slide.layout)}</span>
				{slide.purpose ? <p className="slide-purpose">{slide.purpose}</p> : null}
			</div>

			{slide.layout === "bullets" && slide.bullets ? (
				<ul className="slide-bullets">
					{slide.bullets.map((bullet, position) => (
						<li key={`${slide.id}-${bullet}`}>
							<button
								type="button"
								className="clickable-content"
								onClick={() =>
									onChatContext(
										`I'm looking at slide ${slide.id} (layout: ${slide.layout}), bullet ${String(position + 1)}: "${bullet}"`,
									)
								}
							>
								{bullet}
							</button>
						</li>
					))}
				</ul>
			) : slide.layout === "code" && slide.code ? (
				<pre
					className="slide-code-block"
					onClick={() =>
						onChatContext(
							`I'm looking at slide ${slide.id} (layout: ${slide.layout}, code block)`,
						)
					}
					onKeyDown={(e) => {
						if (e.key === "Enter") {
							onChatContext(
								`I'm looking at slide ${slide.id} (layout: ${slide.layout}, code block)`,
							);
						}
					}}
				>
					<code>{slide.code}</code>
				</pre>
			) : slide.layout === "two_column" ? (
				<div className="slide-two-column">
					<button
						type="button"
						className="clickable-content"
						onClick={() =>
							onChatContext(
								`I'm looking at slide ${slide.id} (layout: two_column, left column)`,
							)
						}
					>
						{slide.left_content}
					</button>
					<button
						type="button"
						className="clickable-content"
						onClick={() =>
							onChatContext(
								`I'm looking at slide ${slide.id} (layout: two_column, right column)`,
							)
						}
					>
						{slide.right_content}
					</button>
				</div>
			) : slide.quote ? (
				<blockquote
					className="slide-quote"
					onClick={() =>
						onChatContext(
							`I'm looking at slide ${slide.id} (layout: quote)`,
						)
					}
					onKeyDown={(e) => {
						if (e.key === "Enter") {
							onChatContext(
								`I'm looking at slide ${slide.id} (layout: quote)`,
							);
						}
					}}
				>
					<p>{slide.quote}</p>
					{slide.attribution ? (
						<footer>{slide.attribution}</footer>
					) : null}
				</blockquote>
			) : slide.statement ? (
				<p
					className="slide-statement clickable-content"
					onClick={() =>
						onChatContext(
							`I'm looking at slide ${slide.id} (layout: big_statement): "${slide.statement}"`,
						)
					}
					onKeyDown={(e) => {
						if (e.key === "Enter") {
							onChatContext(
								`I'm looking at slide ${slide.id} (layout: big_statement): "${slide.statement}"`,
							);
						}
					}}
				>
					{slide.statement}
				</p>
			) : null}

			{slide.speaker_notes ? (
				<div className="slide-speaker-notes">
					<h4>Speaker notes</h4>
					<p>{slide.speaker_notes}</p>
				</div>
			) : null}

			{slide.citations.length > 0 ? (
				<div className="slide-citations-section">
					<h4>Citations</h4>
					<ul className="citation-list">
						{slide.citations.map((citation) => (
							<li key={`${slide.id}-${citation.source_id}`}>
								<button
									type="button"
									className="citation-button"
									onClick={() =>
										onChatContext(
											`This slide cites ${citation.source_id} ("${citation.label}")`,
										)
									}
								>
									<span className="citation-label">{citation.label}</span>
									{citation.url ? (
										<a
											href={citation.url}
											target="_blank"
											rel="noopener noreferrer"
											className="citation-url"
											onClick={(e) => e.stopPropagation()}
										>
											Open
										</a>
									) : null}
									<small className="citation-coordinates">
										{citationDetail(citation)}
									</small>
								</button>
							</li>
						))}
					</ul>
				</div>
			) : null}
		</section>
	);
}

export function PresentationView({
	course,
	busy,
	presentations,
	onChange,
	onChatContext,
}: PresentationViewProps) {
	const [selectedLectureId, setSelectedLectureId] = useState<string | null>(
		null,
	);
	const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);
	const [currentPresentation, setCurrentPresentation] =
		useState<Presentation | null>(null);
	const [error, setError] = useState<string | null>(null);

	const selectLecture = useCallback(
		async (lectureId: string) => {
			setSelectedLectureId(lectureId);
			setSelectedSlideId(null);
			setCurrentPresentation(null);
			setError(null);
			try {
				const response = await fetch(
					`/api/presentations/${encodeURIComponent(lectureId)}`,
				);
				if (response.status === 404) {
					setCurrentPresentation(null);
					return;
				}
				if (!response.ok) {
					throw new Error(await responseError(response));
				}
				setCurrentPresentation((await response.json()) as Presentation);
			} catch (caught) {
				setError(
					caught instanceof Error ? caught.message : "Could not load presentation.",
				);
			}
		},
		[],
	);

	async function deletePresentation() {
		if (!selectedLectureId) return;
		setError(null);
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(selectedLectureId)}`,
				{ method: "DELETE" },
			);
			if (!response.ok && response.status !== 404) {
				throw new Error(await responseError(response));
			}
			setCurrentPresentation(null);
			await onChange();
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "Could not delete presentation.",
			);
		}
	}

	const selectedSlide = selectedSlideId && currentPresentation
		? currentPresentation.slides.find((s) => s.id === selectedSlideId) ?? null
		: null;

	return (
		<main className="page-main presentation-main" aria-labelledby="presentation-heading">
			<header className="page-heading presentation-heading">
				<p className="eyebrow">Presentations</p>
				<h1 id="presentation-heading">Slide canvas</h1>
				<p className="lede">
					Select a Lecture to author or review its Presentation slides.
				</p>
			</header>

			<div className="presentation-layout">
				<section
					className="lecture-selector-section"
					aria-labelledby="lecture-selector-heading"
				>
					<div className="content-section-heading">
						<div>
							<p className="section-kicker">From syllabus</p>
							<h2 id="lecture-selector-heading">Lectures</h2>
						</div>
					</div>
					<ol className="lecture-selector-list">
						{course.lectures.map((lecture, index) => {
							const hasPres = presentations.some(
								(p) => p.lecture_id === lecture.id,
							);
							return (
								<li key={lecture.id}>
									<button
										type="button"
										aria-current={
											selectedLectureId === lecture.id
												? "true"
												: undefined
										}
										disabled={busy}
										onClick={() => void selectLecture(lecture.id)}
									>
										<span className="lecture-index" aria-hidden="true">
											{String(index + 1).padStart(2, "0")}
										</span>
										<span className="lecture-title">{lecture.title}</span>
										{hasPres ? (
											<span className="presentation-indicator" role="img" aria-label="Has presentation">
												¶
											</span>
										) : null}
									</button>
								</li>
							);
						})}
					</ol>
				</section>

				<section
					className="slide-canvas-section"
					aria-labelledby="slide-canvas-heading"
				>
					{error ? (
						<p className="notice error-notice" role="alert">
							{error}
						</p>
					) : null}

					{!selectedLectureId ? (
						<div className="empty-state">
							<p>Select a Lecture to view its slide canvas.</p>
						</div>
					) : !currentPresentation ? (
						<div className="empty-state">
							<p>No Presentation for this Lecture yet.</p>
							<span>Ask the Course Agent to create a slide outline.</span>
						</div>
					) : (
						<>
							<div className="content-section-heading">
								<div>
									<p className="section-kicker">
										{course.lectures.find(
											(l) => l.id === selectedLectureId,
										)?.title ?? "Lecture"}
									</p>
									<h2 id="slide-canvas-heading">Slides</h2>
								</div>
								<div className="section-actions">
									<button
										className="quiet-action compact-action"
										type="button"
										disabled={busy}
										onClick={() => void deletePresentation()}
									>
										Delete presentation
									</button>
								</div>
							</div>

							<div className="slide-canvas">
								{selectedSlide ? (
									<SlideDetail
										slide={selectedSlide}
										onClose={() => setSelectedSlideId(null)}
										onChatContext={onChatContext}
									/>
								) : currentPresentation.slides.length === 0 ? (
									<p className="empty-note">
										No slides yet. Ask the Course Agent to create a
										presentation outline.
									</p>
								) : (
									<ol className="slide-list">
										{currentPresentation.slides.map(
											(slide, index) => (
												<li key={slide.id}>
													<button
														type="button"
														className="slide-card"
														onClick={() =>
															setSelectedSlideId(
																slide.id,
															)
														}
													>
														<span
															className="slide-number"
															aria-hidden="true"
														>
															{String(index + 1).padStart(
																2,
																"0",
															)}
														</span>
														<div className="slide-card-body">
															<div className="slide-card-header">
																<span className="slide-layout-badge">
																	{layoutLabel(
																		slide.layout,
																	)}
																</span>
																{slide.citations.length > 0 ? (
																	<span className="citation-count-badge">
																		{
																			slide
																				.citations
																				.length
																		}{" "}
																		cite
																		{slide
																			.citations
																			.length !==
																		1
																			? "s"
																			: ""}
																	</span>
																) : null}
																{slide.speaker_notes ? (
																	<span className="notes-indicator" role="img" aria-label="Has speaker notes">
																		🎙
																	</span>
																) : null}
															</div>
															<p className="slide-card-preview">
																{slidePreview(slide)}
															</p>
														</div>
													</button>
												</li>
											),
										)}
									</ol>
								)}
							</div>
						</>
					)}
				</section>
			</div>
		</main>
	);
}
