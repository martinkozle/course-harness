import {
	type FormEvent,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";

import { responseError } from "./api";
import type { CourseRevision, CurrentState } from "./models";

type CurrentStateViewProps = {
	workspaceName: string;
	onReconcileWithAgent: (driftId: string) => void;
};

function fileStatusLabel(
	status: CurrentState["changes"][number]["status"],
): string {
	return { added: "Added", modified: "Modified", deleted: "Removed" }[status];
}

function findingEntries(findings: string[]): { key: string; text: string }[] {
	const occurrences = new Map<string, number>();
	return findings.map((text) => {
		const occurrence = occurrences.get(text) ?? 0;
		occurrences.set(text, occurrence + 1);
		return { key: `${text}-${occurrence}`, text };
	});
}

export function CurrentStateView({
	workspaceName,
	onReconcileWithAgent,
}: CurrentStateViewProps) {
	const [state, setState] = useState<CurrentState | null>(null);
	const [revisions, setRevisions] = useState<CourseRevision[]>([]);
	const [loading, setLoading] = useState(true);
	const [refreshing, setRefreshing] = useState(false);
	const [notInitialized, setNotInitialized] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const requestRef = useRef<AbortController | null>(null);
	const requestGenerationRef = useRef(0);
	const confirmationRef = useRef<HTMLButtonElement | null>(null);
	const confirmationTriggerRef = useRef<HTMLButtonElement | null>(null);
	const refreshButtonRef = useRef<HTMLButtonElement | null>(null);
	const [confirmPath, setConfirmPath] = useState<string | null>(null);
	const [confirmRevision, setConfirmRevision] = useState<string | null>(null);
	const [confirmDriftAcceptance, setConfirmDriftAcceptance] = useState(false);
	const [mutating, setMutating] = useState<string | null>(null);
	const [summary, setSummary] = useState("");
	const [driftSummary, setDriftSummary] = useState("");
	const [revisionsStale, setRevisionsStale] = useState(false);

	const loadState = useCallback(
		async (signal: AbortSignal, generation: number) => {
			try {
				const response = await fetch("/api/workspace/current-state", {
					signal,
				});
				if (generation !== requestGenerationRef.current) return;
				if (response.status === 404) {
					setState(null);
					setNotInitialized(true);
					return;
				}
				if (!response.ok) throw new Error(await responseError(response));
				const payload = (await response.json()) as CurrentState;
				if (generation !== requestGenerationRef.current) return;
				setNotInitialized(false);
				setState(payload);
			} catch (caught) {
				if (
					generation === requestGenerationRef.current &&
					!(caught instanceof DOMException && caught.name === "AbortError")
				) {
					setNotInitialized(false);
					setError(
						caught instanceof Error
							? caught.message
							: "Current State could not be read.",
					);
				}
			}
		},
		[],
	);

	const loadRevisions = useCallback(
		async (signal: AbortSignal, generation: number) => {
			try {
				const response = await fetch("/api/workspace/revisions", { signal });
				if (generation !== requestGenerationRef.current) return;
				if (response.status === 404) {
					setRevisions([]);
					setRevisionsStale(false);
					return;
				}
				if (!response.ok) throw new Error(await responseError(response));
				const payload = (await response.json()) as CourseRevision[];
				if (generation !== requestGenerationRef.current) return;
				setRevisions(payload);
				setRevisionsStale(false);
			} catch (caught) {
				if (
					generation === requestGenerationRef.current &&
					!(caught instanceof DOMException && caught.name === "AbortError")
				) {
					setRevisionsStale(true);
					setError(
						caught instanceof Error
							? caught.message
							: "Course Revisions could not be read.",
					);
				}
			}
		},
		[],
	);

	const loadData = useCallback(
		async (signal: AbortSignal, generation: number) => {
			setError(null);
			try {
				await Promise.all([
					loadState(signal, generation),
					loadRevisions(signal, generation),
				]);
			} finally {
				if (!signal.aborted && generation === requestGenerationRef.current) {
					setLoading(false);
					setRefreshing(false);
				}
			}
		},
		[loadRevisions, loadState],
	);

	useEffect(() => {
		const controller = new AbortController();
		const generation = ++requestGenerationRef.current;
		requestRef.current = controller;
		void loadData(controller.signal, generation);
		return () => {
			requestGenerationRef.current += 1;
			requestRef.current?.abort();
			requestRef.current = null;
		};
	}, [loadData]);

	useEffect(() => {
		if (confirmPath || confirmRevision || confirmDriftAcceptance) {
			confirmationRef.current?.focus();
		} else if (confirmationTriggerRef.current) {
			const focusTarget = confirmationTriggerRef.current.isConnected
				? confirmationTriggerRef.current
				: refreshButtonRef.current;
			focusTarget?.focus();
			confirmationTriggerRef.current = null;
		}
	}, [confirmDriftAcceptance, confirmPath, confirmRevision]);

	function refresh() {
		if (mutating) return;
		requestRef.current?.abort();
		const controller = new AbortController();
		const generation = ++requestGenerationRef.current;
		requestRef.current = controller;
		setRefreshing(true);
		void loadData(controller.signal, generation);
	}

	async function mutate(
		key: string,
		url: string,
		options: RequestInit,
	): Promise<void> {
		if (mutating) return;
		requestRef.current?.abort();
		const mutationController = new AbortController();
		const generation = ++requestGenerationRef.current;
		requestRef.current = mutationController;
		setMutating(key);
		setError(null);
		try {
			const response = await fetch(url, {
				...options,
				signal: mutationController.signal,
			});
			if (generation !== requestGenerationRef.current) return;
			if (!response.ok) throw new Error(await responseError(response));
			if (key === "create-revision") setSummary("");
			if (key === "accept-drift") setDriftSummary("");
			setRefreshing(true);
			await loadData(mutationController.signal, generation);
			if (
				mutationController.signal.aborted ||
				generation !== requestGenerationRef.current
			)
				return;
			setConfirmPath(null);
			setConfirmRevision(null);
			setConfirmDriftAcceptance(false);
		} catch (caught) {
			if (
				generation === requestGenerationRef.current &&
				!(caught instanceof DOMException && caught.name === "AbortError")
			) {
				setError(
					caught instanceof Error
						? caught.message
						: "The change could not be applied.",
				);
			}
		} finally {
			if (
				!mutationController.signal.aborted &&
				generation === requestGenerationRef.current
			)
				setMutating(null);
		}
	}

	function createRevision(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		void mutate("create-revision", "/api/workspace/revisions", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ summary: summary.trim() }),
		});
	}

	function acceptDrift(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		if (!state?.drift_id) {
			setError(
				"Workspace Drift changed; refresh Current State before accepting it.",
			);
			return;
		}
		if (!confirmDriftAcceptance) {
			setConfirmPath(null);
			setConfirmRevision(null);
			setConfirmDriftAcceptance(true);
			return;
		}
		void mutate("accept-drift", "/api/workspace/drift/accept", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				summary: driftSummary.trim(),
				drift_id: state.drift_id,
			}),
		});
	}

	const hasUnresolvedDrift =
		state !== null && (state.drift === "drift" || state.drift === "unknown");

	return (
		<main
			className="page-main current-state-main"
			aria-labelledby="current-state-heading"
			aria-busy={loading || refreshing || Boolean(mutating)}
		>
			<header className="page-heading current-state-heading">
				<p className="eyebrow">Course Workspace</p>
				<div className="page-heading-row">
					<div>
						<h1 id="current-state-heading">Current State</h1>
						<p className="lede">
							See what changed in {workspaceName} and whether the Course is
							structurally ready for a Course Revision.
						</p>
					</div>
					<button
						ref={refreshButtonRef}
						className="secondary-action compact-action"
						type="button"
						onClick={refresh}
						disabled={loading || refreshing || Boolean(mutating)}
					>
						{refreshing ? "Refreshing…" : "Refresh"}
					</button>
				</div>
			</header>
			<p className="current-state-status" role="status" aria-live="polite">
				{loading
					? "Reading Current State…"
					: mutating
						? "Applying Course history change…"
						: refreshing
							? "Refreshing Current State…"
							: error
								? "Current State may be outdated."
								: notInitialized
									? "Course Revision history is not initialized."
									: "Current State is up to date."}
			</p>
			{error ? (
				<p className="notice error-notice" role="alert">
					{error}
				</p>
			) : null}
			{notInitialized ? (
				<section
					className="empty-state current-state-empty"
					aria-label="Current State not initialized"
				>
					<p className="section-kicker">Course Revisions</p>
					<h2>History starts when you create a Course Plan</h2>
					<span>
						Once your Course has a plan, this page will show its working state
						and canonical files.
					</span>
				</section>
			) : state ? (
				<div className="current-state-ledger" aria-busy={refreshing}>
					{(() => {
						const unresolvedDrift =
							state.drift === "drift" || state.drift === "unknown";
						const driftChanges =
							state.drift === "unknown" ? state.changes : state.drift_changes;
						const driftLabel =
							state.drift === "unknown"
								? "Unknown provenance"
								: state.drift === "drift"
									? "Workspace Drift detected"
									: "No Workspace Drift";
						return (
							<section
								className={`current-state-drift drift-${state.drift}${
									unresolvedDrift ? " is-unresolved" : ""
								}`}
								aria-labelledby="workspace-drift-heading"
							>
								<div className="current-state-drift-heading">
									<div>
										<p className="section-kicker">Workspace Drift</p>
										<h2 id="workspace-drift-heading">{driftLabel}</h2>
									</div>
									<span className="drift-count-badge">
										{driftChanges.length} path
										{driftChanges.length === 1 ? "" : "s"}
									</span>
								</div>
								{state.drift === "unknown" ? (
									<p>
										This Workspace has no recorded provenance yet. Review its
										canonical state before accepting it into Course history.
									</p>
								) : state.drift === "clean" ? (
									<p>
										Canonical files still match the last app-authored state.
									</p>
								) : state.validation.valid ? (
									<p>
										These external canonical-file changes are structurally
										valid. Accept them with a meaningful Course Revision
										summary.
									</p>
								) : (
									<p>
										These external changes need a reviewed Reconciliation before
										they can become valid Course state.
									</p>
								)}
								{unresolvedDrift && driftChanges.length > 0 ? (
									<ul
										className="workspace-drift-paths"
										aria-label="Workspace Drift paths"
									>
										{driftChanges.map((file) => (
											<li key={file.path}>
												<span>{fileStatusLabel(file.status)}</span>
												<code>{file.path}</code>
											</li>
										))}
									</ul>
								) : null}
								{unresolvedDrift && state.validation.valid ? (
									<form
										className="workspace-drift-accept"
										onSubmit={acceptDrift}
									>
										<label htmlFor="drift-summary">
											Workspace Drift summary
										</label>
										<input
											id="drift-summary"
											value={driftSummary}
											onChange={(event) => setDriftSummary(event.target.value)}
											maxLength={240}
											required
											aria-describedby="drift-summary-help"
											disabled={Boolean(mutating)}
										/>
										<small id="drift-summary-help">
											Describe the external change you are accepting. This
											creates a visible Course Revision.
										</small>
										{confirmDriftAcceptance ? (
											<span className="current-state-confirmation">
												<span>
													Create this Course Revision from Workspace Drift?
												</span>
												<button
													type="submit"
													ref={confirmationRef}
													disabled={Boolean(mutating)}
												>
													Confirm acceptance
												</button>
												<button
													type="button"
													disabled={Boolean(mutating)}
													onClick={() => setConfirmDriftAcceptance(false)}
												>
													Cancel
												</button>
											</span>
										) : (
											<button
												className="primary-action compact-action"
												type="submit"
												disabled={
													!driftSummary.trim() ||
													!state.drift_id ||
													Boolean(mutating)
												}
												onClick={(event) => {
													confirmationTriggerRef.current = event.currentTarget;
												}}
											>
												Accept Workspace Drift
											</button>
										)}
									</form>
								) : unresolvedDrift ? (
									<button
										className="secondary-action compact-action"
										type="button"
										disabled={Boolean(mutating) || !state.drift_id}
										onClick={() => {
											if (state.drift_id) onReconcileWithAgent(state.drift_id);
										}}
									>
										Reconcile with Course Agent
									</button>
								) : null}
							</section>
						);
					})()}
					<section
						className="current-state-summary"
						aria-labelledby="current-state-summary-heading"
					>
						<h2 id="current-state-summary-heading">State summary</h2>
						<div className="current-state-summary-grid">
							<div>
								<span className="ledger-label">Working state</span>
								<strong>
									{state.clean
										? "Clean"
										: `${state.changes.length} change${state.changes.length === 1 ? "" : "s"}`}
								</strong>
								<small>
									{state.clean
										? "No uncommitted changes"
										: "Changes are present in the Course folder"}
								</small>
							</div>
							<div>
								<span className="ledger-label">Structure</span>
								<strong>
									{state.validation.valid ? "Valid" : "Needs review"}
								</strong>
								<small>
									{state.validation.valid
										? "Course state can be read safely"
										: "Review the findings below before making a Revision"}
								</small>
							</div>
						</div>
					</section>
					<section
						className="current-state-revision-form"
						aria-labelledby="create-revision-heading"
					>
						<h2 id="create-revision-heading">Create a Course Revision</h2>
						<p>
							{hasUnresolvedDrift
								? "Accept the Workspace Drift above before creating another Course Revision."
								: "Save these valid changes with a short summary you will recognize later."}
						</p>
						<form
							onSubmit={createRevision}
							aria-busy={mutating === "create-revision"}
						>
							<label htmlFor="revision-summary">Revision summary</label>
							<input
								id="revision-summary"
								value={summary}
								onChange={(event) => setSummary(event.target.value)}
								maxLength={240}
								required
								disabled={
									state.clean ||
									!state.validation.valid ||
									hasUnresolvedDrift ||
									Boolean(mutating)
								}
							/>
							<small>
								Use up to 240 characters. A meaningful summary makes history
								easier to scan.
							</small>
							<button
								className="primary-action compact-action"
								type="submit"
								disabled={
									state.clean ||
									!state.validation.valid ||
									hasUnresolvedDrift ||
									!summary.trim() ||
									Boolean(mutating)
								}
							>
								{mutating === "create-revision"
									? "Creating…"
									: "Create Course Revision"}
							</button>
						</form>
					</section>
					<section
						className="current-state-files"
						aria-labelledby="current-state-files-heading"
					>
						<div className="content-section-heading">
							<div>
								<p className="section-kicker">Canonical files</p>
								<h2 id="current-state-files-heading">Changes in this Course</h2>
							</div>
							<span className="count-badge">{state.changes.length}</span>
						</div>
						{state.changes.length === 0 ? (
							<p className="empty-note">
								The working state is clean. There are no canonical file changes
								to review.
							</p>
						) : (
							<ul className="current-state-file-list">
								{state.changes.map((file) => (
									<li key={file.path}>
										<span
											className={`current-state-marker status-${file.status}`}
											aria-hidden="true"
										/>
										<div>
											<strong>{fileStatusLabel(file.status)}</strong>
											<span className="current-state-path">{file.path}</span>
											{file.diff_lines.length > 0 ? (
												<details className="current-state-diff">
													<summary>Review changes</summary>
													<pre>{file.diff_lines.join("\n")}</pre>
													{file.diff_truncated ? (
														<small>Diff preview is truncated.</small>
													) : null}
												</details>
											) : null}
											{confirmPath === file.path ? (
												<span className="current-state-confirmation">
													<span>
														{file.status === "added"
															? "Remove this newly added file from Current State?"
															: "Revert this file to its last saved state?"}
													</span>
													<button
														type="button"
														ref={confirmationRef}
														disabled={Boolean(mutating)}
														onClick={() =>
															void mutate(
																`revert:${file.path}`,
																"/api/workspace/current-state/revert",
																{
																	method: "POST",
																	headers: {
																		"Content-Type": "application/json",
																	},
																	body: JSON.stringify({ path: file.path }),
																},
															)
														}
													>
														Confirm revert
													</button>
													<button
														type="button"
														disabled={Boolean(mutating)}
														onClick={() => setConfirmPath(null)}
													>
														Cancel
													</button>
												</span>
											) : null}
										</div>
										<small>
											{file.line_count === null
												? "Line count unavailable"
												: `${file.line_count} total line${file.line_count === 1 ? "" : "s"}`}
										</small>
										{confirmPath !== file.path ? (
											<button
												className="quiet-action"
												type="button"
												disabled={Boolean(mutating)}
												onClick={(event) => {
													confirmationTriggerRef.current = event.currentTarget;
													setConfirmRevision(null);
													setConfirmPath(file.path);
												}}
											>
												Revert
											</button>
										) : null}
									</li>
								))}
							</ul>
						)}
					</section>
					<section
						className="current-state-revisions"
						aria-labelledby="revisions-heading"
					>
						<div className="content-section-heading">
							<div>
								<p className="section-kicker">Course history</p>
								<h2 id="revisions-heading">Course Revisions</h2>
							</div>
							<span className="count-badge">{revisions.length}</span>
						</div>
						{revisions.length === 0 ? (
							<p className="empty-note">No Course Revisions yet.</p>
						) : (
							<ol className="revision-list">
								{revisions.map((revision) => (
									<li key={revision.id}>
										<div>
											<strong>{revision.summary}</strong>
											<small>
												{new Date(revision.created_at).toLocaleString()}
											</small>
										</div>
										{confirmRevision === revision.id ? (
											<span className="current-state-confirmation">
												<span>Restore this Course Revision?</span>
												<button
													type="button"
													ref={confirmationRef}
													disabled={Boolean(mutating) || revisionsStale}
													onClick={() =>
														void mutate(
															`restore:${revision.id}`,
															`/api/workspace/revisions/${encodeURIComponent(revision.id)}/restore`,
															{ method: "POST" },
														)
													}
												>
													Confirm restore
												</button>
												<button
													type="button"
													disabled={Boolean(mutating)}
													onClick={() => setConfirmRevision(null)}
												>
													Cancel
												</button>
											</span>
										) : (
											<button
												className="quiet-action"
												type="button"
												disabled={Boolean(mutating) || revisionsStale}
												onClick={(event) => {
													confirmationTriggerRef.current = event.currentTarget;
													setConfirmPath(null);
													setConfirmRevision(revision.id);
												}}
											>
												Restore
											</button>
										)}
									</li>
								))}
							</ol>
						)}
					</section>
					{state.validation.findings.length > 0 ? (
						<section
							className="current-state-findings"
							aria-labelledby="current-state-findings-heading"
						>
							<p className="section-kicker">Structure review</p>
							<h2 id="current-state-findings-heading">Findings to review</h2>
							<ul>
								{findingEntries(state.validation.findings).map((finding) => (
									<li key={finding.key}>{finding.text}</li>
								))}
							</ul>
						</section>
					) : null}
				</div>
			) : null}
		</main>
	);
}
