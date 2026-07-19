import { useState } from "react";
import { responseError } from "./api";
import type { ResourceState, SearchResult, Source } from "./models";

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
	const [admitting, setAdmitting] = useState<Set<string>>(new Set());
	const [searchQuery, setSearchQuery] = useState("");
	const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
	const [searching, setSearching] = useState(false);

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

	async function handleClearCache() {
		try {
			await fetch("/api/resources/cache", { method: "DELETE" });
		} catch {
			// Cache clearing failure is non-blocking
		}
	}

	async function handleSearch(event: React.FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const q = searchQuery.trim();
		if (!q) {
			setSearchResults([]);
			return;
		}
		setSearching(true);
		try {
			const response = await fetch("/api/sources/search", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ query: q, limit: 10 }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			setSearchResults((await response.json()) as SearchResult[]);
		} catch {
			setSearchResults([]);
		} finally {
			setSearching(false);
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
							onClick={handleClearCache}
						>
							Clear cache
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
										{admitted ? (
											<strong className="status-badge status-ready">
												Admitted
											</strong>
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
													: "Process"}
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
													: "Admit as source"}
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
							value={searchQuery}
							onChange={(e) => setSearchQuery(e.target.value)}
							placeholder="Search admitted sources…"
							aria-label="Search source content"
						/>
						<button
							type="submit"
							className="compact-action primary-action"
							disabled={searching || !searchQuery.trim()}
						>
							{searching ? "Searching…" : "Search"}
						</button>
					</form>

					{searchResults.length > 0 ? (
						<ul className="search-results">
							{searchResults.map((result) => (
								<li key={`${result.source_id}-${result.snippet}`}>
									<p className="search-result-label">{result.label}</p>
									<p className="search-result-snippet">
										{result.snippet}
									</p>
									{result.coordinates.line_start != null ? (
										<p className="search-result-coordinates">
											Line {result.coordinates.line_start + 1}
											{result.coordinates.line_end != null &&
											result.coordinates.line_end !==
												result.coordinates.line_start
												? `–${result.coordinates.line_end + 1}`
												: ""}
										</p>
									) : null}
								</li>
							))}
						</ul>
					) : searchResults.length === 0 && searchQuery.trim() && !searching ? (
						<p className="empty-note">No results for "{searchQuery}".</p>
					) : null}
				</section>
			) : null}
		</main>
	);
}
