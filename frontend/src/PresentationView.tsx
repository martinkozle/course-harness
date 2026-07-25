import { useCallback, useEffect, useRef, useState } from "react";

import type { CoursePlan } from "./AgentPanel";
import { responseError } from "./api";
import type { Presentation, PresentationSummary, Slide, SlideCitation } from "./models";

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
	if (slide.code) return slide.code.split("\n")[0].slice(0, 60);
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
	lectureId,
	onClose,
	onChatContext,
	onArchiveChange,
	busy,
}: {
	slide: Slide;
	lectureId: string;
	onClose: () => void;
	onChatContext: (instruction: string) => void;
	onArchiveChange: () => void;
	busy: boolean;
}) {
	async function toggleArchive() {
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lectureId)}/slides/${encodeURIComponent(slide.id)}`,
				{
					method: "PATCH",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ archived: !slide.archived }),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			onArchiveChange();
		} catch {}
	}

	return (
		<section className="slide-detail" aria-label={`Slide ${slide.id} detail`}>
			<div className="slide-detail-header">
				<h3>{slide.title || "Untitled slide"}</h3>
				<div className="slide-detail-actions">
					<button
						className="quiet-action compact-action"
						type="button"
						disabled={busy}
						onClick={() => void toggleArchive()}
					>
						{slide.archived ? "Restore" : "Archive"}
					</button>
					<button
						className="quiet-action compact-action"
						type="button"
						onClick={onClose}
						aria-label="Close slide detail"
					>
						Close
					</button>
				</div>
			</div>
			<div className="slide-detail-meta">
				<span className="slide-layout-badge">{layoutLabel(slide.layout)}</span>
				{slide.archived ? (
					<span className="status-badge status-unprocessed">Archived</span>
				) : null}
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
				<pre className="slide-code-block">
					<code>{slide.code}</code>
				</pre>
			) : slide.layout === "two_column" ? (
				<div className="slide-two-column">
					<div className="clickable-content">{slide.left_content}</div>
					<div className="clickable-content">{slide.right_content}</div>
				</div>
			) : slide.quote ? (
				<blockquote className="slide-quote">
					<p>{slide.quote}</p>
					{slide.attribution ? <footer>{slide.attribution}</footer> : null}
				</blockquote>
			) : slide.statement ? (
				<p className="slide-statement">{slide.statement}</p>
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
	const [selectedLectureId, setSelectedLectureId] = useState<string | null>(null);
	const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);
	const [currentPresentation, setCurrentPresentation] = useState<Presentation | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [showArchived, setShowArchived] = useState(false);
	const prevPresentationsLen = useRef(presentations.length);

	const loadPresentation = useCallback(
		async (lectureId: string) => {
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

	const selectLecture = useCallback(
		async (lectureId: string) => {
			setSelectedLectureId(lectureId);
			setSelectedSlideId(null);
			setCurrentPresentation(null);
			setError(null);
			setShowArchived(false);
			await loadPresentation(lectureId);
		},
		[loadPresentation],
	);

	useEffect(() => {
		if (presentations.length !== prevPresentationsLen.current) {
			prevPresentationsLen.current = presentations.length;
			if (selectedLectureId) {
				void loadPresentation(selectedLectureId);
			}
		}
	}, [presentations.length, selectedLectureId, loadPresentation]);

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
				caught instanceof Error ? caught.message : "Could not delete presentation.",
			);
		}
	}

	async function moveSlide(slideId: string, direction: "up" | "down") {
		if (!selectedLectureId || !currentPresentation) return;
		const active = currentPresentation.slides.filter((s) => !s.archived);
		const idx = active.findIndex((s) => s.id === slideId);
		if (idx < 0) return;
		const target = direction === "up" ? idx - 1 : idx + 1;
		if (target < 0 || target >= active.length) return;
		const reordered = [...active];
		[reordered[idx], reordered[target]] = [reordered[target], reordered[idx]];
		setError(null);
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(selectedLectureId)}/slides/order`,
				{
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ slide_ids: reordered.map((s) => s.id) }),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			setCurrentPresentation((await response.json()) as Presentation);
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : "Could not reorder slides.");
		}
	}

	const selectedSlide =
		selectedSlideId && currentPresentation
			? currentPresentation.slides.find((s) => s.id === selectedSlideId) ?? null
			: null;

	const activeSlides = currentPresentation
		? currentPresentation.slides.filter((s) => !s.archived)
		: [];
	const archivedSlides = currentPresentation
		? currentPresentation.slides.filter((s) => s.archived)
		: [];

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
											selectedLectureId === lecture.id ? "true" : undefined
										}
										disabled={busy}
										onClick={() => void selectLecture(lecture.id)}
									>
										<span className="lecture-index" aria-hidden="true">
											{String(index + 1).padStart(2, "0")}
										</span>
										<span className="lecture-title">{lecture.title}</span>
										{hasPres ? (
											<span
												className="presentation-indicator"
												role="img"
												aria-label="Has presentation"
											>
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
										lectureId={selectedLectureId}
										onClose={() => setSelectedSlideId(null)}
										onChatContext={onChatContext}
										onArchiveChange={() => {
											void loadPresentation(selectedLectureId);
											setSelectedSlideId(null);
										}}
										busy={busy}
									/>
								) : activeSlides.length === 0 ? (
									<p className="empty-note">
										No active slides. Ask the Course Agent to create a
										presentation outline.
									</p>
								) : (
									<ol className="slide-list">
										{activeSlides.map((slide, idx) => (
											<li key={slide.id}>
												<div className="slide-row">
													<fieldset
														className="slide-order-actions"
														aria-label={`Reorder slide ${slide.id}`}
													>
														<button
															type="button"
															className="slide-order-btn"
															disabled={busy || idx === 0}
															title="Move up"
															aria-label="Move slide up"
															onClick={(e) => {
																e.stopPropagation();
																void moveSlide(slide.id, "up");
															}}
														>
															↑
														</button>
														<button
															type="button"
															className="slide-order-btn"
															disabled={
																busy ||
																idx === activeSlides.length - 1
															}
															title="Move down"
															aria-label="Move slide down"
															onClick={(e) => {
																e.stopPropagation();
																void moveSlide(slide.id, "down");
															}}
														>
															↓
														</button>
													</fieldset>
													<button
														type="button"
														className="slide-card"
														onClick={() =>
															setSelectedSlideId(slide.id)
														}
													>
														<span
															className="slide-number"
															aria-hidden="true"
														>
															{String(idx + 1).padStart(
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
																{slide.citations.length >
																0 ? (
																	<span className="citation-count-badge">
																		{slide.citations.length}{" "}
																		cite
																		{slide.citations
																			.length !==
																		1
																			? "s"
																			: ""}
																	</span>
																) : null}
																{slide.speaker_notes ? (
																	<span
																		className="notes-indicator"
																		role="img"
																		aria-label="Has speaker notes"
																	>
																		🎙
																	</span>
																) : null}
															</div>
															<p className="slide-card-preview">
																{slidePreview(slide)}
															</p>
														</div>
													</button>
												</div>
											</li>
										))}
									</ol>
								)}

								{archivedSlides.length > 0 ? (
									<div className="archived-section">
										<button
											type="button"
											className="archived-toggle"
											aria-expanded={showArchived}
											onClick={() =>
												setShowArchived(!showArchived)
											}
										>
											Archived slides ({archivedSlides.length})
										</button>
										{showArchived ? (
											<ol className="slide-list archived-list">
												{archivedSlides.map((slide) => (
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
																—
															</span>
															<div className="slide-card-body">
																<div className="slide-card-header">
																	<span className="slide-layout-badge">
																		{layoutLabel(
																			slide.layout,
																		)}
																	</span>
																	<span className="status-badge status-unprocessed">
																		Archived
																	</span>
																</div>
																<p className="slide-card-preview">
																	{slidePreview(
																		slide,
																	)}
																</p>
															</div>
														</button>
													</li>
												))}
											</ol>
										) : null}
									</div>
								) : null}
							</div>
						</>
					)}
				</section>
			</div>
		</main>
	);
}
