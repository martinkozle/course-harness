import {
	Archive,
	ArchiveRestore,
	ArrowLeft,
	ArrowRight,
	Download,
	LayoutTemplate,
	Pencil,
	Sparkles,
	Trash2,
} from "lucide-react";
import {
	type CSSProperties,
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
} from "react";

import { responseError } from "../api";
import { TemplateGalleryDialog } from "../dialogs/TemplateGalleryDialog";
import type {
	CoursePlan,
	Lecture,
	Presentation,
	PresentationPreview,
	PreviewSlot,
	ReaderTarget,
	Slide,
	SlideCitation,
	SlidePreviewDescriptor,
	Source,
	TemplateProfileSummary,
} from "../models";
import {
	ConfirmDialog,
	Notice,
	downloadResponse,
	errorMessage,
	isAbort,
} from "../ui";
import type { AgentContext } from "../useCourseAgent";
import { lineRangeLabel } from "./SourceReader";

type Freshness = "checking" | "rendering" | "ready";

/** Remembers the selected Slide per Lecture so returning from evidence restores it. */
const lastSelectedSlide = new Map<string, string>();

const layoutLabels: Record<string, string> = {
	title: "Title",
	section: "Section",
	bullets: "Bullets",
	two_column: "Two columns",
	big_statement: "Statement",
	closing: "Closing",
	code: "Code",
	image: "Image",
	quote: "Quote",
};

type EditableField =
	| "title"
	| "subtitle"
	| "bullets"
	| "left_content"
	| "right_content"
	| "statement"
	| "text"
	| "code"
	| "language"
	| "image_url"
	| "caption"
	| "quote"
	| "attribution"
	| "speaker_notes";

const fieldsByLayout: Record<string, EditableField[]> = {
	title: ["title", "subtitle"],
	section: ["title", "subtitle"],
	closing: ["title", "subtitle"],
	bullets: ["title", "bullets"],
	two_column: ["title", "left_content", "right_content"],
	big_statement: ["title", "statement"],
	code: ["title", "code", "language"],
	image: ["title", "image_url", "caption"],
	quote: ["quote", "attribution"],
};

const fieldLabels: Record<EditableField, string> = {
	title: "Slide title",
	subtitle: "Subtitle",
	bullets: "Bullets",
	left_content: "Left column",
	right_content: "Right column",
	statement: "Statement",
	text: "Text",
	code: "Code",
	language: "Code language",
	image_url: "Image address",
	caption: "Caption",
	quote: "Quote",
	attribution: "Attribution",
	speaker_notes: "Speaker notes",
};

const multiline = new Set<EditableField>([
	"title",
	"subtitle",
	"bullets",
	"left_content",
	"right_content",
	"statement",
	"text",
	"code",
	"caption",
	"quote",
	"speaker_notes",
]);

function fieldValue(slide: Slide, field: EditableField): string {
	if (field === "bullets") return (slide.bullets ?? []).join("\n");
	return (slide[field] as string | undefined) ?? "";
}

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
						? slide.left_content
						: slot === "right"
							? slide.right_content
							: slot === "statement"
								? slide.statement
								: slot === "image"
									? (slide.caption ?? "Image")
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

function SlidePreview({
	slide,
	preview,
	freshness,
	aspectRatio,
}: {
	slide: Slide;
	preview: SlidePreviewDescriptor | null;
	freshness: Freshness;
	aspectRatio: number;
}) {
	const rendered = Boolean(preview?.thumbnail_url);
	const slots = Object.entries(preview?.slots ?? {});
	return (
		<div
			className={`slide-preview ${rendered ? "is-rendered" : "is-approximate"}`}
			style={{
				aspectRatio,
				...(preview?.background_url
					? { backgroundImage: `url("${preview.background_url}")` }
					: {}),
			}}
		>
			{rendered && preview?.thumbnail_url ? (
				<img src={preview.thumbnail_url} alt="" />
			) : (
				slots.map(([name, slot]) => {
					const content = slotContent(slide, name);
					return content ? (
						<span key={name} className="slide-slot" style={slotStyle(slot)}>
							{content}
						</span>
					) : null;
				})
			)}
			{!rendered && slots.length === 0 ? (
				<span className="slide-fallback">{slideSummary(slide)}</span>
			) : null}
			{freshness !== "ready" ? (
				<span className="slide-updating" aria-hidden="true">
					Updating…
				</span>
			) : null}
		</div>
	);
}

