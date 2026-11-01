import { Dialog, errorMessage, isAbort, Notice } from "@course-harness/ui";
import {
	AlertTriangle,
	CheckCircle2,
	Circle,
	FileSearch,
	Info,
	Loader2,
	Presentation,
	Sparkles,
	XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { responseError, responseErrorDetail } from "../api";
import { findingScope } from "../canvas/ReleaseDetail";
import type {
	CoursePlan,
	CourseRelease,
	PresentationSummary,
	PushResult,
	ReaderTarget,
	ReleaseValidation,
	ReleaseValidationFinding,
	ReleaseWaiver,
	SharingStatus,
	Source,
} from "../models";
import { SYNC_FIRST } from "../shell/SharingControls";
import { RemoteSetup } from "./RemoteSetup";

const MAX_NOTES = 4000;

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

function plural(count: number, one: string, many = `${one}s`): string {
	return `${count} ${count === 1 ? one : many}`;
}

/** "Release 3 · 1 Nov 2026" */
export function defaultReleaseName(releaseCount: number, today = new Date()) {
	const date = new Intl.DateTimeFormat("en-GB", {
		day: "numeric",
		month: "short",
		year: "numeric",
	}).format(today);
	return `Release ${releaseCount + 1} · ${date}`;
}

/** The summaries of the Revisions since the last Release, and of the one about to be saved. */
export function defaultNotes(status: SharingStatus | null): string {
	if (!status) return "";
	const lines = [...status.revision_summaries];
	if (status.pending_summary) lines.push(status.pending_summary);
	return lines.join("\n").slice(0, MAX_NOTES);
}

type StepState = "waiting" | "running" | "done" | "failed";
type Steps = { save: StepState; publish: StepState; share: StepState | null };

type Props = {
	course: CoursePlan;
	presentations: PresentationSummary[];
	sources: Source[];
	status: SharingStatus | null;
	onAskAgent: (finding: ReleaseValidationFinding | string) => void;
	onOpenEvidence: (target: ReaderTarget) => void;
	onReviewChanges: () => void;
	onRemoteChanged: () => void;
	onPublished: () => void;
	onClose: () => void;
};

export function PublishDialog({
	course,
	presentations,
	sources,
	status,
	onAskAgent,
	onOpenEvidence,
	onReviewChanges,
	onRemoteChanged,
	onPublished,
	onClose,
}: Props) {
	const presentationIds = useMemo(
		() => new Set(presentations.map((presentation) => presentation.id)),
		[presentations],
	);
	const [lectureIds, setLectureIds] = useState<string[]>(() =>
		course.lectures.map((lecture) => lecture.id),
	);
	const [artifactIds, setArtifactIds] = useState<string[]>(() =>
		presentations.map((presentation) => presentation.id),
	);
	const [name, setName] = useState(() =>
		status ? defaultReleaseName(status.release_count) : "",
	);
	const [notes, setNotes] = useState(() => defaultNotes(status));
	const nameEditedRef = useRef(false);
	const notesEditedRef = useRef(false);
	const [validation, setValidation] = useState<ReleaseValidation | null>(null);
	const [checking, setChecking] = useState(false);
	const [waiverText, setWaiverText] = useState<Record<string, string>>({});
	const [share, setShare] = useState(Boolean(status?.remote_url));
	const [remoteLabel, setRemoteLabel] = useState(status?.remote_label ?? null);
	const [settingUp, setSettingUp] = useState(false);
	const [steps, setSteps] = useState<Steps | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [blockedFindings, setBlockedFindings] = useState<string[]>([]);
	const [published, setPublished] = useState<CourseRelease | null>(null);
	const [push, setPush] = useState<PushResult | null>(null);
	const requestRef = useRef<AbortController | null>(null);
	const doneRef = useRef<HTMLHeadingElement>(null);

	// The defaults depend on the status: fill them in once it has loaded, unless edited.
	const statusLoaded = status !== null;
	// biome-ignore lint/correctness/useExhaustiveDependencies: only the first status counts
	useEffect(() => {
		if (!status) return;
		if (!nameEditedRef.current)
			setName(defaultReleaseName(status.release_count));
		if (!notesEditedRef.current) setNotes(defaultNotes(status));
		if (status.remote_url) {
			setShare(true);
			setRemoteLabel(status.remote_label);
		}
	}, [statusLoaded]);

	const running = steps !== null && !published && !error;
	const behind = (status?.behind ?? 0) > 0;
	const blocked = status?.blocked ?? null;
	const findings = blockedFindings.length
		? blockedFindings
		: blocked === "invalid"
			? (status?.findings ?? [])
			: [];

	const byLecture = useMemo(() => {
		const map = new Map<string, PresentationSummary[]>();
		for (const presentation of presentations) {
			const entries = map.get(presentation.lecture_id) ?? [];
			entries.push(presentation);
			map.set(presentation.lecture_id, entries);
		}
		return map;
	}, [presentations]);

	const waivers = useCallback(
		(result: ReleaseValidation | null): ReleaseWaiver[] =>
			(result?.findings ?? [])
				.filter((finding) => finding.severity === "warning")
				.flatMap((finding) => {
					const justification = waiverText[finding.id]?.trim();
					return justification
						? [{ finding_id: finding.id, justification }]
						: [];
				}),
		[waiverText],
	);

	const validate = useCallback(
		async (withWaivers: ReleaseWaiver[]) => {
			requestRef.current?.abort();
			const controller = new AbortController();
			requestRef.current = controller;
			setChecking(true);
			try {
				const response = await fetch("/api/releases/validate", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					signal: controller.signal,
					body: JSON.stringify({
						selection: {
							lecture_ids: lectureIds,
							artifact_ids: artifactIds.filter((id) => presentationIds.has(id)),
						},
						waivers: withWaivers,
					}),
				});
				if (!response.ok) throw new Error(await responseError(response));
				const result = (await response.json()) as ReleaseValidation;
				if (!controller.signal.aborted) setValidation(result);
				return result;
			} catch (caught) {
				if (!isAbort(caught))
					setError(errorMessage(caught, "The release could not be checked."));
				return null;
			} finally {
				if (requestRef.current === controller) setChecking(false);
			}
		},
		[lectureIds, artifactIds, presentationIds],
	);

	// Check the selection whenever it changes, while Current State can be checked.
	useEffect(() => {
		if (blocked || lectureIds.length === 0) {
			setValidation(null);
			return;
		}
		void validate([]);
		return () => requestRef.current?.abort();
	}, [blocked, lectureIds, validate]);

	useEffect(() => {
		if (published) doneRef.current?.focus();
	}, [published]);

	function toggleLecture(lectureId: string) {
		const owned = (byLecture.get(lectureId) ?? []).map((item) => item.id);
		if (lectureIds.includes(lectureId)) {
			setLectureIds(lectureIds.filter((id) => id !== lectureId));
			setArtifactIds(artifactIds.filter((id) => !owned.includes(id)));
		} else {
			setLectureIds(
				course.lectures
					.map((lecture) => lecture.id)
					.filter((id) => id === lectureId || lectureIds.includes(id)),
			);
			setArtifactIds([
				...artifactIds,
				...owned.filter((id) => !artifactIds.includes(id)),
			]);
		}
	}

	function toggleArtifact(artifactId: string) {
		setArtifactIds(
			artifactIds.includes(artifactId)
				? artifactIds.filter((id) => id !== artifactId)
				: [...artifactIds, artifactId],
		);
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

	const errors =
		validation?.findings.filter((finding) => finding.severity === "error") ??
		[];
	const warnings =
		validation?.findings.filter((finding) => finding.severity === "warning") ??
		[];
	const notesFound =
		validation?.findings.filter((finding) => finding.severity === "info") ?? [];
	const missingReasons = warnings.filter(
		(finding) => !waiverText[finding.id]?.trim(),
	).length;
	const selectedArtifacts = artifactIds.filter((id) => presentationIds.has(id));

	const blocker = blocked
		? null
		: !name.trim()
			? "Give this release a name."
			: !slugify(name)
				? "Use letters or numbers in the name."
				: status?.remote_release_slugs.includes(slugify(name))
					? "A release with this name is already on the remote. Choose another name."
					: lectureIds.length === 0
						? "Choose at least one Lecture."
						: checking || validation === null
							? "Checking the release…"
							: errors.length > 0
								? "Fix the problems above to publish."
								: missingReasons > 0
									? `Record why you're publishing with ${plural(missingReasons, "warning")}.`
									: share && !remoteLabel
										? "Connect a remote, or untick Push."
										: null;

	function setStep(key: keyof Steps, value: StepState) {
		setSteps((current) => (current ? { ...current, [key]: value } : current));
	}

	async function publish() {
		if (blocker || running) return;
		setError(null);
		setBlockedFindings([]);
		setSteps({
			save: "running",
			publish: "waiting",
			share: share ? "waiting" : null,
		});
		let step: keyof Steps = "save";
		try {
			// 1. Save uncommitted edits as an automatic Course Revision.
			const saved = await fetch("/api/workspace/revisions/automatic", {
				method: "POST",
			});
			if (!saved.ok) {
				const detail = await responseErrorDetail(saved.clone());
				try {
					const payload = (await saved.json()) as {
						detail?: { findings?: string[] };
					};
					setBlockedFindings(payload.detail?.findings ?? []);
				} catch {
					// The message alone explains it.
				}
				throw new Error(detail.message);
			}
			setStep("save", "done");
			step = "publish";
			setStep("publish", "running");
			// 2. Check again with the reasons, then name the Release.
			const checked = await validate(waivers(validation));
			if (!checked?.can_publish)
				throw new Error(
					"The release has problems to fix before it can be published.",
				);
			const response = await fetch("/api/releases", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					name: name.trim(),
					slug: slugify(name),
					selection: checked.selection,
					waivers: checked.waivers,
					notes: notes.trim() || null,
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const release = (await response.json()) as CourseRelease;
			setStep("publish", "done");
			// The header counts the new Release right away.
			onPublished();
			// 3. Push it. A failed push keeps the Release; Sync can send it later.
			if (share) {
				step = "share";
				setStep("share", "running");
				let result: PushResult;
				try {
					const pushed = await fetch("/api/workspace/remote/push", {
						method: "POST",
					});
					result = pushed.ok
						? ((await pushed.json()) as PushResult)
						: {
								ok: false,
								remote: remoteLabel ?? "",
								message: await responseError(pushed),
								releases_without_files: [],
								releases_with_invalid_files: [],
							};
				} catch {
					result = {
						ok: false,
						remote: remoteLabel ?? "",
						message: "Course Harness could not start the push.",
						releases_without_files: [],
						releases_with_invalid_files: [],
					};
				}
				setPush(result);
				setStep("share", result.ok ? "done" : "failed");
			}
			setPublished(release);
			onPublished();
		} catch (caught) {
			setStep(step, "failed");
			setError(errorMessage(caught, "The release could not be published."));
			onPublished();
		}
	}

	const shareLabel = remoteLabel
		? `Push to ${remoteLabel}`
		: "Push to a Git remote";

	const footer = published ? (
		<button type="button" className="btn btn-primary" onClick={onClose}>
			Done
		</button>
	) : (
		<>
			{blocker && !running ? (
				<p className="meta publish-blocker" id="publish-blocker">
					{blocker}
				</p>
			) : null}
			<button
				type="button"
				className="btn"
				onClick={onClose}
				disabled={running}
			>
				Cancel
			</button>
			<button
				type="button"
				className="btn btn-primary"
				disabled={Boolean(blocker) || running || Boolean(blocked)}
				aria-describedby={blocker ? "publish-blocker" : undefined}
				onClick={() => void publish()}
			>
				{running ? "Publishing…" : "Publish"}
			</button>
		</>
	);

	return (
		<Dialog
			title="Publish a release"
			onClose={onClose}
			dismissible={!running}
			footer={footer}
			size="wide"
		>
			<div className="publish-dialog">
				{published ? (
					<section className="publish-done" aria-labelledby="publish-done">
						<Notice tone="success" role="status">
							<h3 id="publish-done" ref={doneRef} tabIndex={-1}>
								Published {published.name}
							</h3>
							{push?.ok ? (
								<p>
									Classrooms attached to this repository pick it up within 15
									minutes, or right away with Sync now.
								</p>
							) : null}
						</Notice>
						{push && !push.ok ? (
							<Notice tone="warning" role="alert">
								<p>
									The release is published on this computer, but it couldn't be
									pushed to {remoteLabel}. Use Sync to try again.
								</p>
								<details className="sync-detail">
									<summary>Details</summary>
									<pre>{push.message}</pre>
								</details>
							</Notice>
						) : null}
						<PublishSteps steps={steps} shareLabel={shareLabel} />
					</section>
				) : (
					<>
						<p className="meta publish-intro">
							A release is a permanent, checked record of your Course that a
							Classroom can serve.{" "}
							{status && status.changes_since_release > 0
								? `${plural(status.changes_since_release, "change")} since ${
										status.latest_release_name ?? "you started"
									}.`
								: null}
						</p>

						{blocked === "drift" ? (
							<Notice
								tone="warning"
								actions={
									<button
										type="button"
										className="btn btn-small"
										onClick={onReviewChanges}
									>
										Review changes
									</button>
								}
							>
								Files were changed outside Course Harness; review them in
								History first.
							</Notice>
						) : null}
						{behind ? (
							<Notice tone="warning">
								{SYNC_FIRST}. The remote has{" "}
								{plural(status?.behind ?? 0, "change")} this computer doesn't
								have yet.
							</Notice>
						) : null}
						{findings.length > 0 ? (
							<Notice tone="warning">
								<p>Fix these problems before you publish:</p>
								<ul className="sync-findings">
									{findings.map((finding) => (
										<li key={finding}>
											<span>{finding}</span>
											<button
												type="button"
												className="btn btn-quiet btn-small"
												onClick={() => onAskAgent(finding)}
											>
												<Sparkles aria-hidden="true" />
												Ask the agent to fix
											</button>
										</li>
									))}
								</ul>
							</Notice>
						) : null}

						<div className="field">
							<label htmlFor="publish-name">Name</label>
							<input
								id="publish-name"
								value={name}
								maxLength={200}
								disabled={running}
								onChange={(event) => {
									nameEditedRef.current = true;
									setName(event.target.value);
								}}
							/>
						</div>
						<div className="field">
							<label htmlFor="publish-notes">What changed</label>
							<textarea
								id="publish-notes"
								rows={4}
								maxLength={MAX_NOTES}
								value={notes}
								disabled={running}
								aria-describedby="publish-notes-help"
								onChange={(event) => {
									notesEditedRef.current = true;
									setNotes(event.target.value);
								}}
							/>
							<small id="publish-notes-help">
								Instructors see this beside the release in the Classroom.
							</small>
						</div>

						<details className="publish-contents">
							<summary>
								Includes {plural(lectureIds.length, "Lecture")} of{" "}
								{course.lectures.length} ·{" "}
								{plural(selectedArtifacts.length, "PowerPoint file")}
							</summary>
							<ol className="row-list release-lectures">
								{course.lectures.map((lecture, index) => {
									const included = lectureIds.includes(lecture.id);
									const files = byLecture.get(lecture.id) ?? [];
									return (
										<li
											key={lecture.id}
											className={included ? "is-included" : undefined}
										>
											<label className="release-lecture">
												<input
													type="checkbox"
													checked={included}
													disabled={running}
													onChange={() => toggleLecture(lecture.id)}
												/>
												<span
													className="release-lecture-number"
													aria-hidden="true"
												>
													{index + 1}
												</span>
												<span className="row-main">
													<span className="row-title">{lecture.title}</span>
													{files.length === 0 ? (
														<span className="row-meta">No Slides yet</span>
													) : null}
												</span>
											</label>
											{files.length > 0 ? (
												<fieldset
													className="release-files"
													disabled={!included || running}
												>
													<legend className="visually-hidden">
														Files for {lecture.title}
													</legend>
													{files.map((file) => (
														<label key={file.id} className="check-row">
															<input
																type="checkbox"
																checked={artifactIds.includes(file.id)}
																onChange={() => toggleArtifact(file.id)}
															/>
															<Presentation aria-hidden="true" />
															PowerPoint · {plural(file.slide_count, "Slide")}
														</label>
													))}
												</fieldset>
											) : null}
										</li>
									);
								})}
							</ol>
						</details>

						<section aria-label="Release check" className="publish-check">
							{checking && validation === null ? (
								<p className="meta" role="status">
									<Loader2 className="spin" aria-hidden="true" /> Checking the
									release…
								</p>
							) : null}
							{validation && errors.length === 0 && warnings.length === 0 ? (
								<p className="release-clear">
									<CheckCircle2 aria-hidden="true" />
									No issues found.
								</p>
							) : null}
							{validation && validation.findings.length > 0 ? (
								<ul className="release-findings">
									{[...errors, ...warnings, ...notesFound].map((finding) => {
										const target = evidenceTarget(finding);
										return (
											<li
												key={finding.id}
												className={`release-finding is-${finding.severity}`}
											>
												<div className="release-finding-head">
													{finding.severity === "error" ? (
														<XCircle aria-hidden="true" />
													) : finding.severity === "info" ? (
														<Info aria-hidden="true" />
													) : (
														<AlertTriangle aria-hidden="true" />
													)}
													<div>
														<p className="release-finding-kind">
															{finding.severity === "error"
																? "Blocks publishing"
																: finding.severity === "info"
																	? "Note · does not block publishing"
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
														onClick={() => onAskAgent(finding)}
													>
														<Sparkles aria-hidden="true" />
														Ask the agent to fix
													</button>
												</div>
												{finding.severity === "warning" ? (
													<div className="field release-waiver">
														<label htmlFor={`publish-waiver-${finding.id}`}>
															Publish anyway? Record why
														</label>
														<textarea
															id={`publish-waiver-${finding.id}`}
															rows={2}
															maxLength={1000}
															value={waiverText[finding.id] ?? ""}
															disabled={running}
															onChange={(event) =>
																setWaiverText((current) => ({
																	...current,
																	[finding.id]: event.target.value,
																}))
															}
														/>
														<small>
															Your reason is stored with the release.
														</small>
													</div>
												) : null}
											</li>
										);
									})}
								</ul>
							) : null}
						</section>

						<div className="publish-share">
							<label className="check-row">
								<input
									type="checkbox"
									checked={share}
									disabled={running}
									onChange={(event) => {
										setShare(event.target.checked);
										setSettingUp(event.target.checked && !remoteLabel);
									}}
								/>
								{shareLabel}
							</label>
							{settingUp ? (
								<div className="publish-remote-setup">
									<RemoteSetup
										onSaved={(settings) => {
											setRemoteLabel(
												settings.url ? labelFor(settings.url) : null,
											);
											setSettingUp(false);
											setShare(Boolean(settings.url));
											onRemoteChanged();
										}}
										onCancel={() => {
											setSettingUp(false);
											setShare(false);
										}}
									/>
								</div>
							) : null}
						</div>

						{steps ? (
							<PublishSteps steps={steps} shareLabel={shareLabel} />
						) : null}
						{error ? (
							<Notice tone="error" role="alert">
								{error}
							</Notice>
						) : null}
					</>
				)}
			</div>
		</Dialog>
	);
}

/** ``host/owner/repo`` for a Git URL, as the server names it. */
function labelFor(url: string): string {
	if (url.startsWith("/")) return url;
	const scp = /^(?:[^@/]+@)?([^:/]+):(.+)$/.exec(url);
	if (!url.includes("://") && scp)
		return `${scp[1]}/${scp[2].replace(/^\/+|\/+$/g, "").replace(/\.git$/, "")}`;
	try {
		const parsed = new URL(url);
		return `${parsed.hostname}/${parsed.pathname.replace(/^\/+|\/+$/g, "").replace(/\.git$/, "")}`;
	} catch {
		return url;
	}
}

function PublishSteps({
	steps,
	shareLabel,
}: {
	steps: Steps | null;
	shareLabel: string;
}) {
	if (!steps) return null;
	const rows: [string, StepState][] = [
		["Save your changes", steps.save],
		["Publish the release", steps.publish],
		...(steps.share ? [[shareLabel, steps.share] as [string, StepState]] : []),
	];
	return (
		<ol className="publish-steps" aria-label="Progress">
			{rows.map(([label, state]) => (
				<li key={label} className={`is-${state}`}>
					{state === "running" ? (
						<Loader2 className="spin" aria-hidden="true" />
					) : state === "done" ? (
						<CheckCircle2 aria-hidden="true" />
					) : state === "failed" ? (
						<XCircle aria-hidden="true" />
					) : (
						<Circle aria-hidden="true" />
					)}
					<span>{label}</span>
					<span className="visually-hidden">
						{state === "running"
							? " (in progress)"
							: state === "done"
								? " (done)"
								: state === "failed"
									? " (failed)"
									: ""}
					</span>
				</li>
			))}
		</ol>
	);
}
