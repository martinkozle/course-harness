import {
	type FormEvent,
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";

import type { CoursePlan } from "./AgentPanel";
import { responseError } from "./api";
import type {
	CourseRelease,
	CurrentState,
	EvidenceTarget,
	PresentationSummary,
	ReleaseValidation,
	ReleaseValidationFinding,
	ReleaseWaiver,
	Source,
} from "./models";

type ReleasesViewProps = {
	course: CoursePlan;
	presentations: PresentationSummary[];
	sources: Source[];
	onOpenEvidence: (target: EvidenceTarget) => void;
	onOpenAgent: (context: string) => void;
};

const RELEASE_SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

function formatPublishedAt(value: string): string {
	const date = new Date(value);
	return Number.isNaN(date.getTime())
		? value
		: new Intl.DateTimeFormat(undefined, {
				dateStyle: "medium",
				timeStyle: "short",
			}).format(date);
}

function selectionKey(lectureIds: string[], artifactIds: string[]): string {
	return `${lectureIds.join("\u0001")}\u0000${artifactIds.join("\u0001")}`;
}

function findingScope(finding: ReleaseValidationFinding): string {
	const target = finding.target;
	const parts = [target.scope.replaceAll("_", " ")];
	if (target.lecture_id) parts.push(target.lecture_id);
	if (target.artifact_id) parts.push(target.artifact_id);
	if (target.slide_id) parts.push(target.slide_id);
	if (target.content_block) parts.push(target.content_block);
	return parts.join(" · ");
}

function sourceContext(sources: Source[]): string {
	if (sources.length === 0) return "No admitted Sources are available yet.";
	return `Admitted Sources: ${sources
		.slice(0, 8)
		.map((source) => `${source.label} (${source.id})`)
		.join(", ")}.`;
}

function evidenceTargetFor(
	finding: ReleaseValidationFinding,
	sources: Source[],
): EvidenceTarget {
	const target = finding.target;
	const hasAdmittedSource =
		target.scope === "citation" &&
		target.source_id !== null &&
		sources.some((source) => source.id === target.source_id);
	return {
		source_id: hasAdmittedSource ? target.source_id : null,
		line_start: hasAdmittedSource ? target.line_start : null,
		line_end: hasAdmittedSource ? target.line_end : null,
	};
}

export function ReleasesView({
	course,
	presentations,
	sources,
	onOpenEvidence,
	onOpenAgent,
}: ReleasesViewProps) {
	const [lectureIds, setLectureIds] = useState<string[]>([]);
	const [artifactIds, setArtifactIds] = useState<string[]>([]);
	const [name, setName] = useState("");
	const [slug, setSlug] = useState("");
	const [validation, setValidation] = useState<ReleaseValidation | null>(null);
	const [validatedKey, setValidatedKey] = useState<string | null>(null);
	const [waiverText, setWaiverText] = useState<Record<string, string>>({});
	const [releases, setReleases] = useState<CourseRelease[]>([]);
	const [selectedRelease, setSelectedRelease] = useState<CourseRelease | null>(
		null,
	);
	const [currentState, setCurrentState] = useState<CurrentState | null>(null);
	const [loading, setLoading] = useState(true);
	const [validating, setValidating] = useState(false);
	const [publishing, setPublishing] = useState(false);
	const [loadingDetail, setLoadingDetail] = useState<string | null>(null);
	const [downloading, setDownloading] = useState<string | null>(null);
	const [status, setStatus] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const initialRequestRef = useRef<AbortController | null>(null);
	const validationRequestRef = useRef<AbortController | null>(null);
	const validationGenerationRef = useRef(0);
	const detailHeadingRef = useRef<HTMLHeadingElement | null>(null);

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
	const selectedLectureSet = useMemo(() => new Set(lectureIds), [lectureIds]);
	const isSlugValid = RELEASE_SLUG_PATTERN.test(slug);
	const canPublish =
		validation?.can_publish === true &&
		validatedKey === selectedKey &&
		Boolean(name.trim()) &&
		isSlugValid;
	const publicationBlocker = !name.trim()
		? "Give this Release a name."
		: !slug.trim()
			? "Give this Release a stable lowercase slug."
			: !isSlugValid
				? "Use a lowercase slug with letters, numbers, and internal hyphens only."
				: lectureIds.length === 0
					? "Choose at least one Lecture."
					: validation === null || validatedKey !== selectedKey
						? "Validate the current selection before publishing."
						: !validation.can_publish
							? "Resolve structural findings and add a justification for every warning."
							: "";

	const loadReleases = useCallback(async (signal?: AbortSignal) => {
		const response = await fetch("/api/releases", { signal });
		if (!response.ok) throw new Error(await responseError(response));
		setReleases((await response.json()) as CourseRelease[]);
	}, []);

	const refreshCurrentState = useCallback(async (signal?: AbortSignal) => {
		const response = await fetch("/api/workspace/current-state", { signal });
		if (response.status === 404) {
			setCurrentState(null);
			return null;
		}
		if (!response.ok) throw new Error(await responseError(response));
		const state = (await response.json()) as CurrentState;
		setCurrentState(state);
		return state;
	}, []);

	useEffect(() => {
		const controller = new AbortController();
		initialRequestRef.current = controller;
		void Promise.all([
			loadReleases(controller.signal),
			refreshCurrentState(controller.signal),
		])
			.catch((caught) => {
				if (!(caught instanceof DOMException && caught.name === "AbortError")) {
					setError(
						caught instanceof Error
							? caught.message
							: "Releases could not be read.",
					);
				}
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false);
			});
		return () => controller.abort();
	}, [loadReleases, refreshCurrentState]);

	useEffect(
		() => () => {
			validationRequestRef.current?.abort();
		},
		[],
	);

	useEffect(() => {
		if (selectedRelease) detailHeadingRef.current?.focus();
	}, [selectedRelease]);

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
		const selected = selectedLectureSet.has(lectureId);
		const nextLectures = selected
			? lectureIds.filter((id) => id !== lectureId)
			: [...lectureIds, lectureId];
		const ownedArtifacts = new Set(
			(presentationsByLecture.get(lectureId) ?? []).map(
				(presentation) => presentation.id,
			),
		);
		changeSelection(
			nextLectures,
			selected
				? artifactIds.filter((id) => !ownedArtifacts.has(id))
				: artifactIds,
		);
	}

	function toggleArtifact(artifactId: string) {
		changeSelection(
			lectureIds,
			artifactIds.includes(artifactId)
				? artifactIds.filter((id) => id !== artifactId)
				: [...artifactIds, artifactId],
		);
	}

	function waiversForCurrentFindings(): ReleaseWaiver[] {
		return (validation?.findings ?? [])
			.filter((finding) => finding.severity === "warning")
			.flatMap((finding) => {
				const justification = waiverText[finding.id]?.trim();
				return justification ? [{ finding_id: finding.id, justification }] : [];
			});
	}

	async function validate(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		if (lectureIds.length === 0) return;
		validationRequestRef.current?.abort();
		const controller = new AbortController();
		validationRequestRef.current = controller;
		const generation = ++validationGenerationRef.current;
		const requestKey = selectedKey;
		const requestLectureIds = [...lectureIds];
		const requestArtifactIds = [...artifactIds];
		const requestWaivers = waiversForCurrentFindings();
		setValidating(true);
		setError(null);
		setStatus(null);
		try {
			const response = await fetch("/api/releases/validate", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				signal: controller.signal,
				body: JSON.stringify({
					selection: {
						lecture_ids: requestLectureIds,
						artifact_ids: requestArtifactIds,
					},
					waivers: requestWaivers,
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const result = (await response.json()) as ReleaseValidation;
			if (
				controller.signal.aborted ||
				generation !== validationGenerationRef.current
			) {
				return;
			}
			setValidation(result);
			setValidatedKey(requestKey);
			setStatus(
				"Validation is current for this selection. Publishing rechecks Current State.",
			);
			try {
				await refreshCurrentState(controller.signal);
			} catch (caught) {
				if (!controller.signal.aborted) {
					setError(
						caught instanceof Error
							? caught.message
							: "Current State could not be refreshed after validation.",
					);
				}
			}
		} catch (caught) {
			if (!(caught instanceof DOMException && caught.name === "AbortError")) {
				setError(
					caught instanceof Error
						? caught.message
						: "Release validation failed.",
				);
			}
		} finally {
			if (
				!controller.signal.aborted &&
				generation === validationGenerationRef.current
			) {
				setValidating(false);
			}
		}
	}

	async function publish() {
		if (!canPublish) return;
		setPublishing(true);
		setError(null);
		setStatus(null);
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
			setSelectedRelease(release);
			setName("");
			setSlug("");
			setValidation(null);
			setValidatedKey(null);
			setWaiverText({});
			await loadReleases();
			setStatus(`Published ${release.name}; its immutable detail is now open.`);
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "Release could not be published.",
			);
		} finally {
			setPublishing(false);
		}
	}

	async function openRelease(slug: string) {
		setLoadingDetail(slug);
		setError(null);
		setStatus(`Opening immutable detail for ${slug}.`);
		try {
			const response = await fetch(`/api/releases/${encodeURIComponent(slug)}`);
			if (!response.ok) throw new Error(await responseError(response));
			const release = (await response.json()) as CourseRelease;
			setSelectedRelease(release);
			setStatus(`Immutable detail for ${release.name} is open.`);
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "Release detail could not be read.",
			);
		} finally {
			setLoadingDetail(null);
		}
	}

	async function retrieveArtifact(
		release: CourseRelease,
		artifactId: string,
		regenerate: boolean,
	) {
		const action = `${release.slug}:${artifactId}:${regenerate ? "regenerate" : "download"}`;
		setDownloading(action);
		setError(null);
		try {
			const suffix = regenerate ? "/regenerate" : "";
			const response = await fetch(
				`/api/releases/${encodeURIComponent(release.slug)}/artifacts/${encodeURIComponent(artifactId)}${suffix}`,
				regenerate ? { method: "POST" } : undefined,
			);
			if (!response.ok) throw new Error(await responseError(response));
			const objectUrl = URL.createObjectURL(await response.blob());
			const anchor = document.createElement("a");
			anchor.href = objectUrl;
			anchor.download = `${release.slug}-${artifactId}${regenerate ? "-regenerated" : ""}.pptx`;
			document.body.append(anchor);
			anchor.click();
			setTimeout(() => {
				anchor.remove();
				URL.revokeObjectURL(objectUrl);
			}, 0);
			setStatus(
				regenerate
					? "Regenerated artifact matches the published checksum and was downloaded."
					: "Stored artifact downloaded.",
			);
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "Release artifact could not be retrieved.",
			);
		} finally {
			setDownloading(null);
		}
	}

	function handFindingToAgent(finding: ReleaseValidationFinding) {
		onOpenAgent(
			`Release validation needs editorial review. Finding: ${finding.message} Scope: ${findingScope(finding)}. ${sourceContext(sources)} Review the supporting Evidence and propose a correction; do not waive a finding unless the Course Author explicitly decides to publish with that warning.`,
		);
	}

	const warnings =
		validation?.findings.filter((finding) => finding.severity === "warning") ??
		[];
	const errors =
		validation?.findings.filter((finding) => finding.severity === "error") ??
		[];

	return (
		<main
			className="page-main releases-main"
			aria-labelledby="releases-heading"
		>
			<header className="page-heading releases-heading">
				<p className="eyebrow">Publication ledger</p>
				<h1 id="releases-heading">Releases</h1>
				<p className="lede">
					Select the Lectures and Presentation artifacts you are ready to
					publish. The ledger records what is included and what remains planned.
				</p>
			</header>

			{error ? (
				<p className="notice error-notice" role="alert">
					{error}
				</p>
			) : null}
			<p className="release-live-status" aria-live="polite">
				{status}
			</p>

			<section
				className="release-ledger"
				aria-label="Prepare a Release"
				aria-busy={loading}
			>
				<div className="release-spine" aria-hidden="true" />
				<form
					className="release-preparation"
					onSubmit={(event) => void validate(event)}
				>
					<section
						className="release-selection"
						aria-labelledby="release-selection-heading"
					>
						<div className="release-section-heading">
							<div>
								<p className="section-kicker">01 · Coverage</p>
								<h2 id="release-selection-heading">
									Choose the published spine
								</h2>
							</div>
							<span className="count-badge">
								{lectureIds.length} / {course.lectures.length}
							</span>
						</div>
						<ol className="release-lecture-list">
							{course.lectures.map((lecture, index) => {
								const included = selectedLectureSet.has(lecture.id);
								const artifacts = presentationsByLecture.get(lecture.id) ?? [];
								return (
									<li
										className={included ? "is-included" : "is-planned"}
										key={lecture.id}
									>
										<label className="release-lecture-toggle">
											<input
												type="checkbox"
												checked={included}
												onChange={() => toggleLecture(lecture.id)}
											/>
											<span
												className="release-lecture-index"
												aria-hidden="true"
											>
												{String(index + 1).padStart(2, "0")}
											</span>
											<span>
												<strong>{lecture.title}</strong>
												<small>
													{included
														? "Included in this Release"
														: "Planned, not published"}
												</small>
											</span>
										</label>
										{artifacts.length > 0 ? (
											<fieldset
												className="release-artifact-list"
												disabled={!included}
											>
												<legend>
													Presentation artifacts for {lecture.title}
												</legend>
												{artifacts.map((artifact) => (
													<label key={artifact.id}>
														<input
															type="checkbox"
															checked={artifactIds.includes(artifact.id)}
															onChange={() => toggleArtifact(artifact.id)}
														/>
														<span>
															Presentation · {artifact.slide_count} slides
														</span>
													</label>
												))}
											</fieldset>
										) : (
											<small className="release-no-artifact">
												No Presentation artifact is attached.
											</small>
										)}
									</li>
								);
							})}
						</ol>
					</section>

					<section
						className="release-editorial"
						aria-labelledby="release-editorial-heading"
					>
						<div className="release-section-heading">
							<div>
								<p className="section-kicker">02 · Editorial check</p>
								<h2 id="release-editorial-heading">Findings at the margin</h2>
							</div>
						</div>
						{currentState &&
						(!currentState.clean ||
							!currentState.validation.valid ||
							currentState.drift !== "clean") ? (
							<p className="release-current-state-warning">
								Current State is not publishable. Resolve it in Current State,
								then validate again.
							</p>
						) : null}
						{validation === null ? (
							<p className="empty-note">
								Validate this selection to inspect structural and grounding
								findings.
							</p>
						) : (
							<>
								{errors.length > 0 ? (
									<FindingList
										findings={errors}
										onOpenEvidence={onOpenEvidence}
										onOpenAgent={handFindingToAgent}
										sources={sources}
									/>
								) : null}
								{warnings.length > 0 ? (
									<FindingList
										findings={warnings}
										onOpenEvidence={onOpenEvidence}
										onOpenAgent={handFindingToAgent}
										sources={sources}
										waiverText={waiverText}
										onWaiverChange={(findingId, value) => {
											setWaiverText((current) => ({
												...current,
												[findingId]: value,
											}));
											invalidateValidation(false);
										}}
									/>
								) : null}
								{validation.findings.length === 0 ? (
									<p className="release-clear">
										No findings. This selection is ready to publish.
									</p>
								) : null}
							</>
						)}
					</section>

					<section
						className="release-publish"
						aria-labelledby="release-publish-heading"
					>
						<div>
							<p className="section-kicker">03 · Immutable record</p>
							<h2 id="release-publish-heading">Name this Release</h2>
						</div>
						<div className="release-name-fields">
							<label htmlFor="release-name">
								Release name
								<input
									id="release-name"
									value={name}
									onChange={(event) => setName(event.target.value)}
									maxLength={200}
									required
								/>
							</label>
							<label htmlFor="release-slug">
								Release slug
								<input
									id="release-slug"
									value={slug}
									onChange={(event) =>
										setSlug(event.target.value.toLowerCase())
									}
									pattern="[a-z0-9](?:[a-z0-9\\-]{0,62}[a-z0-9])?"
									maxLength={64}
									placeholder="for-example-v1"
									required
									aria-invalid={Boolean(slug) && !isSlugValid}
									aria-describedby={
										isSlugValid || !slug
											? "release-slug-help"
											: "release-slug-help release-slug-error"
									}
								/>
							</label>
						</div>
						<small id="release-slug-help">
							Lowercase letters, numbers, and hyphens. A slug cannot be reused.
						</small>
						{slug && !isSlugValid ? (
							<small id="release-slug-error" className="field-error">
								Use lowercase letters or numbers, with hyphens only between
								them.
							</small>
						) : null}
						<div className="release-publish-actions">
							<button
								className="secondary-action"
								type="submit"
								disabled={validating || lectureIds.length === 0}
							>
								{validating ? "Validating…" : "Validate selection"}
							</button>
							<button
								className="primary-action"
								type="button"
								disabled={!canPublish || publishing}
								onClick={() => void publish()}
								aria-describedby={
									canPublish ? undefined : "release-publication-blocker"
								}
							>
								{publishing ? "Publishing…" : "Publish Release"}
							</button>
						</div>
						{!canPublish ? (
							<p
								id="release-publication-blocker"
								className="release-publication-blocker"
							>
								{publicationBlocker}
							</p>
						) : null}
					</section>
				</form>
			</section>

			<section
				className="release-history"
				aria-labelledby="release-history-heading"
				aria-busy={loading}
			>
				<div className="release-section-heading">
					<div>
						<p className="section-kicker">Published records</p>
						<h2 id="release-history-heading">Release history</h2>
					</div>
					<button
						className="quiet-action"
						type="button"
						onClick={() =>
							void loadReleases().catch((caught) =>
								setError(
									caught instanceof Error
										? caught.message
										: "Releases could not be refreshed.",
								),
							)
						}
						disabled={loading}
					>
						Refresh
					</button>
				</div>
				{releases.length === 0 ? (
					<p className="empty-note">
						No Releases have been published from this Course.
					</p>
				) : (
					<ul className="release-history-list">
						{releases.map((release) => (
							<li key={release.slug}>
								<div>
									<strong>{release.name}</strong>
									<small>
										{release.slug} · {formatPublishedAt(release.published_at)}
									</small>
								</div>
								<span>{release.included_lecture_ids.length} included</span>
								<button
									className="secondary-action compact-action"
									type="button"
									onClick={() => void openRelease(release.slug)}
									disabled={loadingDetail === release.slug}
								>
									{loadingDetail === release.slug ? "Opening…" : "Inspect"}
								</button>
							</li>
						))}
					</ul>
				)}
			</section>

			{selectedRelease ? (
				<ReleaseDetail
					release={selectedRelease}
					downloading={downloading}
					onArtifact={retrieveArtifact}
					headingRef={detailHeadingRef}
				/>
			) : null}
		</main>
	);
}