function slug(value: string): string {
	return (
		value
			.toLowerCase()
			.normalize("NFKD")
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-+|-+$/g, "")
			.slice(0, 60) || "presentation"
	);
}

export function LectureCanvas({
	course,
	lecture,
	lectureNumber,
	presentationVersion,
	templates,
	sources,
	busy,
	onPresentationsChange,
	onCourseChange,
	onFocusChange,
	onAskAgent,
	onOpenEvidence,
	onManageTemplates,
	onTemplatesChange,
}: {
	course: CoursePlan;
	lecture: Lecture;
	lectureNumber: number;
	presentationVersion: number;
	templates: TemplateProfileSummary[];
	sources: Source[];
	busy: boolean;
	onPresentationsChange: () => Promise<void>;
	onCourseChange: () => Promise<void>;
	onFocusChange: (context: AgentContext | null) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
	onOpenEvidence: (target: ReaderTarget) => void;
	onManageTemplates: () => void;
	onTemplatesChange: (templates: TemplateProfileSummary[]) => void;
}) {
	const [presentation, setPresentation] = useState<Presentation | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [editing, setEditing] = useState(false);
	const [showArchived, setShowArchived] = useState(false);
	const [preview, setPreview] = useState<PresentationPreview | null>(null);
	const [previewNote, setPreviewNote] = useState<string | null>(null);
	const [checking, setChecking] = useState(false);
	const [rendering, setRendering] = useState(false);
	const [galleryOpen, setGalleryOpen] = useState(false);
	const [confirmDelete, setConfirmDelete] = useState(false);
	const [deleting, setDeleting] = useState(false);
	const [exporting, setExporting] = useState(false);
	const requestRef = useRef(0);
	const versionRef = useRef(presentationVersion);

	const load = useCallback(async () => {
		const request = ++requestRef.current;
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lecture.id)}`,
			);
			if (request !== requestRef.current) return;
			if (response.status === 404) {
				setPresentation(null);
				return;
			}
			if (!response.ok) throw new Error(await responseError(response));
			const next = (await response.json()) as Presentation;
			if (request !== requestRef.current) return;
			setPresentation(next);
			setSelectedId((current) => {
				const remembered = current ?? lastSelectedSlide.get(lecture.id);
				return remembered &&
					next.slides.some((slide) => slide.id === remembered)
					? remembered
					: (next.slides.find((slide) => !slide.archived)?.id ?? null);
			});
		} catch (caught) {
			if (request === requestRef.current)
				setError(errorMessage(caught, "The Slides could not be loaded."));
		} finally {
			if (request === requestRef.current) setLoading(false);
		}
	}, [lecture.id]);

	useEffect(() => {
		void load();
	}, [load]);

	useEffect(() => {
		if (selectedId) lastSelectedSlide.set(lecture.id, selectedId);
	}, [lecture.id, selectedId]);

	useEffect(() => {
		if (presentationVersion === versionRef.current) return;
		versionRef.current = presentationVersion;
		if (!editing) void load();
	}, [presentationVersion, editing, load]);

	// Preview: semantic layout first, then high-fidelity thumbnails when a renderer exists.
	useEffect(() => {
		if (!presentation) {
			setPreview(null);
			return;
		}
		const controller = new AbortController();
		const expectedProfile = course.template_profile_id ?? "_builtin-default";
		const expectedVersion = course.template_profile_version;
		const url = `/api/presentations/${encodeURIComponent(lecture.id)}/preview`;
		let timer: ReturnType<typeof setTimeout> | undefined;
		setChecking(true);
		setRendering(false);
		void (async () => {
			try {
				const response = await fetch(url, { signal: controller.signal });
				if (!response.ok) throw new Error(await responseError(response));
				const semantic = (await response.json()) as PresentationPreview;
				if (
					semantic.profile_id !== expectedProfile ||
					(expectedVersion != null &&
						semantic.profile_version !== expectedVersion)
				)
					return;
				setPreview(semantic);
				setChecking(false);
				if (!semantic.renderer.available) {
					setPreviewNote(`Approximate preview · ${semantic.renderer.detail}`);
					return;
				}
				const missing = semantic.slides.filter(
					(slide) => !slide.thumbnail_url,
				).length;
				if (missing === 0) {
					setPreviewNote(null);
					return;
				}
				setRendering(true);
				setPreviewNote(
					`Rendering ${missing} preview${missing === 1 ? "" : "s"} with ${semantic.renderer.name}…`,
				);
				timer = setTimeout(() => {
					void fetch(`${url}/render`, {
						method: "POST",
						signal: controller.signal,
					})
						.then(async (renderResponse) => {
							if (!renderResponse.ok)
								throw new Error(await responseError(renderResponse));
							setPreview((await renderResponse.json()) as PresentationPreview);
							setPreviewNote(null);
						})
						.catch((caught: unknown) => {
							if (isAbort(caught)) return;
							setPreviewNote(
								`Rendered preview failed; showing an approximation. ${errorMessage(caught, "")}`.trim(),
							);
						})
						.finally(() => {
							if (!controller.signal.aborted) setRendering(false);
						});
				}, 600);
			} catch (caught) {
				if (isAbort(caught)) return;
				setChecking(false);
				setPreviewNote(
					`Preview unavailable: ${errorMessage(caught, "unknown error")}`,
				);
			}
		})();
		return () => {
			controller.abort();
			if (timer) clearTimeout(timer);
		};
	}, [
		course.template_profile_id,
		course.template_profile_version,
		lecture.id,
		presentation,
	]);

	const activeSlides =
		presentation?.slides.filter((slide) => !slide.archived) ?? [];
	const archivedSlides =
		presentation?.slides.filter((slide) => slide.archived) ?? [];
	const selected =
		presentation?.slides.find((slide) => slide.id === selectedId) ?? null;
	const selectedIndex = selected
		? activeSlides.findIndex((slide) => slide.id === selected.id)
		: -1;
	const previewBySlide = new Map(
		(preview?.slides ?? []).map((item) => [item.slide_id, item]),
	);
	const aspectRatio = preview
		? preview.slide_width / preview.slide_height
		: 16 / 9;
	const freshnessFor = (slide: Slide): Freshness =>
		checking
			? "checking"
			: rendering && !previewBySlide.get(slide.id)?.thumbnail_url
				? "rendering"
				: "ready";

	const lectureContext: AgentContext = {
		key: `lecture-${lecture.id}`,
		label: `Lecture ${lectureNumber} · ${lecture.title}`,
		instruction: `I'm working on the Lecture "${lecture.title}"`,
	};
	const slideContext: AgentContext | null = selected
		? {
				key: `slide-${selected.id}`,
				label: selected.archived
					? `Archived Slide · ${slideSummary(selected)}`
					: `Slide ${selectedIndex + 1} · ${slideSummary(selected)}`,
				instruction: `I'm reviewing ${selected.archived ? "an archived Slide" : `Slide ${selectedIndex + 1}`}, "${slideSummary(selected)}", in "${lecture.title}"`,
			}
		: null;
	const focusKey = slideContext?.key ?? lectureContext.key;
	const focusRef = useRef(slideContext ?? lectureContext);
	focusRef.current = slideContext ?? lectureContext;

	// biome-ignore lint/correctness/useExhaustiveDependencies: focusKey identifies the focus
	useEffect(() => {
		onFocusChange(focusRef.current);
	}, [focusKey, onFocusChange]);

	useEffect(() => () => onFocusChange(null), [onFocusChange]);

	function select(slideId: string) {
		if (editing) return;
		setSelectedId(slideId);
	}

	async function patchSlide(slideId: string, body: Record<string, unknown>) {
		const response = await fetch(
			`/api/presentations/${encodeURIComponent(lecture.id)}/slides/${encodeURIComponent(slideId)}`,
			{
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			},
		);
		if (!response.ok) throw new Error(await responseError(response));
		const next = (await response.json()) as Presentation;
		setPresentation(next);
		await onPresentationsChange();
		return next;
	}

	async function toggleArchive(slide: Slide) {
		setError(null);
		try {
			const next = await patchSlide(slide.id, { archived: !slide.archived });
			if (!slide.archived) {
				const remaining = next.slides.filter((item) => !item.archived);
				setSelectedId(
					remaining[Math.min(selectedIndex, remaining.length - 1)]?.id ?? null,
				);
			}
		} catch (caught) {
			setError(errorMessage(caught, "The Slide could not be updated."));
		}
	}

	async function move(direction: -1 | 1) {
		if (!selected || selectedIndex < 0) return;
		const target = selectedIndex + direction;
		if (target < 0 || target >= activeSlides.length) return;
		const ids = activeSlides.map((slide) => slide.id);
		[ids[selectedIndex], ids[target]] = [ids[target], ids[selectedIndex]];
		setError(null);
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lecture.id)}/slides/order`,
				{
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ slide_ids: ids }),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			setPresentation((await response.json()) as Presentation);
			await onPresentationsChange();
		} catch (caught) {
			setError(errorMessage(caught, "The Slides could not be reordered."));
		}
	}

	async function exportPowerPoint() {
		setExporting(true);
		setError(null);
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lecture.id)}/export`,
			);
			if (!response.ok) throw new Error(await responseError(response));
			await downloadResponse(response, `${slug(lecture.title)}.pptx`);
		} catch (caught) {
			setError(errorMessage(caught, "The PowerPoint could not be exported."));
		} finally {
			setExporting(false);
		}
	}

	async function deletePresentation() {
		setDeleting(true);
		setError(null);
		try {
			const response = await fetch(
				`/api/presentations/${encodeURIComponent(lecture.id)}`,
				{ method: "DELETE" },
			);
			if (!response.ok && response.status !== 404)
				throw new Error(await responseError(response));
			setPresentation(null);
			setSelectedId(null);
			setConfirmDelete(false);
			await onPresentationsChange();
		} catch (caught) {
			setError(errorMessage(caught, "The Presentation could not be deleted."));
		} finally {
			setDeleting(false);
		}
	}

	const templateId = course.template_profile_id ?? "_builtin-default";
	const template = templates.find((item) => item.id === templateId);
	const templateName =
		templateId === "_builtin-default"
			? "Default template"
			: (template?.name ?? "Unavailable template");
	const templateVersion =
		templateId === "_builtin-default" ? null : course.template_profile_version;

	return (
		<div className="canvas-page is-full lecture-page">
			<header className="canvas-head lecture-head">
				<div className="canvas-head-copy">
					<p className="lecture-kicker">Lecture {lectureNumber}</p>
					<h1 className="display-title">{lecture.title}</h1>
					{presentation ? (
						<p className="meta">
							{activeSlides.length} Slide{activeSlides.length === 1 ? "" : "s"}
							{archivedSlides.length > 0
								? ` · ${archivedSlides.length} archived`
								: ""}
						</p>
					) : null}
				</div>
				{presentation ? (
					<div className="canvas-head-actions">
						<button
							type="button"
							className="template-button"
							onClick={() => setGalleryOpen(true)}
							title={`${templateName}${templateVersion ? ` · version ${templateVersion}` : ""}`}
							aria-label={`Template: ${templateName}${templateVersion ? `, version ${templateVersion}` : ""}. Change template`}
						>
							<LayoutTemplate aria-hidden="true" />
							<span className="template-button-name">{templateName}</span>
							{templateVersion ? (
								<span className="template-button-version">
									v{templateVersion}
								</span>
							) : null}
						</button>
						<button
							type="button"
							className="btn btn-primary"
							disabled={busy || exporting}
							onClick={() => void exportPowerPoint()}
						>
							<Download aria-hidden="true" />
							{exporting ? "Exporting…" : "Export PowerPoint"}
						</button>
						<button
							type="button"
							className="icon-btn is-danger lecture-delete"
							aria-label="Delete Presentation"
							title="Delete Presentation"
							disabled={busy}
							onClick={() => setConfirmDelete(true)}
						>
							<Trash2 aria-hidden="true" />
						</button>
					</div>
				) : null}
			</header>

			{error ? <Notice tone="error">{error}</Notice> : null}

			{loading ? (
				<p className="meta" role="status">
					Loading Slides…
				</p>
			) : !presentation ? (
				<div className="canvas-empty lecture-empty">
					<h2>No Slides yet</h2>
					<p>
						This Lecture is planned but has no Presentation. The agent can draft
						one from your Sources; you can edit every Slide afterwards.
					</p>
					<button
						type="button"
						className="btn btn-primary"
						disabled={busy}
						onClick={() =>
							onAskAgent(
								`Create Slides for “${lecture.title}” grounded in the course Sources.`,
								lectureContext,
							)
						}
					>
						<Sparkles aria-hidden="true" />
						Draft Slides with the agent
					</button>
				</div>
			) : (
				<div className="lecture-body">
					<section className="stage" aria-label="Selected Slide">
						{selected ? (
							<>
								<SlidePreview
									slide={selected}
									preview={previewBySlide.get(selected.id) ?? null}
									freshness={freshnessFor(selected)}
									aspectRatio={aspectRatio}
								/>
								<div className="stage-bar">
									<span className="stage-position">
										{selected.archived
											? "Archived Slide"
											: `Slide ${selectedIndex + 1} of ${activeSlides.length}`}
										<span className="stage-layout">
											{layoutLabels[selected.layout] ?? selected.layout}
										</span>
									</span>
									{!editing ? (
										<div className="stage-actions">
											{!selected.archived ? (
												<>
													<button
														type="button"
														className="icon-btn"
														aria-label="Move Slide earlier"
														title="Move Slide earlier"
														disabled={busy || selectedIndex <= 0}
														onClick={() => void move(-1)}
													>
														<ArrowLeft aria-hidden="true" />
													</button>
													<button
														type="button"
														className="icon-btn"
														aria-label="Move Slide later"
														title="Move Slide later"
														disabled={
															busy || selectedIndex >= activeSlides.length - 1
														}
														onClick={() => void move(1)}
													>
														<ArrowRight aria-hidden="true" />
													</button>
												</>
											) : null}
											<button
												type="button"
												className="icon-btn"
												aria-label={
													selected.archived ? "Restore Slide" : "Archive Slide"
												}
												title={
													selected.archived ? "Restore Slide" : "Archive Slide"
												}
												disabled={busy}
												onClick={() => void toggleArchive(selected)}
											>
												{selected.archived ? (
													<ArchiveRestore aria-hidden="true" />
												) : (
													<Archive aria-hidden="true" />
												)}
											</button>
											<button
												type="button"
												className="btn btn-quiet"
												onClick={() =>
													onAskAgent("Improve this Slide: ", slideContext)
												}
											>
												<Sparkles aria-hidden="true" />
												Ask the agent
											</button>
											<button
												type="button"
												className="btn"
												disabled={busy}
												onClick={() => setEditing(true)}
											>
												<Pencil aria-hidden="true" />
												Edit Slide
											</button>
										</div>
									) : null}
								</div>
								{previewNote ? (
									<p className="stage-note" role="status">
										{previewNote}
									</p>
								) : null}
								{editing ? (
									<SlideEditor
										key={selected.id}
										slide={selected}
										busy={busy}
										onCancel={() => setEditing(false)}
										onSave={async (body) => {
											await patchSlide(selected.id, body);
											setEditing(false);
										}}
									/>
								) : (
									<SlideDetails
										slide={selected}
										sources={sources}
										onOpenEvidence={onOpenEvidence}
									/>
								)}
							</>
						) : (
							<p className="meta">Select a Slide to see it here.</p>
						)}
					</section>

					<nav className="filmstrip" aria-label="Slides">
						<ol>
							{activeSlides.map((slide, index) => (
								<li key={slide.id}>
									<button
										type="button"
										className="film-slide"
										aria-current={slide.id === selectedId ? "true" : undefined}
										aria-label={`Slide ${index + 1}: ${slideSummary(slide)}`}
										disabled={editing && slide.id !== selectedId}
										onClick={() => select(slide.id)}
									>
										<span className="film-number" aria-hidden="true">
											{index + 1}
										</span>
										<SlidePreview
											slide={slide}
											preview={previewBySlide.get(slide.id) ?? null}
											freshness={freshnessFor(slide)}
											aspectRatio={aspectRatio}
										/>
										{slide.citations.length > 0 ? (
											<span
												className="film-cites"
												title={`${slide.citations.length} Citation${slide.citations.length === 1 ? "" : "s"}`}
												aria-hidden="true"
											>
												{slide.citations.length}
											</span>
										) : null}
									</button>
								</li>
							))}
						</ol>
						{archivedSlides.length > 0 ? (
							<div className="film-archived">
								<button
									type="button"
									className="btn btn-quiet btn-small"
									aria-expanded={showArchived}
									onClick={() => setShowArchived((value) => !value)}
								>
									Archived ({archivedSlides.length})
								</button>
								{showArchived ? (
									<ol>
										{archivedSlides.map((slide) => (
											<li key={slide.id}>
												<button
													type="button"
													className="film-slide is-archived"
													aria-current={
														slide.id === selectedId ? "true" : undefined
													}
													aria-label={`Archived Slide: ${slideSummary(slide)}`}
													disabled={editing}
													onClick={() => select(slide.id)}
												>
													<SlidePreview
														slide={slide}
														preview={previewBySlide.get(slide.id) ?? null}
														freshness="ready"
														aspectRatio={aspectRatio}
													/>
												</button>
											</li>
										))}
									</ol>
								) : null}
							</div>
						) : null}
					</nav>
				</div>
			)}

			{galleryOpen ? (
				<TemplateGalleryDialog
					course={course}
					templates={templates}
					onTemplatesChange={onTemplatesChange}
					onApplied={onCourseChange}
					onManage={() => {
						setGalleryOpen(false);
						onManageTemplates();
					}}
					onClose={() => setGalleryOpen(false)}
				/>
			) : null}
			{confirmDelete ? (
				<ConfirmDialog
					title="Delete this Presentation?"
					confirmLabel="Delete Presentation"
					busy={deleting}
					error={error}
					onCancel={() => setConfirmDelete(false)}
					onConfirm={() => void deletePresentation()}
				>
					<p>
						All {presentation?.slides.length ?? 0} Slides for Lecture{" "}
						{lectureNumber}, “{lecture.title}”, will be deleted. The Lecture
						stays in the Course Plan, and you can recover the Slides from
						History.
					</p>
				</ConfirmDialog>
			) : null}
		</div>
	);
}

