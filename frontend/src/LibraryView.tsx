import { useState } from "react";
import { responseError } from "./api";
import type {
	Candidate,
	DiscoveryResult,
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
};

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
}: LibraryViewProps) {
	const [uploading, setUploading] = useState(false);
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

	const [discoveryQuery, setDiscoveryQuery] = useState("");
	const [discovering, setDiscovering] = useState(false);
	const [discoveryResults, setDiscoveryResults] = useState<DiscoveryResult[]>([]);
	const [addingRemote, setAddingRemote] = useState<Set<string>>(new Set());
	const [refreshing, setRefreshing] = useState<Set<string>>(new Set());
	const [adopting, setAdopting] = useState<Set<string>>(new Set());

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

	async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		if (!file) return;
		setUploading(true);
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
		} finally {
			setUploading(false);
			event.target.value = "";
		}
	}

	async function handleProcess(resourceId: string) {
		setProcessing((current) => new Set(current).add(resourceId));
		try {
			const response = await fetch(
				`/api/resources/${encodeURIComponent(resourceId)}/process`,
				{ method: "POST" },
			);
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
													Remove
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
										{resource.status === "unprocessed" ||
										resource.status === "failed" ? (
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
										{resource.status === "ready" ? (
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
												onClick={() =>
													void handleRefresh(resource.resource_id)
												}
												disabled={refreshing.has(resource.resource_id)}
											>
												{refreshing.has(resource.resource_id)
													? "Refreshing…"
													: "Refresh"}
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
													<span className="library-name">
														{candidate.title || candidate.url}
													</span>
													{candidate.authors ? (
														<span className="library-label">
															{candidate.authors.join(", ")}
														</span>
													) : null}
												</div>
												{candidate.summary ? (
													<p className="library-snapshot">
														{candidate.summary.slice(0, 300)}
														{candidate.summary.length > 300 ? "…" : ""}
													</p>
												) : null}
												<div className="resource-actions">
													<button
														className="compact-action secondary-action"
														type="button"
														onClick={() =>
															void handleAddRemote(candidate)
														}
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