function FindingList({
	findings,
	sources,
	onOpenEvidence,
	onOpenAgent,
	waiverText,
	onWaiverChange,
}: {
	findings: ReleaseValidationFinding[];
	sources: Source[];
	onOpenEvidence: (target: EvidenceTarget) => void;
	onOpenAgent: (finding: ReleaseValidationFinding) => void;
	waiverText?: Record<string, string>;
	onWaiverChange?: (findingId: string, value: string) => void;
}) {
	return (
		<ul className="release-findings">
			{findings.map((finding) => {
				const evidenceTarget = evidenceTargetFor(finding, sources);
				return (
					<li
						className={`release-finding is-${finding.severity}`}
						key={finding.id}
					>
						<p className="release-finding-scope">{findingScope(finding)}</p>
						<p>{finding.message}</p>
						<div className="release-finding-actions">
							<button
								className="quiet-action"
								type="button"
								onClick={() => onOpenEvidence(evidenceTarget)}
							>
								{evidenceTarget.source_id
									? "Open supporting Evidence"
									: "Find supporting Evidence"}
							</button>
							<button
								className="quiet-action"
								type="button"
								onClick={() => onOpenAgent(finding)}
							>
								Hand to Course Agent
							</button>
						</div>
						{finding.severity === "warning" && onWaiverChange ? (
							<label
								className="release-waiver"
								htmlFor={`waiver-${finding.id}`}
							>
								Publish with this warning only if you record why.
								<textarea
									id={`waiver-${finding.id}`}
									value={waiverText?.[finding.id] ?? ""}
									onChange={(event) =>
										onWaiverChange(finding.id, event.target.value)
									}
									maxLength={1000}
									aria-describedby={`waiver-${finding.id}-help`}
								/>
								<small id={`waiver-${finding.id}-help`}>
									{sources.length > 0
										? "Your justification is recorded with this immutable Release."
										: "Add admitted Sources before relying on this warning decision."}
								</small>
							</label>
						) : null}
					</li>
				);
			})}
		</ul>
	);
}