function SlideDetails({
	slide,
	sources,
	onOpenEvidence,
}: {
	slide: Slide;
	sources: Source[];
	onOpenEvidence: (target: ReaderTarget) => void;
}) {
	function openCitation(citation: SlideCitation) {
		const source = sources.find((item) => item.id === citation.source_id);
		onOpenEvidence({
			sourceId: source ? citation.source_id : null,
			resourceId: null,
			label: source?.label ?? citation.label,
			lineStart: citation.line_start ?? null,
			lineEnd: citation.line_end ?? null,
		});
	}
	return (
		<div className="slide-details">
			{slide.purpose ? (
				<section>
					<h2 className="section-label">Purpose</h2>
					<p>{slide.purpose}</p>
				</section>
			) : null}
			<section>
				<h2 className="section-label">Speaker notes</h2>
				{slide.speaker_notes ? (
					<p className="slide-notes">{slide.speaker_notes}</p>
				) : (
					<p className="meta">No speaker notes.</p>
				)}
			</section>
			<section>
				<h2 className="section-label">
					Citations{" "}
					{slide.citations.length > 0 ? `(${slide.citations.length})` : ""}
				</h2>
				{slide.citations.length > 0 ? (
					<ul className="citation-list">
						{slide.citations.map((citation, index) => {
							const admitted = sources.some(
								(source) => source.id === citation.source_id,
							);
							return (
								// biome-ignore lint/suspicious/noArrayIndexKey: Citations have no identity of their own
								<li key={`${citation.source_id}-${index}`}>
									<button
										type="button"
										className="citation-chip"
										disabled={!admitted && !citation.url}
										title={
											admitted
												? "Open the cited passage"
												: "This Source is no longer in the course"
										}
										onClick={() => {
											if (admitted) openCitation(citation);
											else if (citation.url)
												window.open(
													citation.url,
													"_blank",
													"noopener,noreferrer",
												);
										}}
									>
										<span className="citation-marker">{index + 1}</span>
										<span className="citation-label">{citation.label}</span>
										{citation.line_start != null ? (
											<span className="citation-where">
												{lineRangeLabel(
													citation.line_start,
													citation.line_end ?? null,
												)}
											</span>
										) : null}
									</button>
								</li>
							);
						})}
					</ul>
				) : (
					<p className="meta">This Slide cites no Sources.</p>
				)}
			</section>
		</div>
	);
}

