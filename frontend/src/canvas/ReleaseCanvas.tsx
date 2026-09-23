import {
	AlertTriangle,
	CheckCircle2,
	FileSearch,
	Presentation,
	Sparkles,
	XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { responseError } from "../api";
import type {
	CoursePlan,
	CourseRelease,
	CurrentState,
	PresentationSummary,
	ReaderTarget,
	ReleaseValidation,
	ReleaseValidationFinding,
	ReleaseWaiver,
	Source,
} from "../models";
import { errorMessage, isAbort, Notice } from "../ui";
import type { AgentContext } from "../useCourseAgent";
import type { HistoryTab } from "./HistoryCanvas";
import { findingScope, ReleaseDetail } from "./ReleaseDetail";

type Props = {
	course: CoursePlan;
	presentations: PresentationSummary[];
	sources: Source[];
	currentState: CurrentState | null;
	onOpenHistory: (tab: HistoryTab) => void;
	onOpenEvidence: (target: ReaderTarget) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
	onPublished: () => void;
};

const RELEASE_SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

function slugify(name: string): string {
	return name
		.toLowerCase()
		.normalize("NFKD")
		.replace(/[̀-ͯ]/g, "")
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+/, "")
		.slice(0, 64)
		.replace(/-+$/, "");
}

function selectionKey(lectureIds: string[], artifactIds: string[]): string {
	return `${lectureIds.join("\u0001")}\u0000${artifactIds.join("\u0001")}`;
}

function plural(count: number, one: string, many = `${one}s`): string {
	return `${count} ${count === 1 ? one : many}`;
}

