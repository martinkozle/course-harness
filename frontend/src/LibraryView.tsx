import { useEffect, useRef, useState } from "react";
import { responseError } from "./api";
import type {
	Candidate,
	DiscoveryResult,
	EvidenceTarget,
	GroupedSearchResult,
	ResourceState,
	SearchResult,
	Source,
} from "./models";

type LibraryViewProps = {
	resources: ResourceState[];
	sources: Source[];
	onResourcesChange: (resources: ResourceState[]) => void;
	onSourcesChange: (sources: Source[]) => void;
	evidenceTarget: EvidenceTarget | null;
	onEvidenceTargetClose: () => void;
};

type ModelDownloadStatus = {
	ready: boolean;
	downloading: boolean;
	stage: string | null;
	completed_steps: number;
	total_steps: number;
	error: string | null;
};

type PendingModelAction =
	| { kind: "upload"; file: File }
	| { kind: "reprocess"; resourceId: string }
	| { kind: "process"; resourceId: string }
	| { kind: "manual" };

function formatBytes(bytes: number): string {
	if (bytes === 0) return "0 B";
	const units = ["B", "KB", "MB"];
	const index = Math.min(
		Math.floor(Math.log(bytes) / Math.log(1024)),
		units.length - 1,
	);
	return `${(bytes / 1024 ** index).toFixed(1)} ${units[index]}`;
}

function statusBadge(status: string): string {
	const lookup: Record<string, string> = {
		unprocessed: "Awaiting processing",
		processing: "Processing…",
		ready: "Ready",
		failed: "Failed",
		retrying: "Retrying",
	};
	return lookup[status] ?? status;
}

