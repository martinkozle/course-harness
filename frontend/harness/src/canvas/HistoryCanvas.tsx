import {
	AlertTriangle,
	ChevronRight,
	FileText,
	History as HistoryIcon,
	LibraryBig,
	ListTree,
	Presentation,
	RotateCcw,
	Send,
} from "lucide-react";
import {
	type FormEvent,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";

import { responseError } from "../api";
import type {
	CoursePlan,
	CourseRelease,
	CourseRevision,
	CurrentState,
	CurrentStateFile,
	PresentationSummary,
} from "../models";
import { ConfirmDialog, errorMessage, isAbort, Notice, Tabs } from "../ui";
import { formatDate, ReleaseDetail } from "./ReleaseDetail";

export type HistoryTab = "changes" | "revisions" | "releases";

type Props = {
	initialTab?: HistoryTab;
	course: CoursePlan | null;
	presentations: PresentationSummary[];
	onChanged: () => Promise<void>;
	onReconcile: (driftId: string) => void;
	onPublish: () => void;
};

type DomainItem = {
	kind: "plan" | "slides" | "sources" | "file";
	name: string;
	/** Used inside sentences such as "Undo changes to …". */
	object: string;
};

export function describePath(
	path: string,
	course: CoursePlan | null,
	presentations: PresentationSummary[],
): DomainItem {
	if (path === "course.yaml")
		return { kind: "plan", name: "Course Plan", object: "the Course Plan" };
	if (path === "sources.yaml")
		return {
			kind: "sources",
			name: "Course Sources",
			object: "the Course Sources",
		};
	const match = /^presentations\/(.+)\.yaml$/.exec(path);
	if (match) {
		const presentation = presentations.find((item) => item.id === match[1]);
		const index =
			course?.lectures.findIndex(
				(lecture) => lecture.id === presentation?.lecture_id,
			) ?? -1;
		if (course && index >= 0) {
			const name = `Slides for Lecture ${index + 1} · ${course.lectures[index].title}`;
			return {
				kind: "slides",
				name,
				object: `the Slides for Lecture ${index + 1}`,
			};
		}
		return {
			kind: "slides",
			name: `Presentation ${match[1]}`,
			object: `Presentation ${match[1]}`,
		};
	}
	return { kind: "file", name: path, object: path };
}

const statusWord: Record<CurrentStateFile["status"], string> = {
	added: "Added",
	modified: "Changed",
	deleted: "Removed",
};

function ItemIcon({ kind }: { kind: DomainItem["kind"] }) {
	const Icon =
		kind === "plan"
			? ListTree
			: kind === "slides"
				? Presentation
				: kind === "sources"
					? LibraryBig
					: FileText;
	return <Icon className="row-icon" aria-hidden="true" />;
}

function suggestedSummary(items: DomainItem[]): string {
	const kinds = new Set(items.map((item) => item.kind));
	const parts: string[] = [];
	if (kinds.has("plan")) parts.push("Course Plan");
	if (kinds.has("slides")) parts.push("Slides");
	if (kinds.has("sources")) parts.push("Sources");
	if (parts.length === 0) return "Update course files";
	return `Update ${parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`}`;
}

function uniqueFindings(findings: string[]) {
	const seen = new Map<string, number>();
	return findings.map((text) => {
		const count = seen.get(text) ?? 0;
		seen.set(text, count + 1);
		return { key: `${text}-${count}`, text };
	});
}

type Confirmation =
	| { kind: "revert"; file: CurrentStateFile; item: DomainItem }
	| { kind: "restore"; revision: CourseRevision }
	| { kind: "accept-drift" };

export function HistoryCanvas({
	initialTab,
	course,
	presentations,
	onChanged,
	onReconcile,
	onPublish,
}: Props) {
	const [tab, setTab] = useState<HistoryTab>(initialTab ?? "changes");
	const [state, setState] = useState<CurrentState | null>(null);
	const [notInitialized, setNotInitialized] = useState(false);
	const [revisions, setRevisions] = useState<CourseRevision[]>([]);
	const [revisionsStale, setRevisionsStale] = useState(false);
	const [releases, setReleases] = useState<CourseRelease[]>([]);
	const [openRelease, setOpenRelease] = useState<CourseRelease | null>(null);
	const [loadingRelease, setLoadingRelease] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const [mutating, setMutating] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [confirmError, setConfirmError] = useState<string | null>(null);
	const [summary, setSummary] = useState("");
	const [summaryTouched, setSummaryTouched] = useState(false);
	const [driftSummary, setDriftSummary] = useState("");
	const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
	const requestRef = useRef<AbortController | null>(null);
	const generationRef = useRef(0);

	useEffect(() => {
		if (initialTab) setTab(initialTab);
	}, [initialTab]);

	const load = useCallback(async (signal: AbortSignal, generation: number) => {
		setError(null);
		const readState = async () => {
			const response = await fetch("/api/workspace/current-state", { signal });
			if (generation !== generationRef.current) return;
			if (response.status === 404) {
				setState(null);
				setNotInitialized(true);
				return;
			}
			if (!response.ok) throw new Error(await responseError(response));
			const payload = (await response.json()) as CurrentState;
			if (generation !== generationRef.current) return;
			setNotInitialized(false);
			setState(payload);
		};
		const readRevisions = async () => {
			try {
				const response = await fetch("/api/workspace/revisions", { signal });
				if (generation !== generationRef.current) return;
				if (response.status === 404) {
					setRevisions([]);
					setRevisionsStale(false);
					return;
				}
				if (!response.ok) throw new Error(await responseError(response));
				const payload = (await response.json()) as CourseRevision[];
				if (generation !== generationRef.current) return;
				setRevisions(payload);
				setRevisionsStale(false);
			} catch (caught) {
				if (!isAbort(caught)) setRevisionsStale(true);
				throw caught;
			}
		};
		const readReleases = async () => {
			const response = await fetch("/api/releases", { signal });
			if (!response.ok) throw new Error(await responseError(response));
			const payload = (await response.json()) as CourseRelease[];
			if (generation === generationRef.current) setReleases(payload);
		};
		try {
			const results = await Promise.allSettled([
				readState(),
				readRevisions(),
				readReleases(),
			]);
			const failure = results.find(
				(result): result is PromiseRejectedResult =>
					result.status === "rejected" && !isAbort(result.reason),
			);
			if (failure && generation === generationRef.current)
				setError(errorMessage(failure.reason, "History could not be read."));
		} finally {
			if (!signal.aborted && generation === generationRef.current)
				setLoading(false);
		}
	}, []);

	const reload = useCallback(() => {
		requestRef.current?.abort();
		const controller = new AbortController();
		const generation = ++generationRef.current;
		requestRef.current = controller;
		return load(controller.signal, generation);
	}, [load]);

	useEffect(() => {
		void reload();
		return () => {
			generationRef.current += 1;
			requestRef.current?.abort();
		};
	}, [reload]);

	const items = (state?.changes ?? []).map((file) => ({
		file,
		item: describePath(file.path, course, presentations),
	}));
	const suggestion = suggestedSummary(items.map(({ item }) => item));
	useEffect(() => {
		if (!summaryTouched) setSummary(suggestion);
	}, [suggestion, summaryTouched]);

	const unresolvedDrift =
		state !== null && (state.drift === "drift" || state.drift === "unknown");
	const interruptedRun = state?.interrupted_run === true;
	const changesName = interruptedRun ? "these changes" : "outside changes";
	const theChanges = interruptedRun ? "these changes" : "the outside changes";

	async function mutate(
		key: string,
		url: string,
		init: RequestInit,
	): Promise<boolean> {
		if (mutating) return false;
		setMutating(key);
		setError(null);
		setConfirmError(null);
		try {
			const response = await fetch(url, init);
			if (!response.ok) throw new Error(await responseError(response));
			await reload();
			await onChanged();
			return true;
		} catch (caught) {
			const message = errorMessage(caught, "The change could not be applied.");
			if (confirmation) setConfirmError(message);
			else setError(message);
			return false;
		} finally {
			setMutating(null);
		}
	}

	async function createRevision(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const done = await mutate("create-revision", "/api/workspace/revisions", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ summary: summary.trim() }),
		});
		if (done) {
			setSummaryTouched(false);
			setTab("revisions");
		}
	}

	async function confirm() {
		if (!confirmation) return;
		let done = false;
		if (confirmation.kind === "revert") {
			done = await mutate(
				`revert:${confirmation.file.path}`,
				"/api/workspace/current-state/revert",
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ path: confirmation.file.path }),
				},
			);
		} else if (confirmation.kind === "restore") {
			done = await mutate(
				`restore:${confirmation.revision.id}`,
				`/api/workspace/revisions/${encodeURIComponent(confirmation.revision.id)}/restore`,
				{ method: "POST" },
			);
		} else {
			if (!state?.drift_id) {
				setConfirmError(
					"The outside changes changed again. Close this and review them once more.",
				);
				return;
			}
			done = await mutate("accept-drift", "/api/workspace/drift/accept", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					summary: driftSummary.trim(),
					drift_id: state.drift_id,
				}),
			});
			if (done) setDriftSummary("");
		}
		if (done) setConfirmation(null);
	}

	async function openReleaseDetail(slug: string) {
		if (openRelease?.slug === slug) {
			setOpenRelease(null);
			return;
		}
		setLoadingRelease(slug);
		setError(null);
		try {
			const response = await fetch(`/api/releases/${encodeURIComponent(slug)}`);
			if (!response.ok) throw new Error(await responseError(response));
			setOpenRelease((await response.json()) as CourseRelease);
		} catch (caught) {
			setError(errorMessage(caught, "The release could not be opened."));
		} finally {
			setLoadingRelease(null);
		}
	}

	const driftChanges = state
		? state.drift === "unknown"
			? state.changes
			: state.drift_changes
		: [];

	return (
		<div className="canvas-page" aria-busy={loading || Boolean(mutating)}>
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title">History</h1>
					<p className="meta">
						Review what changed, save checkpoints, and return to earlier
						versions.
					</p>
				</div>
			</header>

			<Tabs<HistoryTab>
				label="History"
				value={tab}
				onChange={setTab}
				tabs={[
					{
						id: "changes",
						label: "Current changes",
						count: state?.changes.length ?? 0,
					},
					{
						id: "revisions",
						label: "Course Revisions",
						count: revisions.length,
					},
					{ id: "releases", label: "Releases", count: releases.length },
				]}
			/>

			<div className="history-body">
				{error ? <Notice tone="error">{error}</Notice> : null}
				{loading ? (
					<p className="meta" role="status">
						Reading history…
					</p>
				) : null}

				{tab === "changes" && !loading ? (
					notInitialized ? (
						<div className="canvas-empty">
							<HistoryIcon aria-hidden="true" />
							<h2>History starts once the course has a Course Plan.</h2>
						</div>
					) : state ? (
						<>
							{unresolvedDrift ? (
								<section
									className="history-drift panel panel-pad"
									aria-labelledby="history-drift-heading"
								>
									<div className="history-drift-head">
										<AlertTriangle aria-hidden="true" />
										<h2 id="history-drift-heading">
											{state.drift === "unknown"
												? "This course has no recorded history yet"
												: interruptedRun
													? "The Course Agent stopped before it finished"
													: "Course files changed outside the app"}
										</h2>
									</div>
									<p>
										{state.drift === "unknown"
											? "Review its files, then accept them as the starting point of this course's history."
											: state.validation.valid
												? "The changes are readable. Accept them to record them as a Course Revision."
												: "The changes leave the course in a state the app cannot read. Ask the Course Agent to propose a fix for you to approve."}
									</p>
									{driftChanges.length > 0 ? (
										<ul className="history-drift-items">
											{driftChanges.map((file) => (
												<li key={file.path}>
													<span className="history-status">
														{statusWord[file.status]}
													</span>
													{describePath(file.path, course, presentations).name}
												</li>
											))}
										</ul>
									) : null}
									{state.validation.valid ? (
										<form
											className="history-inline-form"
											onSubmit={(event) => {
												event.preventDefault();
												setConfirmError(null);
												setConfirmation({ kind: "accept-drift" });
											}}
										>
											<div className="field">
												<label htmlFor="drift-summary">
													Describe {theChanges}
												</label>
												<input
													id="drift-summary"
													value={driftSummary}
													maxLength={240}
													required
													disabled={Boolean(mutating)}
													onChange={(event) =>
														setDriftSummary(event.target.value)
													}
												/>
											</div>
											<button
												type="submit"
												className="btn btn-primary"
												disabled={
													!driftSummary.trim() ||
													!state.drift_id ||
													Boolean(mutating)
												}
											>
												Accept {changesName}
											</button>
										</form>
									) : (
										<button
											type="button"
											className="btn btn-primary"
											disabled={!state.drift_id || Boolean(mutating)}
											onClick={() => {
												if (state.drift_id) onReconcile(state.drift_id);
											}}
										>
											Fix with the Course Agent
										</button>
									)}
								</section>
							) : null}

							{state.changes.length === 0 ? (
								<div className="canvas-empty">
									<h2>No changes since the last revision.</h2>
								</div>
							) : (
								<>
									{state.validation.valid && !unresolvedDrift ? (
										<section
											className="history-save panel panel-pad"
											aria-labelledby="history-save-heading"
										>
											<h2 id="history-save-heading">Save a Course Revision</h2>
											<p className="meta">
												Your edits are already saved. A revision is a named
												checkpoint you can return to.
											</p>
											<form
												className="history-inline-form"
												onSubmit={createRevision}
											>
												<div className="field">
													<label htmlFor="revision-summary">
														Revision summary
													</label>
													<input
														id="revision-summary"
														value={summary}
														maxLength={240}
														required
														disabled={Boolean(mutating)}
														onChange={(event) => {
															setSummaryTouched(true);
															setSummary(event.target.value);
														}}
													/>
												</div>
												<button
													type="submit"
													className="btn btn-primary"
													disabled={!summary.trim() || Boolean(mutating)}
												>
													{mutating === "create-revision"
														? "Creating…"
														: "Create Course Revision"}
												</button>
											</form>
										</section>
									) : null}

									<section
										className="canvas-section"
										aria-labelledby="history-changes-heading"
									>
										<div className="canvas-section-head">
											<h2 id="history-changes-heading">
												Changed since the last revision
											</h2>
										</div>
										<ul className="row-list">
											{items.map(({ file, item }) => (
												<li className="history-change" key={file.path}>
													<div className="row">
														<ItemIcon kind={item.kind} />
														<div className="row-main">
															<span className="row-title">{item.name}</span>
															<span className="row-meta">
																<span
																	className={`history-status is-${file.status}`}
																>
																	{statusWord[file.status]}
																</span>
															</span>
														</div>
														<div className="row-actions">
															<button
																type="button"
																className="btn btn-quiet btn-small"
																disabled={Boolean(mutating)}
																onClick={() => {
																	setConfirmError(null);
																	setConfirmation({
																		kind: "revert",
																		file,
																		item,
																	});
																}}
															>
																<RotateCcw aria-hidden="true" />
																{file.status === "added"
																	? `Remove ${item.object}`
																	: `Undo changes to ${item.object}`}
															</button>
														</div>
													</div>
													<details className="history-diff">
														<summary>
															<ChevronRight aria-hidden="true" />
															Show file changes
														</summary>
														<p className="meta">
															<span className="mono">{file.path}</span>
															{file.line_count !== null
																? ` · ${file.line_count} lines`
																: ""}
														</p>
														{file.diff_lines.length > 0 ? (
															<pre>
																{file.diff_lines.map((line, index) => (
																	<span
																		// biome-ignore lint/suspicious/noArrayIndexKey: diff lines are positional
																		key={index}
																		className={
																			line.startsWith("+")
																				? "is-add"
																				: line.startsWith("-")
																					? "is-del"
																					: undefined
																		}
																	>
																		{line}
																		{"\n"}
																	</span>
																))}
															</pre>
														) : (
															<p className="meta">No line changes to show.</p>
														)}
														{file.diff_truncated ? (
															<p className="meta">
																Only the first part is shown.
															</p>
														) : null}
													</details>
												</li>
											))}
										</ul>
									</section>
								</>
							)}

							{state.validation.findings.length > 0 ? (
								<section
									className="canvas-section"
									aria-labelledby="history-issues-heading"
								>
									<div className="canvas-section-head">
										<h2 id="history-issues-heading">Issues to review</h2>
									</div>
									<ul className="history-issues">
										{uniqueFindings(state.validation.findings).map(
											(finding) => (
												<li key={finding.key}>
													<AlertTriangle aria-hidden="true" />
													{finding.text}
												</li>
											),
										)}
									</ul>
								</section>
							) : null}
						</>
					) : null
				) : null}

				{tab === "revisions" && !loading ? (
					revisions.length === 0 ? (
						<div className="canvas-empty">
							<h2>No Course Revisions yet</h2>
							<p>
								Save a revision from Current changes to create a checkpoint.
							</p>
						</div>
					) : (
						<ol className="row-list history-revisions">
							{revisions.map((revision, index) => (
								<li className="row" key={revision.id}>
									<span className="history-revision-dot" aria-hidden="true" />
									<div className="row-main">
										<span className="row-title">{revision.summary}</span>
										<span className="row-meta">
											{formatDate(revision.created_at)}
											{index === 0 ? " · Latest" : ""}
										</span>
									</div>
									<div className="row-actions">
										<button
											type="button"
											className="btn btn-small"
											disabled={Boolean(mutating) || revisionsStale}
											onClick={() => {
												setConfirmError(null);
												setConfirmation({ kind: "restore", revision });
											}}
										>
											<RotateCcw aria-hidden="true" />
											Restore
										</button>
									</div>
								</li>
							))}
						</ol>
					)
				) : null}

				{tab === "releases" && !loading ? (
					<>
						<div className="history-release-head">
							<p className="meta">
								Releases are permanent records of what you published.
							</p>
							{course ? (
								<button
									type="button"
									className="btn btn-primary"
									onClick={onPublish}
								>
									<Send aria-hidden="true" />
									Publish release
								</button>
							) : null}
						</div>
						{releases.length === 0 ? (
							<div className="canvas-empty">
								<h2>No releases yet</h2>
							</div>
						) : (
							<ul className="history-release-list">
								{releases.map((release) => {
									const open = openRelease?.slug === release.slug;
									const total =
										release.included_lecture_ids.length +
										release.planned_unpublished_lecture_ids.length;
									return (
										<li key={release.slug}>
											<button
												type="button"
												className="history-release-row"
												aria-expanded={open}
												disabled={loadingRelease === release.slug}
												onClick={() => void openReleaseDetail(release.slug)}
											>
												<span className="row-main">
													<span className="row-title">{release.name}</span>
													<span className="row-meta">
														<span className="mono">{release.slug}</span>
														<span>{formatDate(release.published_at)}</span>
														<span>
															{release.included_lecture_ids.length} of {total}{" "}
															Lectures
														</span>
													</span>
												</span>
												<ChevronRight aria-hidden="true" />
											</button>
											{open && openRelease ? (
												<ReleaseDetail release={openRelease} course={course} />
											) : null}
										</li>
									);
								})}
							</ul>
						)}
					</>
				) : null}
			</div>

			{confirmation ? (
				<ConfirmDialog
					title={
						confirmation.kind === "revert"
							? confirmation.file.status === "added"
								? `Remove ${confirmation.item.object}?`
								: `Undo changes to ${confirmation.item.object}?`
							: confirmation.kind === "restore"
								? "Restore this Course Revision?"
								: `Accept ${changesName}?`
					}
					confirmLabel={
						confirmation.kind === "revert"
							? confirmation.file.status === "added"
								? "Remove"
								: "Undo changes"
							: confirmation.kind === "restore"
								? "Restore revision"
								: "Accept changes"
					}
					danger={confirmation.kind !== "accept-drift"}
					busy={Boolean(mutating)}
					error={confirmError}
					onCancel={() => setConfirmation(null)}
					onConfirm={() => void confirm()}
				>
					{confirmation.kind === "revert" ? (
						<p>
							{confirmation.file.status === "added"
								? `This removes ${confirmation.item.object}, which was added since the last revision.`
								: `This restores ${confirmation.item.object} to the last saved revision. Other changes stay as they are.`}
						</p>
					) : confirmation.kind === "restore" ? (
						<p>
							The course returns to “{confirmation.revision.summary}” (
							{formatDate(confirmation.revision.created_at)}). Changes made
							since the last revision are replaced, and the restore is recorded
							in History.
						</p>
					) : (
						<p>
							This records {theChanges} as a Course Revision named “
							{driftSummary.trim()}”.
						</p>
					)}
				</ConfirmDialog>
			) : null}
		</div>
	);
}