export function ReleaseCanvas({
	course,
	presentations,
	sources,
	currentState,
	onOpenHistory,
	onOpenEvidence,
	onAskAgent,
	onPublished,
}: Props) {
	const [lectureIds, setLectureIds] = useState<string[]>([]);
	const [artifactIds, setArtifactIds] = useState<string[]>([]);
	const [name, setName] = useState("");
	const [slug, setSlug] = useState("");
	const [slugEdited, setSlugEdited] = useState(false);
	const [validation, setValidation] = useState<ReleaseValidation | null>(null);
	const [validatedKey, setValidatedKey] = useState<string | null>(null);
	const [waiverText, setWaiverText] = useState<Record<string, string>>({});
	const [validating, setValidating] = useState(false);
	const [publishing, setPublishing] = useState(false);
	const [published, setPublished] = useState<CourseRelease | null>(null);
	const [error, setError] = useState<string | null>(null);
	const validationRequestRef = useRef<AbortController | null>(null);
	const validationGenerationRef = useRef(0);
	const publishedHeadingRef = useRef<HTMLHeadingElement>(null);

	useEffect(() => () => validationRequestRef.current?.abort(), []);
	useEffect(() => {
		if (published) publishedHeadingRef.current?.focus();
	}, [published]);

	const presentationsByLecture = useMemo(() => {
		const map = new Map<string, PresentationSummary[]>();
		for (const presentation of presentations) {
			const entries = map.get(presentation.lecture_id) ?? [];
			entries.push(presentation);
			map.set(presentation.lecture_id, entries);
		}
		return map;
	}, [presentations]);

	const selectedKey = selectionKey(lectureIds, artifactIds);
	const isSlugValid = RELEASE_SLUG_PATTERN.test(slug);
	const stateBlocked =
		currentState !== null &&
		(!currentState.clean ||
			!currentState.validation.valid ||
			currentState.drift !== "clean");
	const canPublish =
		validation?.can_publish === true &&
		validatedKey === selectedKey &&
		Boolean(name.trim()) &&
		isSlugValid;
	const publicationBlocker = !name.trim()
		? "Give this release a name."
		: !slug.trim()
			? "Give this release a stable lowercase ID."
			: !isSlugValid
				? "Use a lowercase ID with letters, numbers, and internal hyphens only."
				: lectureIds.length === 0
					? "Choose at least one Lecture."
					: validation === null || validatedKey !== selectedKey
						? "Check the current selection before publishing."
						: !validation.can_publish
							? "Resolve the blocking issues and record a reason for every warning."
							: "";

	function invalidateValidation(clearResult = true) {
		validationGenerationRef.current += 1;
		validationRequestRef.current?.abort();
		validationRequestRef.current = null;
		setValidating(false);
		if (clearResult) setValidation(null);
		setValidatedKey(null);
	}

	function changeSelection(nextLectures: string[], nextArtifacts: string[]) {
		setLectureIds(nextLectures);
		setArtifactIds(nextArtifacts);
		invalidateValidation();
		setWaiverText({});
	}

	function toggleLecture(lectureId: string) {
		const owned = (presentationsByLecture.get(lectureId) ?? []).map(
			(presentation) => presentation.id,
		);
		if (lectureIds.includes(lectureId)) {
			changeSelection(
				lectureIds.filter((id) => id !== lectureId),
				artifactIds.filter((id) => !owned.includes(id)),
			);
		} else {
			changeSelection(
				[...lectureIds, lectureId],
				[...artifactIds, ...owned.filter((id) => !artifactIds.includes(id))],
			);
		}
	}

	function toggleArtifact(artifactId: string) {
		changeSelection(
			lectureIds,
			artifactIds.includes(artifactId)
				? artifactIds.filter((id) => id !== artifactId)
				: [...artifactIds, artifactId],
		);
	}

	function waivers(): ReleaseWaiver[] {
		return (validation?.findings ?? [])
			.filter((finding) => finding.severity === "warning")
			.flatMap((finding) => {
				const justification = waiverText[finding.id]?.trim();
				return justification ? [{ finding_id: finding.id, justification }] : [];
			});
	}

	async function validate() {
		if (lectureIds.length === 0) return;
		validationRequestRef.current?.abort();
		const controller = new AbortController();
		validationRequestRef.current = controller;
		const generation = ++validationGenerationRef.current;
		const requestKey = selectedKey;
		setValidating(true);
		setError(null);
		try {
			const response = await fetch("/api/releases/validate", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				signal: controller.signal,
				body: JSON.stringify({
					selection: {
						lecture_ids: [...lectureIds],
						artifact_ids: [...artifactIds],
					},
					waivers: waivers(),
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const result = (await response.json()) as ReleaseValidation;
			if (
				controller.signal.aborted ||
				generation !== validationGenerationRef.current
			)
				return;
			setValidation(result);
			setValidatedKey(requestKey);
		} catch (caught) {
			if (!isAbort(caught))
				setError(errorMessage(caught, "The release could not be checked."));
		} finally {
			if (
				!controller.signal.aborted &&
				generation === validationGenerationRef.current
			)
				setValidating(false);
		}
	}

	async function publish() {
		if (!canPublish) return;
		setPublishing(true);
		setError(null);
		try {
			const response = await fetch("/api/releases", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					name: name.trim(),
					slug: slug.trim(),
					selection: { lecture_ids: lectureIds, artifact_ids: artifactIds },
					waivers: validation?.waivers ?? [],
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const release = (await response.json()) as CourseRelease;
			setPublished(release);
			setName("");
			setSlug("");
			setSlugEdited(false);
			setValidation(null);
			setValidatedKey(null);
			setWaiverText({});
			setLectureIds([]);
			setArtifactIds([]);
			onPublished();
		} catch (caught) {
			setError(errorMessage(caught, "The release could not be published."));
		} finally {
			setPublishing(false);
		}
	}

	function evidenceTarget(
		finding: ReleaseValidationFinding,
	): ReaderTarget | null {
		const target = finding.target;
		if (target.scope !== "citation" || !target.source_id) return null;
		const source = sources.find((item) => item.id === target.source_id);
		if (!source) return null;
		return {
			sourceId: source.id,
			resourceId: source.resource_id,
			label: source.label,
			lineStart: target.line_start,
			lineEnd: target.line_end,
		};
	}

	function askAgent(finding: ReleaseValidationFinding) {
		const scope = findingScope(finding, course);
		onAskAgent(
			`Please fix this release issue in ${scope}: ${finding.message}`,
			{
				key: `finding-${finding.id}`,
				label: "Release finding",
				instruction: `Release validation needs editorial review. Finding: ${finding.message} Scope: ${scope}. Review the supporting Evidence and propose a correction; do not waive a finding unless the Course Author explicitly decides to publish with that warning.`,
			},
		);
	}

	const errors =
		validation?.findings.filter((f) => f.severity === "error") ?? [];
	const warnings =
		validation?.findings.filter((f) => f.severity === "warning") ?? [];
	const validationCurrent = validation !== null && validatedKey === selectedKey;

	return (
		<div className="canvas-page release-page">
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title">Publish a Course Release</h1>
					<p className="meta">
						Exporting PowerPoint gives you a file from your current work. A
						release is a named, checked, permanent record with its Sources and
						template pinned.
					</p>
				</div>
			</header>

			{stateBlocked ? (
				<Notice
					tone="warning"
					actions={
						<button
							type="button"
							className="btn btn-small"
							onClick={() => onOpenHistory("changes")}
						>
							Open History
						</button>
					}
				>
					{currentState?.drift !== "clean"
						? "Course files changed outside the app. Review them before publishing."
						: !currentState?.validation.valid
							? "The course has issues to review before it can be published."
							: "Save your recent changes as a Course Revision before publishing."}
				</Notice>
			) : null}
			{error ? <Notice tone="error">{error}</Notice> : null}

			{published ? (
				<section
					className="canvas-section release-published"
					aria-labelledby="release-published-heading"
				>
					<Notice tone="success" role="status">
						<h2
							id="release-published-heading"
							ref={publishedHeadingRef}
							tabIndex={-1}
						>
							Published {published.name}
						</h2>
					</Notice>
					<ReleaseDetail release={published} course={course} />
				</section>
			) : null}

			<section
				className="canvas-section release-step"
				aria-labelledby="release-choose-heading"
			>
				<div className="canvas-section-head">
					<span className="release-step-number" aria-hidden="true">
						1
					</span>
					<h2 id="release-choose-heading">Choose Lectures and files</h2>
					<span className="meta">
						{lectureIds.length} of {course.lectures.length}
					</span>
				</div>
				<ol className="row-list release-lectures">
					{course.lectures.map((lecture, index) => {
						const included = lectureIds.includes(lecture.id);
						const artifacts = presentationsByLecture.get(lecture.id) ?? [];
						return (
							<li
								key={lecture.id}
								className={included ? "is-included" : undefined}
							>
								<label className="release-lecture">
									<input
										type="checkbox"
										checked={included}
										onChange={() => toggleLecture(lecture.id)}
									/>
									<span className="release-lecture-number" aria-hidden="true">
										{index + 1}
									</span>
									<span className="row-main">
										<span className="row-title">{lecture.title}</span>
										{artifacts.length === 0 ? (
											<span className="row-meta">No Slides yet</span>
										) : null}
									</span>
								</label>
								{artifacts.length > 0 ? (
									<fieldset className="release-files" disabled={!included}>
										<legend className="visually-hidden">
											Files for {lecture.title}
										</legend>
										{artifacts.map((artifact) => (
											<label key={artifact.id} className="check-row">
												<input
													type="checkbox"
													checked={artifactIds.includes(artifact.id)}
													onChange={() => toggleArtifact(artifact.id)}
												/>
												<Presentation aria-hidden="true" />
												PowerPoint · {plural(artifact.slide_count, "Slide")}
											</label>
										))}
									</fieldset>
								) : null}
							</li>
						);
					})}
				</ol>
				<p className="release-summary" aria-live="polite">
					{plural(lectureIds.length, "Lecture")} ·{" "}
					{plural(artifactIds.length, "PowerPoint file")}
				</p>
				{lectureIds.length > 0 && artifactIds.length === 0 ? (
					<Notice tone="warning" role="status">
						No Presentation files included — this release records the plan only.
					</Notice>
				) : null}
			</section>

			<section
				className="canvas-section release-step"
				aria-labelledby="release-check-heading"
			>
				<div className="canvas-section-head">
					<span className="release-step-number" aria-hidden="true">
						2
					</span>
					<h2 id="release-check-heading">Check the release</h2>
					<button
						type="button"
						className="btn"
						disabled={validating || lectureIds.length === 0}
						onClick={() => void validate()}
					>
						{validating
							? "Checking…"
							: validation && !validationCurrent
								? "Check again"
								: "Check release"}
					</button>
				</div>
				{validation === null ? (
					<p className="meta">
						Checks structure, citations, and Source coverage for the selection.
					</p>
				) : (
					<div className={validationCurrent ? undefined : "release-stale"}>
						{!validationCurrent ? (
							<p className="meta">Your changes need a new check.</p>
						) : null}
						{validation.findings.length === 0 ? (
							<p className="release-clear">
								<CheckCircle2 aria-hidden="true" />
								No issues found.
							</p>
						) : (
							<ul className="release-findings">
								{[...errors, ...warnings].map((finding) => {
									const target = evidenceTarget(finding);
									return (
										<li
											key={finding.id}
											className={`release-finding is-${finding.severity}`}
										>
											<div className="release-finding-head">
												{finding.severity === "error" ? (
													<XCircle aria-hidden="true" />
												) : (
													<AlertTriangle aria-hidden="true" />
												)}
												<div>
													<p className="release-finding-kind">
														{finding.severity === "error"
															? "Blocks publishing"
															: "Warning"}
														<span className="meta">
															{" "}
															· {findingScope(finding, course)}
														</span>
													</p>
													<p>{finding.message}</p>
												</div>
											</div>
											<div className="release-finding-actions">
												{target ? (
													<button
														type="button"
														className="btn btn-small"
														onClick={() => onOpenEvidence(target)}
													>
														<FileSearch aria-hidden="true" />
														Open evidence
													</button>
												) : null}
												<button
													type="button"
													className="btn btn-quiet btn-small"
													onClick={() => askAgent(finding)}
												>
													<Sparkles aria-hidden="true" />
													Ask the agent to fix
												</button>
											</div>
											{finding.severity === "warning" ? (
												<div className="field release-waiver">
													<label htmlFor={`waiver-${finding.id}`}>
														Publish anyway? Record why
													</label>
													<textarea
														id={`waiver-${finding.id}`}
														rows={2}
														maxLength={1000}
														value={waiverText[finding.id] ?? ""}
														onChange={(event) => {
															setWaiverText((current) => ({
																...current,
																[finding.id]: event.target.value,
															}));
															invalidateValidation(false);
														}}
													/>
													<small>Your reason is stored with the release.</small>
												</div>
											) : null}
										</li>
									);
								})}
							</ul>
						)}
					</div>
				)}
			</section>

			<section
				className="canvas-section release-step"
				aria-labelledby="release-name-heading"
			>
				<div className="canvas-section-head">
					<span className="release-step-number" aria-hidden="true">
						3
					</span>
					<h2 id="release-name-heading">Name and publish</h2>
				</div>
				<div className="release-name-fields">
					<div className="field">
						<label htmlFor="release-name">Release name</label>
						<input
							id="release-name"
							value={name}
							maxLength={200}
							placeholder="Spring term, first half"
							onChange={(event) => {
								setName(event.target.value);
								if (!slugEdited) setSlug(slugify(event.target.value));
							}}
						/>
					</div>
					<div className="field">
						<label htmlFor="release-slug">Release ID</label>
						<input
							id="release-slug"
							className="mono"
							value={slug}
							maxLength={64}
							aria-invalid={Boolean(slug) && !isSlugValid}
							aria-describedby="release-slug-help"
							onChange={(event) => {
								setSlugEdited(true);
								setSlug(event.target.value.toLowerCase());
							}}
						/>
						<small id="release-slug-help">
							Lowercase letters, numbers, and hyphens. An ID can't be reused.
						</small>
					</div>
				</div>
				<div className="release-publish">
					<button
						type="button"
						className="btn btn-primary"
						disabled={!canPublish || publishing}
						aria-describedby={canPublish ? undefined : "release-blocker"}
						onClick={() => void publish()}
					>
						{publishing ? "Publishing…" : "Publish release"}
					</button>
					{!canPublish ? (
						<p id="release-blocker" className="meta">
							{publicationBlocker}
						</p>
					) : null}
				</div>
			</section>
		</div>
	);
}