export function LibraryView({
	resources,
	sources,
	onResourcesChange,
	onSourcesChange,
	evidenceTarget,
	onEvidenceTargetClose,
}: LibraryViewProps) {
	const [uploading, setUploading] = useState(false);
	const [pendingModelAction, setPendingModelAction] =
		useState<PendingModelAction | null>(null);
	const [modelStatus, setModelStatus] = useState<ModelDownloadStatus | null>(null);
	const [downloadBusy, setDownloadBusy] = useState(false);
	const [modelError, setModelError] = useState<string | null>(null);
	const [uploadError, setUploadError] = useState<string | null>(null);
	const modelDialog = useRef<HTMLDialogElement>(null);
	const [processing, setProcessing] = useState<Set<string>>(new Set());
	const [reprocessing, setReprocessing] = useState<Set<string>>(new Set());
	const [reprocessErrors, setReprocessErrors] = useState<Map<string, string>>(
		new Map(),
	);
	const [admitting, setAdmitting] = useState<Set<string>>(new Set());
	const [regenerating, setRegenerating] = useState(false);
	const [searchQuery, setSearchQuery] = useState("");
	const [searchResults, setSearchResults] = useState<GroupedSearchResult[]>([]);
	const [searching, setSearching] = useState(false);
	const [hasSearched, setHasSearched] = useState(false);
	const [lastSubmittedQuery, setLastSubmittedQuery] = useState("");
	const [viewingSource, setViewingSource] = useState<string | null>(null);
	const [sourceContent, setSourceContent] = useState("");
	const [loadingContent, setLoadingContent] = useState(false);
	const [supportingEvidenceTarget, setSupportingEvidenceTarget] =
		useState<EvidenceTarget | null>(null);
	const [supportingEvidenceContent, setSupportingEvidenceContent] =
		useState("");
	const [supportingEvidenceError, setSupportingEvidenceError] = useState<
		string | null
	>(null);
	const [loadingSupportingEvidence, setLoadingSupportingEvidence] =
		useState(false);
	const supportingEvidenceHeadingRef = useRef<HTMLHeadingElement | null>(null);

	useEffect(() => {
		if (!evidenceTarget?.source_id) {
			setSupportingEvidenceTarget(null);
			setSupportingEvidenceContent("");
			setSupportingEvidenceError(null);
			setLoadingSupportingEvidence(false);
			return;
		}

		const controller = new AbortController();
		setSupportingEvidenceTarget(evidenceTarget);
		setSupportingEvidenceContent("");
		setSupportingEvidenceError(null);
		setLoadingSupportingEvidence(true);
		const params = new URLSearchParams({ max_chars: "8000" });
		if (evidenceTarget.line_start !== null) {
			params.set("line_start", String(evidenceTarget.line_start));
		}
		if (evidenceTarget.line_end !== null) {
			params.set("line_end", String(evidenceTarget.line_end));
		}

		void fetch(
			`/api/sources/${encodeURIComponent(evidenceTarget.source_id)}/content?${params.toString()}`,
			{ signal: controller.signal },
		)
			.then(async (response) => {
				if (!response.ok) throw new Error(await responseError(response));
				setSupportingEvidenceContent(await response.text());
			})
			.catch((caught) => {
				if (!(caught instanceof DOMException && caught.name === "AbortError")) {
					setSupportingEvidenceError(
						caught instanceof Error
							? caught.message
							: "Supporting Evidence could not be loaded.",
					);
				}
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoadingSupportingEvidence(false);
			});

		return () => controller.abort();
	}, [evidenceTarget]);

	useEffect(() => {
		if (supportingEvidenceTarget) {
			supportingEvidenceHeadingRef.current?.focus();
		}
	}, [supportingEvidenceTarget]);

	const [discoveryQuery, setDiscoveryQuery] = useState("");
	const [discovering, setDiscovering] = useState(false);
	const [discoveryResults, setDiscoveryResults] = useState<DiscoveryResult[]>(
		[],
	);
	const [addingRemote, setAddingRemote] = useState<Set<string>>(new Set());
	const [refreshing, setRefreshing] = useState<Set<string>>(new Set());
	const [adopting, setAdopting] = useState<Set<string>>(new Set());
	const [removing, setRemoving] = useState<Set<string>>(new Set());

	const sourceByResource = Object.fromEntries(
		sources.map((s) => [s.resource_id, s]),
	);

	const statusCounts = resources.reduce(
		(counts, item) => {
			counts[item.status] = (counts[item.status] ?? 0) + 1;
			return counts;
		},
		{} as Record<string, number>,
	);

	useEffect(() => {
		if (pendingModelAction && !modelDialog.current?.open)
			modelDialog.current?.showModal();
		if (!pendingModelAction && modelDialog.current?.open)
			modelDialog.current.close();
	}, [pendingModelAction]);

	useEffect(() => {
		let active = true;
		async function refresh() {
			try {
				const response = await fetch("/api/resources/parser-models");
				if (!response.ok) return;
				const status = (await response.json()) as ModelDownloadStatus;
				if (active) setModelStatus(status);
			} catch {
				// The manual action reports a request error when selected.
			}
		}
		void refresh();
		if (downloadBusy) {
			const timer = window.setInterval(() => void refresh(), 500);
			return () => {
				active = false;
				window.clearInterval(timer);
			};
		}
		return () => {
			active = false;
		};
	}, [downloadBusy]);

	async function uploadFile(file: File) {
		setUploading(true);
		setUploadError(null);
		try {
			const formData = new FormData();
			formData.append("file", file);
			const response = await fetch("/api/resources/upload", {
				method: "POST",
				body: formData,
			});
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} catch (caught) {
			setUploadError(caught instanceof Error ? caught.message : "Upload failed.");
		} finally {
			setUploading(false);
		}
	}

	async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		event.target.value = "";
		if (!file) return;
		if (file.name.toLowerCase().endsWith(".pdf")) {
			try {
				const response = await fetch("/api/resources/parser-models");
				if (!response.ok) throw new Error(await responseError(response));
				const status = (await response.json()) as ModelDownloadStatus;
				setModelStatus(status);
				if (!status.ready) {
					setModelError(null);
					setPendingModelAction({ kind: "upload", file });
					return;
				}
			} catch (caught) {
				setUploadError(
					caught instanceof Error ? caught.message : "Parser status could not be checked.",
				);
				return;
			}
		}
		await uploadFile(file);
	}

	async function confirmModelDownload() {
		if (!pendingModelAction) return;
		const action = pendingModelAction;
		setDownloadBusy(true);
		setModelError(null);
		try {
			const response = await fetch("/api/resources/parser-models", { method: "POST" });
			if (!response.ok) throw new Error(await responseError(response));
			setModelStatus((await response.json()) as ModelDownloadStatus);
			setPendingModelAction(null);
			if (action.kind === "upload") await uploadFile(action.file);
			if (action.kind === "reprocess") await handleReprocess(action.resourceId);
			if (action.kind === "process") await handleProcess(action.resourceId);
		} catch (caught) {
			setModelError(
				caught instanceof Error ? caught.message : "Models could not be downloaded.",
			);
		} finally {
			setDownloadBusy(false);
		}
	}

	async function handleProcess(resourceId: string) {
		setProcessing((current) => new Set(current).add(resourceId));
		try {
			const response = await fetch(
				`/api/resources/${encodeURIComponent(resourceId)}/process`,
				{ method: "POST" },
			);
			if (response.status === 409) {
				setModelError(null);
				setPendingModelAction({ kind: "process", resourceId });
				return;
			}
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} finally {
			setProcessing((current) => {
				const next = new Set(current);
				next.delete(resourceId);
				return next;
			});
		}
	}

	async function handleReprocess(resourceId: string) {
		setReprocessing((current) => new Set(current).add(resourceId));
		setReprocessErrors((current) => {
			const next = new Map(current);
			next.delete(resourceId);
			return next;
		});
		try {
			const response = await fetch(
				`/api/resources/${encodeURIComponent(resourceId)}/reprocess`,
				{ method: "POST" },
			);
			if (response.status === 409) {
				setModelError(null);
				setPendingModelAction({ kind: "reprocess", resourceId });
				return;
			}
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} catch (error) {
			const message =
				error instanceof Error ? error.message : "Reprocessing failed.";
			setReprocessErrors((current) =>
				new Map(current).set(resourceId, message),
			);
		} finally {
			setReprocessing((current) => {
				const next = new Set(current);
				next.delete(resourceId);
				return next;
			});
		}
	}

	async function handleAdmit(resourceId: string) {
		const resource = resources.find((r) => r.resource_id === resourceId);
		if (!resource) return;
		const label = (resource.location ?? resourceId).slice(0, 200);
		setAdmitting((current) => new Set(current).add(resourceId));
		try {
			const response = await fetch("/api/sources", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ resource_id: resourceId, label }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/sources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onSourcesChange((await updated.json()) as Source[]);
		} finally {
			setAdmitting((current) => {
				const next = new Set(current);
				next.delete(resourceId);
				return next;
			});
		}
	}

	async function handleRemoveSource(sourceId: string) {
		try {
			const response = await fetch(
				`/api/sources/${encodeURIComponent(sourceId)}`,
				{ method: "DELETE" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/sources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onSourcesChange((await updated.json()) as Source[]);
		} catch {
			// Source removal failure is non-blocking
		}
	}

	async function handleRegenerateIndex() {
		setRegenerating(true);
		try {
			await fetch("/api/resources/cache", { method: "DELETE" });
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} catch {
			// Regeneration failure is non-blocking
		} finally {
			setRegenerating(false);
		}
	}

	async function handleDiscoverySearch(
		event: React.FormEvent<HTMLFormElement>,
	) {
		event.preventDefault();
		const q = discoveryQuery.trim();
		if (!q) return;
		setDiscovering(true);
		setDiscoveryResults([]);
		try {
			const response = await fetch("/api/discovery/search", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ query: q }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			setDiscoveryResults((await response.json()) as DiscoveryResult[]);
		} catch {
			setDiscoveryResults([]);
		} finally {
			setDiscovering(false);
		}
	}

	async function handleAddRemote(candidate: Candidate) {
		setAddingRemote((current) => new Set(current).add(candidate.url));
		try {
			const response = await fetch("/api/resources/remote", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ url: candidate.url }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} finally {
			setAddingRemote((current) => {
				const next = new Set(current);
				next.delete(candidate.url);
				return next;
			});
		}
	}

	async function handleRefresh(resourceId: string) {
		setRefreshing((current) => new Set(current).add(resourceId));
		try {
			const response = await fetch(
				`/api/resources/${encodeURIComponent(resourceId)}/refresh`,
				{ method: "POST" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} finally {
			setRefreshing((current) => {
				const next = new Set(current);
				next.delete(resourceId);
				return next;
			});
		}
	}

	async function handleAdoptVersion(sourceId: string) {
		setAdopting((current) => new Set(current).add(sourceId));
		try {
			const response = await fetch(
				`/api/sources/${encodeURIComponent(sourceId)}/adopt-version`,
				{ method: "POST" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/sources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onSourcesChange((await updated.json()) as Source[]);
		} finally {
			setAdopting((current) => {
				const next = new Set(current);
				next.delete(sourceId);
				return next;
			});
		}
	}

	async function handleRemoveResource(resourceId: string) {
		setRemoving((current) => new Set(current).add(resourceId));
		try {
			const response = await fetch(
				`/api/resources/${encodeURIComponent(resourceId)}`,
				{ method: "DELETE" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			const updated = await fetch("/api/resources");
			if (!updated.ok) throw new Error(await responseError(updated));
			onResourcesChange((await updated.json()) as ResourceState[]);
		} finally {
			setRemoving((current) => {
				const next = new Set(current);
				next.delete(resourceId);
				return next;
			});
		}
	}

	async function handleSearch(event: React.FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const q = searchQuery.trim();
		if (!q) {
			setSearchResults([]);
			setHasSearched(false);
			return;
		}
		setSearching(true);
		setHasSearched(true);
		setLastSubmittedQuery(q);
		try {
			const response = await fetch("/api/sources/search", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ query: q, limit: 20 }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			setSearchResults((await response.json()) as GroupedSearchResult[]);
		} catch {
			setSearchResults([]);
		} finally {
			setSearching(false);
		}
	}

	async function handleViewSource(sourceId: string) {
		if (viewingSource === sourceId) {
			setViewingSource(null);
			setSourceContent("");
			return;
		}
		setViewingSource(sourceId);
		setLoadingContent(true);
		setSourceContent("");
		try {
			const response = await fetch(
				`/api/sources/${encodeURIComponent(sourceId)}/content?max_chars=8000`,
			);
			if (!response.ok) throw new Error(await responseError(response));
			setSourceContent(await response.text());
		} catch {
			setSourceContent("Could not load source content.");
		} finally {
			setLoadingContent(false);
		}
	}

	function closeSupportingEvidence() {
		setSupportingEvidenceTarget(null);
		setSupportingEvidenceContent("");
		setSupportingEvidenceError(null);
		setLoadingSupportingEvidence(false);
		onEvidenceTargetClose();
	}

	const supportingEvidenceSource = supportingEvidenceTarget
		? sources.find((source) => source.id === supportingEvidenceTarget.source_id)
		: null;

	return (
		<main className="page-main library-main" aria-labelledby="library-heading">
			<header className="page-heading library-heading">
				<p className="eyebrow">Library</p>
				<h1 id="library-heading">Resources</h1>
				<p className="lede">
					Files, uploads, and attachments available to the Course Agent for
					research and grounding.
				</p>
		</header>

			<dialog
				ref={modelDialog}
				className="model-download-dialog"
				aria-labelledby="model-download-heading"
				onCancel={(event) => {
					if (downloadBusy) event.preventDefault();
					else setPendingModelAction(null);
				}}
			>
				<h2 id="model-download-heading">Download document processing models?</h2>
				<p>
					PDF processing uses local layout, table, and text recognition models.
					The models use several hundred megabytes of storage. They will be
					downloaded once to this device and kept outside your Course
					Workspace. Your document stays on this device.
				</p>
				{pendingModelAction?.kind === "upload" ? (
					<p>Cancel leaves the file unuploaded. You can select it again later.</p>
				) : pendingModelAction?.kind === "reprocess" ||
					pendingModelAction?.kind === "process" ? (
					<p>Cancel keeps the existing resource unchanged.</p>
				) : pendingModelAction?.kind === "manual" ? (
					<p>After the download, select Reprocess on any existing PDF.</p>
				) : null}
				{downloadBusy ? (
					<div className="model-download-progress" role="status">
						<p>{modelStatus?.stage ?? "Starting model download…"}</p>
						<progress
							aria-label="Model download stages completed"
							max={modelStatus?.total_steps ?? 3}
							value={modelStatus?.completed_steps ?? 0}
						/>
						<p>
							{modelStatus?.completed_steps ?? 0} of {modelStatus?.total_steps ?? 3}{" "}
							stages complete
						</p>
					</div>
				) : null}
				{modelError ? <p role="alert">{modelError}</p> : null}
				<div className="section-actions">
					<button
						type="button"
						onClick={() => setPendingModelAction(null)}
						disabled={downloadBusy}
					>
						Cancel
					</button>
					<button
						type="button"
						className="primary-action"
						onClick={() => void confirmModelDownload()}
						disabled={downloadBusy}
					>
						{downloadBusy
							? "Downloading models…"
							: pendingModelAction?.kind === "upload"
								? "Download and upload"
								: pendingModelAction?.kind === "reprocess" ||
									pendingModelAction?.kind === "process"
									? "Download and process"
									: "Download models"}
					</button>
				</div>
			</dialog>
			{uploading ? (
				<p role="status">Uploading and processing your document…</p>
			) : null}
			{uploadError ? <p role="alert">{uploadError}</p> : null}

			{supportingEvidenceTarget ? (
				<section
					className="supporting-evidence-panel"
					aria-labelledby="supporting-evidence-heading"
					aria-busy={loadingSupportingEvidence}
				>
					<div className="content-section-heading">
						<div>
							<p className="section-kicker">Pinned source passage</p>
							<h2
								id="supporting-evidence-heading"
								ref={supportingEvidenceHeadingRef}
								tabIndex={-1}
							>
								Supporting Evidence
							</h2>
							{supportingEvidenceSource ? (
								<p className="supporting-evidence-source">
									{supportingEvidenceSource.label}
									{supportingEvidenceTarget.line_start !== null
										? ` · Line ${supportingEvidenceTarget.line_start + 1}${
												supportingEvidenceTarget.line_end !== null &&
												supportingEvidenceTarget.line_end !==
													supportingEvidenceTarget.line_start
													? `–${supportingEvidenceTarget.line_end + 1}`
													: ""
											}`
										: ""}
								</p>
							) : null}
						</div>
						<button
							className="quiet-action"
							type="button"
							onClick={closeSupportingEvidence}
						>
							Close
						</button>
					</div>
					<p
						className="supporting-evidence-status"
						role="status"
						aria-live="polite"
					>
						{loadingSupportingEvidence
							? "Loading the pinned source passage…"
							: supportingEvidenceError
								? "Supporting Evidence could not be loaded."
								: "Pinned source passage loaded."}
					</p>
					{supportingEvidenceError ? (
						<p className="library-error" role="alert">
							{supportingEvidenceError}
						</p>
					) : loadingSupportingEvidence ? null : (
						<pre className="source-content-body">
							{supportingEvidenceContent}
						</pre>
					)}
				</section>
			) : null}

			<section
				className="content-section library-section"
				aria-label="Resource library"
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">Global library</p>
						<h2>Registered resources</h2>
					</div>
					<div className="section-actions">
						{modelStatus?.ready ? (
							<span className="status-badge status-ready">PDF models ready</span>
						) : (
							<button
								className="quiet-action"
								type="button"
								onClick={() => {
									setModelError(null);
									setPendingModelAction({ kind: "manual" });
								}}
							>
								Download PDF models
							</button>
						)}
						<button
							className="quiet-action"
							type="button"
							onClick={handleRegenerateIndex}
							disabled={regenerating}
						>
							{regenerating ? "Regenerating…" : "Regenerate search index"}
						</button>
						<label className="primary-action compact-action upload-label">
							Upload file
							<input
								type="file"
								hidden
								onChange={(e) => void handleUpload(e)}
								disabled={uploading}
							/>
						</label>
					</div>
				</div>

				<div className="library-stats" aria-live="polite">
					<span className="count-badge">{resources.length}</span>
					<span className="stats-detail">
						{statusCounts.ready ?? 0} ready
						{statusCounts.unprocessed
							? ` · ${statusCounts.unprocessed} unprocessed`
							: ""}
						{statusCounts.failed ? ` · ${statusCounts.failed} failed` : ""}
						{resources.filter((r) => r.indexed).length > 0
							? ` · ${resources.filter((r) => r.indexed).length} indexed`
							: ""}
						{sources.length > 0 ? ` · ${sources.length} admitted` : ""}
					</span>
				</div>

				{resources.length === 0 && !uploading ? (
					<div className="empty-state">
						<p>No resources registered yet.</p>
						<span>
							Upload a file or register a local path to add material to the
							Library.
						</span>
					</div>
				) : (
					<ul className="library-list">
						{resources.map((resource) => {
							const admitted = sourceByResource[resource.resource_id];
							return (
								<li key={resource.resource_id}>
									<div className="library-meta">
										<strong
											className={`status-badge status-${resource.status}`}
										>
											{statusBadge(resource.status)}
										</strong>
										{resource.status === "ready" && resource.indexed ? (
											<strong className="status-badge status-indexed">
												Indexed
											</strong>
										) : resource.status === "ready" && !resource.indexed ? (
											<strong className="status-badge status-unindexed">
												Not indexed
											</strong>
										) : null}
										{admitted ? (
											<>
												<strong className="status-badge status-ready">
													Admitted
												</strong>
												{resource.snapshot &&
												admitted.source_version_id !==
													resource.snapshot.content_hash ? (
													<>
														<strong className="status-badge status-unindexed">
															Update available
														</strong>
														<button
															className="quiet-action"
															type="button"
															onClick={() =>
																void handleAdoptVersion(admitted.id)
															}
															disabled={adopting.has(admitted.id)}
														>
															{adopting.has(admitted.id)
																? "Adopting…"
																: "Adopt latest"}
														</button>
													</>
												) : null}
												<button
													className="quiet-action"
													type="button"
													onClick={() => void handleRemoveSource(admitted.id)}
												>
													Unadmit
												</button>
											</>
										) : null}
										<span className="library-name">
											{resource.location ?? resource.resource_id}
										</span>
									</div>
									{resource.snapshot ? (
										<p className="library-snapshot">
											{formatBytes(resource.snapshot.byte_count)}
											{" · "}
											{resource.snapshot.content_hash.slice(0, 8)}…
										</p>
									) : null}
									{resource.error ? (
										<p className="library-error" role="alert">
											{resource.error}
										</p>
									) : null}
									{reprocessErrors.get(resource.resource_id) ? (
										<p className="library-error" role="alert">
											{reprocessErrors.get(resource.resource_id)}
										</p>
									) : null}
									<div className="resource-actions">
										{!resource.snapshot &&
										(resource.status === "unprocessed" ||
											resource.status === "failed") ? (
											<button
												className="compact-action secondary-action"
												type="button"
												onClick={() => void handleProcess(resource.resource_id)}
												disabled={processing.has(resource.resource_id)}
											>
												{processing.has(resource.resource_id)
													? "Processing…"
													: "Make searchable"}
											</button>
										) : null}
										{resource.snapshot ? (
											<button
												className="compact-action secondary-action"
												type="button"
												onClick={() =>
													void handleReprocess(resource.resource_id)
												}
												disabled={reprocessing.has(resource.resource_id)}
											>
												{reprocessing.has(resource.resource_id)
													? "Reprocessing…"
													: "Reprocess"}
											</button>
										) : null}
										{resource.status === "ready" && !admitted ? (
											<button
												className="compact-action secondary-action"
												type="button"
												onClick={() => void handleAdmit(resource.resource_id)}
												disabled={admitting.has(resource.resource_id)}
											>
												{admitting.has(resource.resource_id)
													? "Admitting…"
													: "Use as course material"}
											</button>
										) : null}
										{resource.kind === "remote" ? (
											<button
												className="compact-action secondary-action"
												type="button"
												onClick={() => void handleRefresh(resource.resource_id)}
												disabled={refreshing.has(resource.resource_id)}
											>
												{refreshing.has(resource.resource_id)
													? "Refreshing…"
													: "Refresh"}
											</button>
										) : null}
										{!admitted ? (
											<button
												className="compact-action secondary-action"
												type="button"
												onClick={() =>
													void handleRemoveResource(resource.resource_id)
												}
												disabled={removing.has(resource.resource_id)}
											>
												{removing.has(resource.resource_id)
													? "Deleting…"
													: "Delete"}
											</button>
										) : null}
									</div>
								</li>
							);
						})}
					</ul>
				)}

				{uploading ? (
					<p className="empty-note" aria-live="assertive">
						Uploading file…
					</p>
				) : null}
			</section>

			<section
				className="content-section discovery-section"
				aria-label="Remote discovery"
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">Discovery</p>
						<h2>Find remote resources</h2>
					</div>
				</div>
				<p className="network-disclosure" role="note">
					Searching sends only your query to the listed public discovery services.
					Adding a result sends its URL to that result&apos;s host so Course Harness
					can capture a Snapshot. These connectors use no Provider Account or
					unrelated environment credentials.
				</p>

				<form className="search-form" onSubmit={handleDiscoverySearch}>
					<input
						type="search"
						className="search-input"
						value={discoveryQuery}
						onChange={(e) => setDiscoveryQuery(e.target.value)}
						placeholder="Search arXiv, CrossRef, GitHub, HuggingFace…"
						aria-label="Search remote resources"
					/>
					<button
						type="submit"
						className="search-submit primary-action"
						disabled={discovering || !discoveryQuery.trim()}
					>
						{discovering ? "Searching…" : "Search"}
					</button>
				</form>

				{discoveryResults.length > 0 ? (
					<div className="discovery-results">
						{discoveryResults.map((result) => (
							<div key={result.provider} className="discovery-provider-group">
								<h3 className="discovery-provider-label">{result.provider}</h3>
								{result.error ? (
									<p className="library-error" role="alert">
										{result.error}
									</p>
								) : null}
								{result.candidates.length > 0 ? (
									<ul className="library-list">
										{result.candidates.map((candidate) => (
											<li key={candidate.url}>
												<div className="library-meta">
													<a
														className="library-name library-name-link"
														href={candidate.url}
														target="_blank"
														rel="noopener noreferrer"
													>
														{candidate.title || candidate.url}
													</a>
													{candidate.authors ? (
														<span className="library-label">
															{candidate.authors.join(", ")}
														</span>
													) : null}
													{candidate.published_at ? (
														<span className="library-label">
															{candidate.published_at.slice(0, 10)}
														</span>
													) : null}
												</div>
												{candidate.summary ? (
													<p className="library-snapshot">
														{candidate.summary.slice(0, 500)}
														{candidate.summary.length > 500 ? "…" : ""}
													</p>
												) : null}
												<div className="resource-actions">
													<button
														className="compact-action secondary-action"
														type="button"
														onClick={() => void handleAddRemote(candidate)}
														disabled={addingRemote.has(candidate.url)}
													>
														{addingRemote.has(candidate.url)
															? "Adding…"
															: "Add to Library"}
													</button>
												</div>
											</li>
										))}
									</ul>
								) : !result.error ? (
									<p className="empty-note">No results.</p>
								) : null}
							</div>
						))}
					</div>
				) : null}
			</section>

			{sources.length > 0 ? (
				<section
					className="content-section search-section"
					aria-labelledby="search-heading"
				>
					<div className="content-section-heading">
						<div>
							<p className="section-kicker">Search</p>
							<h2 id="search-heading">Find in sources</h2>
						</div>
					</div>

					<form className="search-form" onSubmit={handleSearch}>
						<input
							type="search"
							className="search-input"
							value={searchQuery}
							onChange={(e) => {
								setSearchQuery(e.target.value);
								setHasSearched(false);
							}}
							placeholder="Search admitted sources…"
							aria-label="Search source content"
						/>
						<button
							type="submit"
							className="search-submit primary-action"
							disabled={searching || !searchQuery.trim()}
						>
							{searching ? "Searching…" : "Search"}
						</button>
					</form>

					{searchResults.length > 0 ? (
						<ul className="search-results">
							{searchResults.map((group) => (
								<li className="search-group" key={group.source_id}>
									<button
										type="button"
										className="search-group-label"
										onClick={() => void handleViewSource(group.source_id)}
									>
										{group.label}
										<span className="search-group-count">
											{group.chunks.length}{" "}
											{group.chunks.length === 1 ? "match" : "matches"}
										</span>
									</button>

									{viewingSource === group.source_id ? (
										<div className="source-content-panel">
											{loadingContent ? (
												<p className="empty-note">Loading…</p>
											) : (
												<pre className="source-content-body">
													{sourceContent}
												</pre>
											)}
											<button
												type="button"
												className="quiet-action"
												onClick={() => {
													setViewingSource(null);
													setSourceContent("");
												}}
											>
												Close
											</button>
										</div>
									) : null}

									<ul className="search-chunks">
										{group.chunks.map((chunk: SearchResult) => (
											<li
												key={`${group.source_id}-${chunk.coordinates.line_start ?? 0}`}
											>
												<p className="search-result-snippet">{chunk.snippet}</p>
												{chunk.coordinates.line_start != null ? (
													<p className="search-result-coordinates">
														Line {chunk.coordinates.line_start + 1}
														{chunk.coordinates.line_end != null &&
														chunk.coordinates.line_end !==
															chunk.coordinates.line_start
															? `–${chunk.coordinates.line_end + 1}`
															: ""}
													</p>
												) : null}
											</li>
										))}
									</ul>
								</li>
							))}
						</ul>
					) : searchResults.length === 0 && hasSearched && !searching ? (
						<p className="empty-note">No results for "{lastSubmittedQuery}".</p>
					) : null}
				</section>
			) : null}
		</main>
	);
}