function ReleaseDetail({
	release,
	downloading,
	onArtifact,
	headingRef,
}: {
	release: CourseRelease;
	downloading: string | null;
	onArtifact: (
		release: CourseRelease,
		artifactId: string,
		regenerate: boolean,
	) => Promise<void>;
	headingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
	const waiverByFinding = new Map(
		release.validation.waivers.map((waiver) => [
			waiver.finding_id,
			waiver.justification,
		]),
	);
	return (
		<section
			className="release-detail"
			aria-labelledby="release-detail-heading"
		>
			<div className="release-section-heading">
				<div>
					<p className="section-kicker">Immutable detail</p>
					<h2 id="release-detail-heading" ref={headingRef} tabIndex={-1}>
						{release.name}
					</h2>
					<p>
						{release.tag} · {release.revision_id}
					</p>
				</div>
			</div>
			<div className="release-coverage-summary">
				<p>
					<strong>{release.included_lecture_ids.length}</strong> lectures
					included
				</p>
				<p>
					<strong>{release.planned_unpublished_lecture_ids.length}</strong>{" "}
					still planned
				</p>
			</div>
			<section
				className="release-detail-section"
				aria-labelledby="release-validation-history-heading"
			>
				<h3 id="release-validation-history-heading">Validation record</h3>
				{release.validation.findings.length === 0 ? (
					<p className="empty-note">
						No findings were recorded for this Release.
					</p>
				) : (
					<ul className="release-history-findings">
						{release.validation.findings.map((finding) => (
							<li className={`is-${finding.severity}`} key={finding.id}>
								<p className="release-finding-scope">{findingScope(finding)}</p>
								<p>{finding.message}</p>
								{finding.waived ? (
									<p className="release-historic-waiver">
										Waived with justification: {waiverByFinding.get(finding.id)}
									</p>
								) : null}
							</li>
						))}
					</ul>
				)}
			</section>
			<section
				className="release-detail-section"
				aria-labelledby="release-provenance-heading"
			>
				<h3 id="release-provenance-heading">Pinned provenance</h3>
				<div className="release-template-pin">
					<p className="ledger-label">Template Profile</p>
					<strong>
						{release.template_profile.definition.name} · v
						{release.template_profile.version}
					</strong>
					<dl>
						<div>
							<dt>Profile</dt>
							<dd>{release.template_profile.id}</dd>
						</div>
						<div>
							<dt>Profile SHA-256</dt>
							<dd>{release.template_profile.profile_sha256}</dd>
						</div>
						{release.template_profile.template_sha256 ? (
							<div>
								<dt>Template SHA-256</dt>
								<dd>{release.template_profile.template_sha256}</dd>
							</div>
						) : null}
					</dl>
				</div>
				<p className="ledger-label">Source Versions</p>
				{release.sources.length === 0 ? (
					<p className="empty-note">No Source Versions were pinned.</p>
				) : (
					<ul className="release-provenance-list">
						{release.sources.map((source) => (
							<li key={source.id}>
								<strong>{source.label}</strong>
								<small>
									{source.id} · {source.resource_id} ·{" "}
									{source.source_version_id}
								</small>
							</li>
						))}
					</ul>
				)}
			</section>
			<h3>Stored artifacts</h3>
			{release.artifacts.length === 0 ? (
				<p className="empty-note">
					This Release records no Presentation artifacts.
				</p>
			) : (
				<ul className="release-artifact-history">
					{release.artifacts.map((artifact) => {
						const downloadKey = `${release.slug}:${artifact.id}:download`;
						const regenerateKey = `${release.slug}:${artifact.id}:regenerate`;
						return (
							<li key={artifact.id}>
								<div>
									<strong>{artifact.id}</strong>
									<small>
										{artifact.size.toLocaleString()} bytes · SHA-256{" "}
										{artifact.sha256.slice(0, 12)}…
									</small>
								</div>
								<div>
									<button
										className="secondary-action compact-action"
										type="button"
										disabled={downloading === downloadKey}
										onClick={() => void onArtifact(release, artifact.id, false)}
									>
										Download
									</button>
									<button
										className="quiet-action"
										type="button"
										disabled={downloading === regenerateKey}
										onClick={() => void onArtifact(release, artifact.id, true)}
									>
										Regenerate and verify
									</button>
								</div>
							</li>
						);
					})}
				</ul>
			)}
		</section>
	);
}
