import { useCallback, useEffect, useRef, useState } from "react";

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
	presentationVersion: number;
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
		if (
			citation.line_end != null &&
			citation.line_end !== citation.line_start
		) {
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
	const [editing, setEditing] = useState(false);
	const [editTitle, setEditTitle] = useState(slide.title ?? "");
	const [editNotes, setEditNotes] = useState(slide.speaker_notes ?? "");
	const [editBullets, setEditBullets] = useState((slide.bullets ?? []).join("\n"));
	const [editQuote, setEditQuote] = useState(slide.quote ?? "");
	const [editAttribution, setEditAttribution] = useState(slide.attribution ?? "");
	const [editStatement, setEditStatement] = useState(slide.statement ?? "");
	const [editCode, setEditCode] = useState(slide.code ?? "");
	const [editLeft, setEditLeft] = useState(slide.left_content ?? "");
	const [editRight, setEditRight] = useState(slide.right_content ?? "");
	const [saving, setSaving] = useState(false);
	const [saveError, setSaveError] = useState<string | null>(null);

	async function toggleArchive() {
		setSaveError(null);
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
		} catch (caught) {
			setSaveError(caught instanceof Error ? caught.message : "Could not update slide.");
		}
	}

	async function saveEdits() {
		setSaveError(null);
		setSaving(true);
		try {
			const patchBody: Record<string, unknown> = {};
			if (editTitle !== (slide.title ?? "")) patchBody["title"] = editTitle;
			if (editNotes !== (slide.speaker_notes ?? "")) patchBody["speaker_notes"] = editNotes;
			if (slide.layout === "bullets" && slide.bullets) {
				const newBullets = editBullets.split("\n").map((l) => l.trim()).filter(Boolean);
				if (JSON.stringify(newBullets) !== JSON.stringify(slide.bullets)) {
					patchBody["bullets"] = newBullets;
				}
			}
			if (slide.layout === "quote") {
				if (editQuote !== (slide.quote ?? "")) patchBody["quote"] = editQuote;
				if (editAttribution !== (slide.attribution ?? "")) patchBody["attribution"] = editAttribution;
			}
			if (slide.layout === "big_statement") {
				if (editStatement !== (slide.statement ?? "")) patchBody["statement"] = editStatement;
			}
			if (slide.layout === "code") {
				if (editCode !== (slide.code ?? "")) patchBody["code"] = editCode;
			}
			if (slide.layout === "two_column") {
				if (editLeft !== (slide.left_content ?? "")) patchBody["left_content"] = editLeft;
				if (editRight !== (slide.right_content ?? "")) patchBody["right_content"] = editRight;
			}
			if (Object.keys(patchBody).length === 0) {
				setEditing(false);
				return;
			}
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lectureId)}/slides/${encodeURIComponent(slide.id)}`,
				{
					method: "PATCH",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(patchBody),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			setEditing(false);
			onArchiveChange();
		} catch (caught) {
			setSaveError(caught instanceof Error ? caught.message : "Could not save changes.");
		} finally {
			setSaving(false);
		}
	}

	function startEditing() {
		setEditTitle(slide.title ?? "");
		setEditNotes(slide.speaker_notes ?? "");
		setEditBullets((slide.bullets ?? []).join("\n"));
		setEditQuote(slide.quote ?? "");
		setEditAttribution(slide.attribution ?? "");
		setEditStatement(slide.statement ?? "");
		setEditCode(slide.code ?? "");
		setEditLeft(slide.left_content ?? "");
		setEditRight(slide.right_content ?? "");
		setEditing(true);
	}

	return (
		<section className="slide-detail" aria-label={`Slide ${slide.id} detail`}>
			<div className="slide-detail-header">
				{editing ? (
					<input
						className="slide-title-edit"
						value={editTitle}
						onChange={(e) => setEditTitle(e.target.value)}
						placeholder="Slide title"
					/>
				) : (
					<h3>{slide.title || "Untitled slide"}</h3>
				)}
				<div className="slide-detail-actions">
					{editing ? (
						<>
							<button
								className="quiet-action compact-action"
								type="button"
								onClick={() => setEditing(false)}
								disabled={saving}
							>
								Cancel
							</button>
							<button
								className="primary-action compact-action"
								type="button"
								onClick={() => void saveEdits()}
								disabled={saving || busy}
							>
								Save
							</button>
						</>
					) : (
						<>
							<button
								className="quiet-action compact-action"
								type="button"
								disabled={busy}
								onClick={startEditing}
							>
								Edit
							</button>
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
						</>
					)}
				</div>
			</div>
			<div className="slide-detail-meta">
				<span className="slide-layout-badge">{layoutLabel(slide.layout)}</span>
				{slide.archived ? (
					<span className="status-badge status-unprocessed">Archived</span>
				) : null}
				{slide.purpose && !editing ? (
					<p className="slide-purpose">{slide.purpose}</p>
				) : null}
			</div>

			{editing ? (
				<div className="slide-edit-fields">
					{slide.layout === "bullets" ? (
						<div className="field">
							<label htmlFor={`slide-${slide.id}-bullets`}>Bullets (one per line)</label>
							<textarea
								id={`slide-${slide.id}-bullets`}
								value={editBullets}
								onChange={(e) => setEditBullets(e.target.value)}
								rows={Math.max(4, editBullets.split("\n").length + 2)}
							/>
						</div>
					) : slide.layout === "quote" ? (
						<>
							<div className="field">
								<label htmlFor={`slide-${slide.id}-quote`}>Quote</label>
								<textarea
									id={`slide-${slide.id}-quote`}
									value={editQuote}
									onChange={(e) => setEditQuote(e.target.value)}
									rows={3}
								/>
							</div>
							<div className="field">
								<label htmlFor={`slide-${slide.id}-attribution`}>Attribution</label>
								<input
									id={`slide-${slide.id}-attribution`}
									value={editAttribution}
									onChange={(e) => setEditAttribution(e.target.value)}
								/>
							</div>
						</>
					) : slide.layout === "big_statement" ? (
						<div className="field">
							<label htmlFor={`slide-${slide.id}-statement`}>Statement</label>
							<textarea
								id={`slide-${slide.id}-statement`}
								value={editStatement}
								onChange={(e) => setEditStatement(e.target.value)}
								rows={3}
							/>
						</div>
					) : slide.layout === "code" ? (
						<div className="field">
							<label htmlFor={`slide-${slide.id}-code`}>Code</label>
							<textarea
								id={`slide-${slide.id}-code`}
								className="code-edit"
								value={editCode}
								onChange={(e) => setEditCode(e.target.value)}
								rows={Math.max(6, editCode.split("\n").length + 2)}
							/>
						</div>
					) : slide.layout === "two_column" ? (
						<>
							<div className="field">
								<label htmlFor={`slide-${slide.id}-left`}>Left column</label>
								<textarea
									id={`slide-${slide.id}-left`}
									value={editLeft}
									onChange={(e) => setEditLeft(e.target.value)}
									rows={3}
								/>
							</div>
							<div className="field">
								<label htmlFor={`slide-${slide.id}-right`}>Right column</label>
								<textarea
									id={`slide-${slide.id}-right`}
									value={editRight}
									onChange={(e) => setEditRight(e.target.value)}
									rows={3}
								/>
							</div>
						</>
					) : null}
					<div className="field">
						<label htmlFor={`slide-${slide.id}-notes`}>Speaker notes</label>
						<textarea
							id={`slide-${slide.id}-notes`}
							value={editNotes}
							onChange={(e) => setEditNotes(e.target.value)}
							rows={3}
							placeholder="Notes for the presenter…"
						/>
					</div>
				</div>
			) : (
				<>
					{saveError ? (
						<p className="notice error-notice" role="alert">{saveError}</p>
					) : null}
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
				</>
			)}

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
	presentationVersion,
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
	const [showArchived, setShowArchived] = useState(false);
	const prevVersion = useRef(presentationVersion);

	const loadPresentation = useCallback(async (lectureId: string) => {
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
				caught instanceof Error
					? caught.message
					: "Could not load presentation.",
			);
		}
	}, []);

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
		if (presentationVersion !== prevVersion.current) {
			prevVersion.current = presentationVersion;
			if (selectedLectureId) {
				void loadPresentation(selectedLectureId);
			}
		}
	}, [presentationVersion, selectedLectureId, loadPresentation]);

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
			setError(
				caught instanceof Error ? caught.message : "Could not reorder slides.",
			);
		}
	}

	const selectedSlide =
		selectedSlideId && currentPresentation
			? (currentPresentation.slides.find((s) => s.id === selectedSlideId) ??
				null)
			: null;

	const activeSlides = currentPresentation
		? currentPresentation.slides.filter((s) => !s.archived)
		: [];
	const archivedSlides = currentPresentation
		? currentPresentation.slides.filter((s) => s.archived)
		: [];

	return (
		<main
			className="page-main presentation-main"
			aria-labelledby="presentation-heading"
		>
			<header className="page-heading presentation-heading">
				<p className="eyebrow">Presentations</p>
				<h1 id="presentation-heading">Slide canvas</h1>
				<p className="lede">
					Select a Lecture to author or review its Presentation slides.
				</p>
				<button
					type="button"
					className="secondary-action compact-action"
					onClick={() =>
						onChatContext("I'm working on the course presentations")
					}
				>
					Ask the agent about presentations
				</button>
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
								<li key={lecture.id} className="lecture-selector-row">
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
									<button
										type="button"
										className="quiet-action compact-action lecture-context-btn"
										aria-label={`Chat about lecture: ${lecture.title}`}
										title={`Chat about lecture: ${lecture.title}`}
										onClick={() =>
											onChatContext(
												`I'm looking at the lecture "${lecture.title}" (id: ${lecture.id})`,
											)
										}
									>
										→
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
							<button
								type="button"
								className="primary-action compact-action"
								disabled={busy}
								onClick={() =>
									onChatContext(
										`Create a presentation for the lecture "${course.lectures.find((l) => l.id === selectedLectureId)?.title ?? selectedLectureId}"`,
									)
								}
							>
								Create presentation
							</button>
						</div>
					) : (
						<>
							<div className="content-section-heading">
								<div>
									<p className="section-kicker">
										{course.lectures.find((l) => l.id === selectedLectureId)
											?.title ?? "Lecture"}
									</p>
									<h2 id="slide-canvas-heading">Slides</h2>
								</div>
								<div className="section-actions">
									<button
										type="button"
										className="quiet-action compact-action"
										disabled={busy}
										onClick={() =>
											onChatContext(
												`Refine the presentation for the lecture "${course.lectures.find((l) => l.id === selectedLectureId)?.title ?? selectedLectureId}"`,
											)
										}
									>
										Refine slides
									</button>
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
										No active slides.
										<button
											type="button"
											className="quiet-action compact-action"
											style={{ marginLeft: "0.5rem" }}
											onClick={() =>
												onChatContext(
													`Create slides for the lecture "${course.lectures.find((l) => l.id === selectedLectureId)?.title ?? selectedLectureId}"`,
												)
											}
										>
											Ask the agent to create slides
										</button>
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
															disabled={busy || idx === activeSlides.length - 1}
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
														onClick={() => setSelectedSlideId(slide.id)}
													>
														<span className="slide-number" aria-hidden="true">
															{String(idx + 1).padStart(2, "0")}
														</span>
														<div className="slide-card-body">
															<div className="slide-card-header">
																<span className="slide-layout-badge">
																	{layoutLabel(slide.layout)}
																</span>
																{slide.citations.length > 0 ? (
																	<span className="citation-count-badge">
																		{slide.citations.length} cite
																		{slide.citations.length !== 1 ? "s" : ""}
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
											onClick={() => setShowArchived(!showArchived)}
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
															onClick={() => setSelectedSlideId(slide.id)}
														>
															<span className="slide-number" aria-hidden="true">
																—
															</span>
															<div className="slide-card-body">
																<div className="slide-card-header">
																	<span className="slide-layout-badge">
																		{layoutLabel(slide.layout)}
																	</span>
																	<span className="status-badge status-unprocessed">
																		Archived
																	</span>
																</div>
																<p className="slide-card-preview">
																	{slidePreview(slide)}
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