function AutoTextarea({
	id,
	value,
	onChange,
	minRows,
	className,
}: {
	id: string;
	value: string;
	onChange: (value: string) => void;
	minRows: number;
	className?: string;
}) {
	const ref = useRef<HTMLTextAreaElement>(null);
	// biome-ignore lint/correctness/useExhaustiveDependencies: value drives height
	useLayoutEffect(() => {
		const element = ref.current;
		if (!element) return;
		element.style.height = "auto";
		element.style.height = `${element.scrollHeight + 2}px`;
	}, [value]);
	return (
		<textarea
			ref={ref}
			id={id}
			rows={minRows}
			className={className}
			value={value}
			onChange={(event) => onChange(event.target.value)}
		/>
	);
}

function SlideEditor({
	slide,
	busy,
	onCancel,
	onSave,
}: {
	slide: Slide;
	busy: boolean;
	onCancel: () => void;
	onSave: (body: Record<string, unknown>) => Promise<void>;
}) {
	const fields: EditableField[] = [
		...(fieldsByLayout[slide.layout] ?? ["title"]),
		"speaker_notes",
	];
	const [values, setValues] = useState<Record<string, string>>(() =>
		Object.fromEntries(
			fields.map((field) => [field, fieldValue(slide, field)]),
		),
	);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const formRef = useRef<HTMLFormElement>(null);
	useEffect(() => {
		formRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
		formRef.current?.querySelector<HTMLElement>("textarea, input")?.focus({
			preventScroll: true,
		});
	}, []);
	const dirty = fields.some(
		(field) => values[field] !== fieldValue(slide, field),
	);

	async function save() {
		const body: Record<string, unknown> = {};
		for (const field of fields) {
			if (values[field] === fieldValue(slide, field)) continue;
			body[field] =
				field === "bullets"
					? values[field]
							.split("\n")
							.map((line) => line.trim())
							.filter(Boolean)
					: values[field];
		}
		if (Object.keys(body).length === 0) {
			onCancel();
			return;
		}
		setSaving(true);
		setError(null);
		try {
			await onSave(body);
		} catch (caught) {
			setError(errorMessage(caught, "The Slide could not be saved."));
		} finally {
			setSaving(false);
		}
	}

	return (
		<form
			ref={formRef}
			className="slide-editor"
			aria-label={`Edit ${slideSummary(slide)}`}
			onSubmit={(event) => {
				event.preventDefault();
				void save();
			}}
			onKeyDown={(event) => {
				if (event.key === "Escape" && !dirty) onCancel();
			}}
		>
			<div className="slide-editor-bar">
				<span className="section-label">Editing Slide</span>
				<div className="slide-editor-actions">
					<button
						type="button"
						className="btn"
						onClick={onCancel}
						disabled={saving}
					>
						Cancel
					</button>
					<button
						type="submit"
						className="btn btn-primary"
						disabled={saving || busy || !dirty}
					>
						{saving ? "Saving…" : "Save changes"}
					</button>
				</div>
			</div>
			{error ? <Notice tone="error">{error}</Notice> : null}
			{fields.map((field) => {
				const id = `slide-${slide.id}-${field}`;
				return (
					<div className="field" key={field}>
						<label htmlFor={id}>{fieldLabels[field]}</label>
						{multiline.has(field) ? (
							<AutoTextarea
								id={id}
								value={values[field]}
								minRows={field === "title" ? 1 : field === "code" ? 6 : 3}
								className={
									field === "title"
										? "slide-title-input"
										: field === "code"
											? "slide-code-input"
											: undefined
								}
								onChange={(value) =>
									setValues((current) => ({ ...current, [field]: value }))
								}
							/>
						) : (
							<input
								id={id}
								value={values[field]}
								onChange={(event) =>
									setValues((current) => ({
										...current,
										[field]: event.target.value,
									}))
								}
							/>
						)}
						{field === "bullets" ? <small>One bullet per line.</small> : null}
					</div>
				);
			})}
		</form>
	);
}
