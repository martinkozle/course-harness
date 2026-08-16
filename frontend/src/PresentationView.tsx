import { useCallback, useEffect, useRef, useState } from "react";

import type { CoursePlan } from "./AgentPanel";
import { responseError } from "./api";
import type {
	Presentation,
	PresentationSummary,
	Slide,
	SlideCitation,
	TemplateProfileSummary,
} from "./models";

type PresentationViewProps = {
	course: CoursePlan;
	busy: boolean;
	presentations: PresentationSummary[];
	presentationVersion: number;
	templates: TemplateProfileSummary[];
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
			parts.push(`–${citation.line_end + 1}`);
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
	const [editBullets, setEditBullets] = useState(
		(slide.bullets ?? []).join("\n"),
	);
	const [editQuote, setEditQuote] = useState(slide.quote ?? "");
	const [editAttribution, setEditAttribution] = useState(
		slide.attribution ?? "",
	);
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
			setSaveError(
				caught instanceof Error ? caught.message : "Could not update slide.",
			);
		}
	}

	async function saveEdits() {
		setSaveError(null);
		setSaving(true);
		try {
			const patchBody: Record<string, unknown> = {};
			if (editTitle !== (slide.title ?? "")) patchBody.title = editTitle;
			if (editNotes !== (slide.speaker_notes ?? ""))
				patchBody.speaker_notes = editNotes;
			if (slide.layout === "bullets" && slide.bullets) {
				const newBullets = editBullets
					.split("\n")
					.map((l) => l.trim())
					.filter(Boolean);
				if (JSON.stringify(newBullets) !== JSON.stringify(slide.bullets)) {
					patchBody.bullets = newBullets;
				}
			}
			if (slide.layout === "quote") {
				if (editQuote !== (slide.quote ?? "")) patchBody.quote = editQuote;
				if (editAttribution !== (slide.attribution ?? ""))
					patchBody.attribution = editAttribution;
			}
			if (slide.layout === "big_statement") {
				if (editStatement !== (slide.statement ?? ""))
					patchBody.statement = editStatement;
			}
			if (slide.layout === "code") {
				if (editCode !== (slide.code ?? "")) patchBody.code = editCode;
			}
			if (slide.layout === "two_column") {
				if (editLeft !== (slide.left_content ?? ""))
					patchBody.left_content = editLeft;
				if (editRight !== (slide.right_content ?? ""))
					patchBody.right_content = editRight;
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
			setSaveError(
				caught instanceof Error ? caught.message : "Could not save changes.",
			);
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
					<div className="field slide-title-field">
						<label htmlFor={`slide-${slide.id}-title`}>Slide title</label>
						<input
							id={`slide-${slide.id}-title`}
							className="slide-title-edit"
							value={editTitle}
							onChange={(e) => setEditTitle(e.target.value)}
						/>
					</div>
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
			{saveError ? (
				<p className="notice error-notice" role="alert">
					{saveError}
				</p>
			) : null}
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
							<label htmlFor={`slide-${slide.id}-bullets`}>
								Bullets (one per line)
							</label>
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
								<label htmlFor={`slide-${slide.id}-attribution`}>
									Attribution
								</label>
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
					{slide.layout === "bullets" && slide.bullets ? (
						<ul className="slide-bullets">
							{slide.bullets.map((bullet, position) => (
								<li key={`${slide.id}-${String(position)}-${bullet}`}>
									<button
										type="button"
										className="clickable-content"
										onClick={() =>
											onChatContext(
												`I'm looking at bullet ${String(position + 1)} on "${slidePreview(slide)}": "${bullet}"`,
											)
										}
									>
										{bullet}
									</button>
								</li>
							))}
						</ul>
					) : slide.layout === "code" && slide.code ? (
						<div className="content-block">
							<pre className="slide-code-block">
								<code>{slide.code}</code>
							</pre>
							<button
								type="button"
								className="content-context-action"
								onClick={() =>
									onChatContext(
										`I'm looking at the code on "${slidePreview(slide)}"`,
									)
								}
							>
								Chat about code
							</button>
						</div>
					) : slide.layout === "two_column" ? (
						<div className="slide-two-column">
							<button
								type="button"
								className="clickable-content"
								onClick={() =>
									onChatContext(
										`I'm looking at the left column on "${slidePreview(slide)}"`,
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
										`I'm looking at the right column on "${slidePreview(slide)}"`,
									)
								}
							>
								{slide.right_content}
							</button>
						</div>
					) : slide.quote ? (
						<div className="content-block">
							<blockquote className="slide-quote">
								<p>{slide.quote}</p>
								{slide.attribution ? (
									<footer>{slide.attribution}</footer>
								) : null}
							</blockquote>
							<button
								type="button"
								className="content-context-action"
								onClick={() =>
									onChatContext(
										`I'm looking at the quote on "${slidePreview(slide)}"`,
									)
								}
							>
								Chat about quote
							</button>
						</div>
					) : slide.statement ? (
						<button
							type="button"
							className="content-block-button"
							onClick={() =>
								onChatContext(
									`I'm looking at the statement on "${slidePreview(slide)}"`,
								)
							}
						>
							<span className="slide-statement">{slide.statement}</span>
						</button>
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
						{slide.citations.map((citation, position) => (
							<li key={`${slide.id}-${String(position)}-${citation.source_id}`}>
								<div className="citation-row">
									<button
										type="button"
										className="citation-context-button"
										onClick={() =>
											onChatContext(
												`This slide cites ${citation.source_id} ("${citation.label}")`,
											)
										}
									>
										<span className="citation-label">{citation.label}</span>
										<small className="citation-coordinates">
											{citationDetail(citation)}
										</small>
									</button>
									{citation.url ? (
										<a
											href={citation.url}
											target="_blank"
											rel="noopener noreferrer"
											className="citation-url"
										>
											Open source
										</a>
									) : null}
								</div>
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
	templates,
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
	const [loadingPresentation, setLoadingPresentation] = useState(false);
	const [showArchived, setShowArchived] = useState(false);
	const [selectedProfileId, setSelectedProfileId] = useState<string>(
		course.template_profile_id ?? "_builtin-default",
	);
	const prevVersion = useRef(presentationVersion);
	const presentationRequest = useRef(0);

	const loadPresentation = useCallback(async (lectureId: string) => {
		const requestId = ++presentationRequest.current;
		setLoadingPresentation(true);
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lectureId)}`,
			);
			if (response.status === 404) {
				if (requestId === presentationRequest.current) {
					setCurrentPresentation(null);
				}
				return;
			}
			if (!response.ok) {
				throw new Error(await responseError(response));
			}
			const presentation = (await response.json()) as Presentation;
			if (requestId === presentationRequest.current) {
				setCurrentPresentation(presentation);
			}
		} catch (caught) {
			if (requestId === presentationRequest.current)
				setError(
					caught instanceof Error
						? caught.message
						: "Could not load presentation.",
				);
		} finally {
			if (requestId === presentationRequest.current) {
				setLoadingPresentation(false);
			}
		}
	}, []);

	const selectLecture = useCallback(
		async (lectureId: string) => {
			const lecture = course.lectures.find((item) => item.id === lectureId);
			setSelectedLectureId(lectureId);
			setSelectedSlideId(null);
			setCurrentPresentation(null);
			setError(null);
			setShowArchived(false);
			onChatContext(
				`I'm working on the Lecture "${lecture?.title ?? "Untitled Lecture"}"`,
			);
			await loadPresentation(lectureId);
		},
		[course.lectures, loadPresentation, onChatContext],
	);

	useEffect(() => {
		const selectionStillExists = course.lectures.some(
			(lecture) => lecture.id === selectedLectureId,
		);
		if (!selectionStillExists && course.lectures[0]) {
			void selectLecture(course.lectures[0].id);
		}
	}, [course.lectures, selectLecture, selectedLectureId]);

	useEffect(() => {
		if (presentationVersion !== prevVersion.current) {
			prevVersion.current = presentationVersion;
			if (selectedLectureId) {
				void loadPresentation(selectedLectureId);
			}
		}
	}, [presentationVersion, selectedLectureId, loadPresentation]);

	useEffect(() => {
		setSelectedProfileId(course.template_profile_id ?? "_builtin-default");
	}, [course.template_profile_id]);

	async function deletePresentation() {
		if (!selectedLectureId) return;
		if (!window.confirm("Delete this Presentation and all of its Slides?"))
			return;
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

	async function selectProfile(profileId: string) {
		const previous = selectedProfileId;
		const selected = templates.find((template) => template.id === profileId);
		setSelectedProfileId(profileId);
		setError(null);
		try {
			const response = await fetch("/api/course/profile", {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					template_profile_id:
						profileId === "_builtin-default" ? null : profileId,
					template_profile_version:
						profileId === "_builtin-default" ? null : selected?.version,
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
		} catch (caught) {
			setSelectedProfileId(previous);
			setError(
				caught instanceof Error
					? caught.message
					: "Could not select the Template Profile.",
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
		<section className="presentation-main" aria-label="Presentation authoring">
			<div className="presentation-layout">
				<section
					className="lecture-selector-section"
					aria-labelledby="lecture-selector-heading"
				>
					<div className="content-section-heading">
						<div>
							<p className="section-kicker">Course Plan</p>
							<h2 id="lecture-selector-heading">Lectures</h2>
						</div>
					</div>
					<ol className="lecture-selector-list">
						{course.lectures.map((lecture, index) => {
							const presentation = presentations.find(
								(item) => item.lecture_id === lecture.id,
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
										<span className="lecture-status">
											{presentation
												? `${presentation.slide_count} slides`
												: "No slides"}
										</span>
									</button>
								</li>
							);
						})}
					</ol>
				</section>

				<section className="slide-canvas-section" aria-label="Slide canvas">
					{error ? (
						<p className="notice error-notice" role="alert">
							{error}
						</p>
					) : null}

					{loadingPresentation ? (
						<div className="empty-state" role="status">
							<p>Loading Presentation…</p>
						</div>
					) : !selectedLectureId ? (
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
									<h2 id="slide-canvas-heading">Presentation</h2>
								</div>
								<div className="section-actions">
									<div className="export-row">
										<select
											value={selectedProfileId}
											onChange={(e) => void selectProfile(e.target.value)}
											aria-label="Template profile"
											className="profile-select"
										>
											<option value="_builtin-default">Default template</option>
											{selectedProfileId !== "_builtin-default" &&
											!templates.some(
												(template) => template.id === selectedProfileId,
											) ? (
												<option value={selectedProfileId}>
													Unavailable template
												</option>
											) : null}
											{templates
												.filter((t) => t.id !== "_builtin-default")
												.map((t) => (
													<option key={t.id} value={t.id}>
														{t.name}
													</option>
												))}
										</select>
										<button
											className="secondary-action compact-action"
											type="button"
											disabled={busy}
											onClick={() => {
												void (async () => {
													if (!selectedLectureId) return;
													setError(null);
													try {
														const exportUrl = `/api/presentations/${encodeURIComponent(selectedLectureId)}/export`;
														const r = await fetch(exportUrl);
														if (!r.ok) throw new Error(await responseError(r));
														const blob = await r.blob();
														const url = URL.createObjectURL(blob);
														const a = document.createElement("a");
														a.href = url;
														a.download = `presentation.pptx`;
														document.body.appendChild(a);
														a.click();
														document.body.removeChild(a);
														URL.revokeObjectURL(url);
													} catch (caught) {
														setError(
															caught instanceof Error
																? caught.message
																: "Could not export presentation.",
														);
													}
												})();
											}}
										>
											Export PowerPoint
										</button>
									</div>
									<details className="presentation-more">
										<summary>More</summary>
										<button
											className="quiet-action destructive-action compact-action"
											type="button"
											disabled={busy}
											onClick={() => void deletePresentation()}
										>
											Delete Presentation
										</button>
									</details>
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
														onClick={() => {
															setSelectedSlideId(slide.id);
															onChatContext(
																`I'm reviewing Slide ${idx + 1}, "${slidePreview(slide)}", in "${course.lectures.find((lecture) => lecture.id === selectedLectureId)?.title ?? "this Lecture"}"`,
															);
														}}
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
		</section>
	);
}
