import {
	AlertCircle,
	AlertTriangle,
	ExternalLink,
	FilePlus2,
	FileText,
	Globe,
	Loader2,
	MoreHorizontal,
	RefreshCw,
	RotateCcw,
	Search,
	Trash2,
	X,
} from "lucide-react";
import { type FormEvent, type ReactNode, useRef, useState } from "react";

import { responseError } from "../api";
import { DropZone, UploadList } from "../chat/StartSurface";
import type {
	Candidate,
	DiscoveryResult,
	GroupedSearchResult,
	ReaderTarget,
	ResourceState,
	Source,
} from "../models";
import {
	ConfirmDialog,
	errorMessage,
	Menu,
	type MenuItem,
	Notice,
	Tabs,
} from "../ui";
import type { AgentContext } from "../useCourseAgent";
import { type Library, MATERIAL_ACCEPT, resourceName } from "../useLibrary";
import { lineRangeLabel } from "./SourceReader";

export type SourcesTab = "course" | "library" | "discover";

type Pending =
	| { kind: "remove"; source: Source }
	| { kind: "delete"; resource: ResourceState };

function formatBytes(bytes: number): string {
	if (bytes === 0) return "0 B";
	const units = ["B", "KB", "MB", "GB"];
	const index = Math.min(
		Math.floor(Math.log(bytes) / Math.log(1024)),
		units.length - 1,
	);
	return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function fileKind(resource: ResourceState | undefined): string | null {
	if (!resource?.location) return null;
	if (resource.kind === "remote") {
		try {
			return new URL(resource.location).hostname.replace(/^www\./, "");
		} catch {
			return "Web";
		}
	}
	const extension = resource.location.split(".").at(-1);
	if (!extension || extension === resource.location) return null;
	const labels: Record<string, string> = {
		pdf: "PDF",
		pptx: "PowerPoint",
		docx: "Word",
		md: "Markdown",
		markdown: "Markdown",
		txt: "Text",
	};
	return labels[extension.toLowerCase()] ?? extension.toUpperCase();
}

/** Turns FTS snippets with literal <mark> tags into highlighted text safely. */
function Snippet({ text }: { text: string }) {
	const parts = text.split(/<mark>([\s\S]*?)<\/mark>/g);
	return (
		<>
			{parts.map((part, index) =>
				index % 2 === 1 ? (
					// biome-ignore lint/suspicious/noArrayIndexKey: parts are positional
					<mark key={index} className="evidence-mark">
						{part}
					</mark>
				) : (
					// biome-ignore lint/suspicious/noArrayIndexKey: parts are positional
					<span key={index}>{part.replace(/<\/?mark>/g, "")}</span>
				),
			)}
		</>
	);
}

export function SourcesCanvas({
	initialTab,
	resources,
	sources,
	library,
	onRead,
	onAskAgent,
}: {
	initialTab?: SourcesTab;
	resources: ResourceState[];
	sources: Source[];
	library: Library;
	onRead: (target: ReaderTarget) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
}) {
	const [tab, setTab] = useState<SourcesTab>(initialTab ?? "course");
	const [pending, setPending] = useState<Pending | null>(null);
	const [pendingBusy, setPendingBusy] = useState(false);
	const fileInputRef = useRef<HTMLInputElement>(null);

	const sourceByResource = new Map(
		sources.map((source) => [source.resource_id, source]),
	);
	const resourceById = new Map(
		resources.map((resource) => [resource.resource_id, resource]),
	);

	async function confirmPending() {
		if (!pending) return;
		setPendingBusy(true);
		if (pending.kind === "remove")
			await library.removeFromCourse(pending.source.id);
		else await library.deleteResource(pending.resource.resource_id);
		setPendingBusy(false);
		setPending(null);
	}

	return (
		<div className="canvas-page sources">
			<header className="canvas-head">
				<div className="canvas-head-copy">
					<h1 className="display-title">Sources</h1>
					<p className="meta">
						{tab === "library"
							? "Files saved on this computer. Add the ones this course should use."
							: tab === "discover"
								? "Find papers and repositories to add."
								: "What the Course Agent can read and cite for this course."}
					</p>
				</div>
				<div className="canvas-head-actions">
					<button
						type="button"
						className="btn btn-primary"
						onClick={() => fileInputRef.current?.click()}
					>
						<FilePlus2 aria-hidden="true" />
						{tab === "library" ? "Add to Library" : "Add files"}
					</button>
					<input
						ref={fileInputRef}
						type="file"
						multiple
						hidden
						accept={MATERIAL_ACCEPT}
						aria-label={
							tab === "library"
								? "Add files to Library"
								: "Add files to this course"
						}
						onChange={(event) => {
							const files = Array.from(event.target.files ?? []);
							event.target.value = "";
							void library.addFiles(files, tab !== "library");
						}}
					/>
				</div>
			</header>

			<UploadList library={library} />

			<Tabs
				label="Source scope"
				value={tab}
				onChange={setTab}
				tabs={[
					{ id: "course", label: "This course", count: sources.length },
					{ id: "library", label: "Library", count: resources.length },
					{ id: "discover", label: "Discover" },
				]}
			/>

			{/* Every tab stays mounted so its query and results survive switching. */}
			<div
				className="sources-panel"
				role="tabpanel"
				aria-label="This course"
				hidden={tab !== "course"}
			>
				<CourseTab
					sources={sources}
					resourceById={resourceById}
					library={library}
					onRead={onRead}
					onAskAgent={onAskAgent}
					onRemove={(source) => setPending({ kind: "remove", source })}
					onDiscover={() => setTab("discover")}
				/>
			</div>
			<div
				className="sources-panel"
				role="tabpanel"
				aria-label="Library"
				hidden={tab !== "library"}
			>
				<LibraryTab
					resources={resources}
					sourceByResource={sourceByResource}
					library={library}
					onRead={onRead}
					onDelete={(resource) => setPending({ kind: "delete", resource })}
				/>
			</div>
			<div
				className="sources-panel"
				role="tabpanel"
				aria-label="Discover"
				hidden={tab !== "discover"}
			>
				<DiscoverTab
					resources={resources}
					sourceByResource={sourceByResource}
					library={library}
				/>
			</div>

			{pending?.kind === "remove" ? (
				<ConfirmDialog
					title="Remove from this course?"
					confirmLabel="Remove from course"
					busy={pendingBusy}
					error={library.errors.get(pending.source.resource_id)}
					onCancel={() => setPending(null)}
					onConfirm={() => void confirmPending()}
				>
					<p>
						“{pending.source.label}” stays in your Library, but the Course Agent
						will no longer read it for this course. Existing Citations to it
						keep pointing at the version they cite.
					</p>
				</ConfirmDialog>
			) : null}
			{pending?.kind === "delete" ? (
				<ConfirmDialog
					title="Delete from Library?"
					confirmLabel="Delete from Library"
					busy={pendingBusy}
					error={library.errors.get(pending.resource.resource_id)}
					onCancel={() => setPending(null)}
					onConfirm={() => void confirmPending()}
				>
					<p>
						“{resourceName(pending.resource)}” will be removed from the Library
						on this computer. Other courses that include it keep their pinned
						copy.
					</p>
				</ConfirmDialog>
			) : null}
		</div>
	);
}

/* ---------- This course ---------- */

function CourseTab({
	sources,
	resourceById,
	library,
	onRead,
	onAskAgent,
	onRemove,
	onDiscover,
}: {
	sources: Source[];
	resourceById: Map<string, ResourceState>;
	library: Library;
	onRead: (target: ReaderTarget) => void;
	onAskAgent: (request: string, context?: AgentContext | null) => void;
	onRemove: (source: Source) => void;
	onDiscover: () => void;
}) {
	const [query, setQuery] = useState("");
	const [submitted, setSubmitted] = useState("");
	const [results, setResults] = useState<GroupedSearchResult[] | null>(null);
	const [searching, setSearching] = useState(false);
	const [searchError, setSearchError] = useState<string | null>(null);

	async function search(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const trimmed = query.trim();
		if (!trimmed) {
			setResults(null);
			return;
		}
		setSearching(true);
		setSearchError(null);
		setSubmitted(trimmed);
		try {
			const response = await fetch("/api/sources/search", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ query: trimmed, limit: 20 }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			setResults((await response.json()) as GroupedSearchResult[]);
		} catch (caught) {
			setResults([]);
			setSearchError(
				errorMessage(caught, "The search could not be completed."),
			);
		} finally {
			setSearching(false);
		}
	}

	if (sources.length === 0) {
		return (
			<div className="sources-empty">
				<DropZone library={library} compact />
				<p className="meta">
					Or{" "}
					<button type="button" className="link-btn" onClick={onDiscover}>
						find papers in Discover
					</button>
					, or ask the Course Agent to suggest some.{" "}
					<button
						type="button"
						className="link-btn"
						onClick={() =>
							onAskAgent(
								"Suggest papers and resources I should add as Sources for this course.",
								null,
							)
						}
					>
						Ask the agent
					</button>
				</p>
			</div>
		);
	}

	return (
		<>
			<form
				className="search-row sources-search"
				onSubmit={(event) => void search(event)}
			>
				<div className="search-field">
					<Search aria-hidden="true" />
					<input
						type="search"
						aria-label="Search inside this course's Sources"
						placeholder="Search inside this course's Sources"
						value={query}
						onChange={(event) => {
							setQuery(event.target.value);
							if (!event.target.value.trim()) setResults(null);
						}}
					/>
				</div>
				<button
					type="submit"
					className="btn"
					disabled={searching || !query.trim()}
				>
					{searching ? "Searching…" : "Search"}
				</button>
			</form>

			{results !== null ? (
				<section className="sources-results" aria-label="Search results">
					<div className="canvas-section-head">
						<h2>
							{searching
								? "Searching…"
								: `${results.reduce((total, group) => total + group.chunks.length, 0)} passages for “${submitted}”`}
						</h2>
						<button
							type="button"
							className="btn btn-quiet btn-small"
							onClick={() => {
								setQuery("");
								setResults(null);
							}}
						>
							<X aria-hidden="true" />
							Clear search
						</button>
					</div>
					{searchError ? <Notice tone="error">{searchError}</Notice> : null}
					{results.length === 0 && !searching && !searchError ? (
						<p className="empty">
							No passages match “{submitted}”. Try other words.
						</p>
					) : (
						<ul className="sources-result-groups">
							{results.map((group) => (
								<li
									key={group.source_id}
									className="panel sources-result-group"
								>
									<button
										type="button"
										className="sources-result-source"
										onClick={() =>
											onRead({
												sourceId: group.source_id,
												resourceId: group.resource_id,
												label: group.label,
												lineStart: null,
												lineEnd: null,
											})
										}
									>
										<FileText aria-hidden="true" />
										{group.label}
									</button>
									<ul>
										{group.chunks.map((chunk) => (
											<li
												key={`${group.source_id}-${chunk.coordinates.line_start ?? 0}-${chunk.rank}`}
											>
												<button
													type="button"
													className="sources-passage"
													onClick={() =>
														onRead({
															sourceId: group.source_id,
															resourceId: group.resource_id,
															label: group.label,
															lineStart: chunk.coordinates.line_start,
															lineEnd: chunk.coordinates.line_end,
														})
													}
												>
													<span className="sources-passage-text">
														<Snippet text={chunk.snippet} />
													</span>
													{chunk.coordinates.line_start !== null ? (
														<span className="sources-passage-where">
															{lineRangeLabel(
																chunk.coordinates.line_start,
																chunk.coordinates.line_end,
															)}
														</span>
													) : null}
												</button>
											</li>
										))}
									</ul>
								</li>
							))}
						</ul>
					)}
				</section>
			) : (
				<ul
					className="row-list sources-list"
					aria-label="Sources in this course"
				>
					{sources.map((source) => {
						const resource = resourceById.get(source.resource_id);
						const busy = library.busy.get(source.resource_id);
						const error = library.errors.get(source.resource_id);
						const updateAvailable =
							resource?.status === "ready" &&
							resource.snapshot !== null &&
							source.source_version_id !== resource.snapshot.content_hash;
						const kind = fileKind(resource);
						const menuItems: MenuItem[] = [];
						if (resource?.snapshot && resource.status !== "processing")
							menuItems.push({
								label: "Process again",
								detail: "Re-read the file to refresh search",
								icon: <RotateCcw aria-hidden="true" />,
								disabled: Boolean(busy),
								onSelect: () => void library.reprocess(resource.resource_id),
							});
						if (resource?.kind === "remote")
							menuItems.push({
								label: "Check for a newer version",
								icon: <RefreshCw aria-hidden="true" />,
								disabled: Boolean(busy) || resource.status === "processing",
								onSelect: () =>
									void library.refreshRemote(resource.resource_id),
							});
						return (
							<li key={source.id} className="row">
								{resource?.kind === "remote" ? (
									<Globe className="row-icon" aria-hidden="true" />
								) : (
									<FileText className="row-icon" aria-hidden="true" />
								)}
								<div className="row-main">
									<button
										type="button"
										className="row-title"
										title={source.label}
										onClick={() =>
											onRead({
												sourceId: source.id,
												resourceId: source.resource_id,
												label: source.label,
												lineStart: null,
												lineEnd: null,
											})
										}
									>
										{source.label}
									</button>
									<div className="row-meta">
										{kind ? <span>{kind}</span> : null}
										{resource?.snapshot ? (
											<span>{formatBytes(resource.snapshot.byte_count)}</span>
										) : null}
										{busy ? (
											<span className="state is-working">
												<Loader2 className="spin" aria-hidden="true" />
												{busy}
											</span>
										) : resource && !resource.indexed ? (
											<span className="state is-warning">
												<AlertTriangle aria-hidden="true" />
												Search unavailable
											</span>
										) : (
											<span>Searchable</span>
										)}
										{updateAvailable ? (
											<span className="state is-warning">
												<AlertTriangle aria-hidden="true" />
												Newer version available
											</span>
										) : null}
									</div>
									{error ? (
										<p className="row-error" role="alert">
											{error}
										</p>
									) : null}
								</div>
								<div className="row-actions">
									{updateAvailable ? (
										<button
											type="button"
											className="btn btn-small"
											disabled={Boolean(busy)}
											onClick={() => void library.adoptVersion(source.id)}
										>
											Use newer version
										</button>
									) : null}
									{menuItems.length > 0 ? (
										<Menu
											label={`More actions for ${source.label}`}
											trigger={<MoreHorizontal aria-hidden="true" />}
											items={menuItems}
										/>
									) : null}
									<button
										type="button"
										className="icon-btn is-danger"
										aria-label={`Remove ${source.label} from course`}
										title="Remove from course"
										disabled={Boolean(busy)}
										onClick={() => onRemove(source)}
									>
										<Trash2 aria-hidden="true" />
									</button>
								</div>
							</li>
						);
					})}
				</ul>
			)}
		</>
	);
}

/* ---------- Library ---------- */

function LibraryTab({
	resources,
	sourceByResource,
	library,
	onRead,
	onDelete,
}: {
	resources: ResourceState[];
	sourceByResource: Map<string, Source>;
	library: Library;
	onRead: (target: ReaderTarget) => void;
	onDelete: (resource: ResourceState) => void;
}) {
	const [filter, setFilter] = useState("");
	const needle = filter.trim().toLowerCase();
	const visible = needle
		? resources.filter((resource) =>
				`${resourceName(resource)} ${resource.location ?? ""}`
					.toLowerCase()
					.includes(needle),
			)
		: resources;

	if (resources.length === 0) {
		return (
			<p className="empty">
				<strong>Your Library is empty.</strong>
				Files you add to any course are kept here so you can reuse them.
			</p>
		);
	}

	return (
		<>
			<div className="search-row sources-search">
				<div className="search-field">
					<Search aria-hidden="true" />
					<input
						type="search"
						aria-label="Filter Library by name"
						placeholder="Filter Library by name"
						value={filter}
						onChange={(event) => setFilter(event.target.value)}
					/>
				</div>
			</div>
			{library.indexError ? (
				<Notice tone="error">{library.indexError}</Notice>
			) : null}
			{visible.length === 0 ? (
				<p className="empty">No files match “{filter.trim()}”.</p>
			) : (
				<ul className="row-list sources-list" aria-label="Library">
					{visible.map((resource) => (
						<LibraryRow
							key={resource.resource_id}
							resource={resource}
							source={sourceByResource.get(resource.resource_id)}
							library={library}
							onRead={onRead}
							onDelete={onDelete}
						/>
					))}
				</ul>
			)}
		</>
	);
}

function LibraryRow({
	resource,
	source,
	library,
	onRead,
	onDelete,
}: {
	resource: ResourceState;
	source: Source | undefined;
	library: Library;
	onRead: (target: ReaderTarget) => void;
	onDelete: (resource: ResourceState) => void;
}) {
	const id = resource.resource_id;
	const name = source?.label ?? resourceName(resource);
	const busy = library.busy.get(id);
	const error = library.errors.get(id) ?? resource.error;
	const remote = resource.kind === "remote";
	const kind = fileKind(resource);

	let state: ReactNode;
	let primary: ReactNode = null;
	if (
		busy ||
		resource.status === "processing" ||
		resource.status === "retrying"
	) {
		state = (
			<span className="state is-working">
				<Loader2 className="spin" aria-hidden="true" />
				{busy ??
					(remote && !resource.snapshot ? "Downloading…" : "Processing…")}
			</span>
		);
	} else if (resource.status === "failed") {
		state = (
			<span className="state is-error">
				<AlertCircle aria-hidden="true" />
				{remote && !resource.snapshot
					? "Download failed"
					: "Couldn't read this file"}
			</span>
		);
		primary = (
			<button
				type="button"
				className="btn btn-small"
				onClick={() =>
					void (remote ? library.refreshRemote(id) : library.process(id))
				}
			>
				<RotateCcw aria-hidden="true" />
				{remote && !resource.snapshot ? "Retry download" : "Try again"}
			</button>
		);
	} else if (resource.status === "unprocessed") {
		state = (
			<span className="state">
				{remote ? "Waiting to download" : "Waiting to process"}
			</span>
		);
		primary =
			remote && !resource.snapshot ? (
				<button
					type="button"
					className="btn btn-small"
					onClick={() => void library.refreshRemote(id)}
				>
					Retry download
				</button>
			) : resource.kind === "local-file" && !resource.snapshot ? (
				<button
					type="button"
					className="btn btn-small"
					onClick={() => void library.process(id)}
				>
					Process
				</button>
			) : null;
	} else if (source) {
		state = <span className="state is-included">In this course</span>;
	} else {
		state = resource.indexed ? (
			<span>Searchable</span>
		) : (
			<span className="state is-warning">
				<AlertTriangle aria-hidden="true" />
				Search unavailable
			</span>
		);
		primary = (
			<button
				type="button"
				className="btn btn-small"
				onClick={() => void library.include(id)}
			>
				Add to course
			</button>
		);
	}

	const menuItems: MenuItem[] = [];
	if (resource.snapshot && resource.status !== "processing")
		menuItems.push({
			label: "Process again",
			detail: "Re-read the file to refresh search",
			icon: <RotateCcw aria-hidden="true" />,
			disabled: Boolean(busy),
			onSelect: () => void library.reprocess(id),
		});
	if (remote && resource.snapshot)
		menuItems.push({
			label: "Check for a newer version",
			icon: <RefreshCw aria-hidden="true" />,
			disabled: Boolean(busy) || resource.status === "processing",
			onSelect: () => void library.refreshRemote(id),
		});
	if (remote && resource.location)
		menuItems.push({
			label: "Open original",
			icon: <ExternalLink aria-hidden="true" />,
			onSelect: () =>
				window.open(resource.location ?? "", "_blank", "noopener"),
		});

	return (
		<li className="row">
			{remote ? (
				<Globe className="row-icon" aria-hidden="true" />
			) : (
				<FileText className="row-icon" aria-hidden="true" />
			)}
			<div className="row-main">
				{resource.snapshot ? (
					<button
						type="button"
						className="row-title"
						title={resource.location ?? name}
						onClick={() =>
							onRead({
								sourceId: source?.id ?? null,
								resourceId: id,
								label: name,
								lineStart: null,
								lineEnd: null,
							})
						}
					>
						{name}
					</button>
				) : (
					<span className="row-title" title={resource.location ?? name}>
						{name}
					</span>
				)}
				<div className="row-meta">
					{state}
					{kind ? <span>{kind}</span> : null}
					{resource.snapshot ? (
						<span>{formatBytes(resource.snapshot.byte_count)}</span>
					) : null}
				</div>
				{error ? (
					<p className="row-error" role="alert">
						{error}
					</p>
				) : null}
			</div>
			<div className="row-actions">
				{primary}
				{menuItems.length > 0 ? (
					<Menu
						label={`More actions for ${name}`}
						trigger={<MoreHorizontal aria-hidden="true" />}
						items={menuItems}
					/>
				) : null}
				<button
					type="button"
					className="icon-btn is-danger"
					aria-label={`Delete ${name} from Library`}
					title={
						source ? "Remove it from this course first" : "Delete from Library"
					}
					disabled={Boolean(source) || Boolean(busy)}
					onClick={() => onDelete(resource)}
				>
					<Trash2 aria-hidden="true" />
				</button>
			</div>
		</li>
	);
}

/* ---------- Discover ---------- */

function DiscoverTab({
	resources,
	sourceByResource,
	library,
}: {
	resources: ResourceState[];
	sourceByResource: Map<string, Source>;
	library: Library;
}) {
	const [query, setQuery] = useState("");
	const [results, setResults] = useState<DiscoveryResult[] | null>(null);
	const [searching, setSearching] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function search(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const trimmed = query.trim();
		if (!trimmed) return;
		setSearching(true);
		setError(null);
		try {
			const response = await fetch("/api/discovery/search", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ query: trimmed }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			setResults((await response.json()) as DiscoveryResult[]);
		} catch (caught) {
			setError(errorMessage(caught, "The search could not be completed."));
		} finally {
			setSearching(false);
		}
	}

	const total =
		results?.reduce((sum, group) => sum + group.candidates.length, 0) ?? 0;

	return (
		<>
			<form
				className="search-row sources-search"
				onSubmit={(event) => void search(event)}
			>
				<div className="search-field">
					<Search aria-hidden="true" />
					<input
						type="search"
						aria-label="Search for papers and repositories"
						placeholder="Search arXiv, Crossref, GitHub, and Hugging Face"
						value={query}
						onChange={(event) => setQuery(event.target.value)}
					/>
				</div>
				<button
					type="submit"
					className="btn btn-primary"
					disabled={searching || !query.trim()}
				>
					{searching ? "Searching…" : "Search"}
				</button>
			</form>
			<p className="meta sources-disclosure" role="note">
				Searching sends only your query to these public services. Adding a
				result sends its address to its host so a copy can be saved. No Provider
				Account credentials are used.
			</p>
			{error ? <Notice tone="error">{error}</Notice> : null}
			{results !== null &&
			total === 0 &&
			!results.some((group) => group.error) ? (
				<p className="empty">Nothing found. Try broader or different words.</p>
			) : null}
			{results?.map((group) => (
				<section
					key={group.provider}
					className="sources-provider"
					aria-label={group.provider}
				>
					<h2 className="section-label sources-provider-label">
						{group.provider}
					</h2>
					{group.error ? <Notice tone="warning">{group.error}</Notice> : null}
					{group.candidates.length > 0 ? (
						<ul className="row-list">
							{group.candidates.map((candidate) => (
								<CandidateRow
									key={candidate.url}
									candidate={candidate}
									resource={resources.find(
										(resource) => resource.location === candidate.url,
									)}
									sourceByResource={sourceByResource}
									library={library}
								/>
							))}
						</ul>
					) : !group.error ? (
						<p className="meta">No results from {group.provider}.</p>
					) : null}
				</section>
			))}
		</>
	);
}

function CandidateRow({
	candidate,
	resource,
	sourceByResource,
	library,
}: {
	candidate: Candidate;
	resource: ResourceState | undefined;
	sourceByResource: Map<string, Source>;
	library: Library;
}) {
	const busy = library.busy.get(candidate.url);
	const error = library.errors.get(candidate.url);
	const included = resource
		? sourceByResource.has(resource.resource_id)
		: false;
	const summary = candidate.summary
		? candidate.summary.length > 280
			? `${candidate.summary.slice(0, 280)}…`
			: candidate.summary
		: null;
	return (
		<li className="row sources-candidate">
			<div className="row-main">
				<a
					className="sources-candidate-title"
					href={candidate.url}
					target="_blank"
					rel="noopener noreferrer"
				>
					{candidate.title || candidate.url}
					<ExternalLink aria-hidden="true" />
				</a>
				<div className="row-meta">
					{candidate.authors?.length ? (
						<span>
							{candidate.authors.slice(0, 3).join(", ")}
							{candidate.authors.length > 3 ? " et al." : ""}
						</span>
					) : null}
					{candidate.published_at ? (
						<span>{candidate.published_at.slice(0, 10)}</span>
					) : null}
					{included ? (
						<span className="state is-included">In this course</span>
					) : resource ? (
						<span className="state">In Library</span>
					) : null}
					{busy ? (
						<span className="state is-working">
							<Loader2 className="spin" aria-hidden="true" />
							{busy}
						</span>
					) : null}
				</div>
				{summary ? (
					<p className="sources-candidate-summary">{summary}</p>
				) : null}
				{error ? (
					<p className="row-error" role="alert">
						{error}
					</p>
				) : null}
			</div>
			{!included ? (
				<div className="row-actions sources-candidate-actions">
					{!resource ? (
						<button
							type="button"
							className="btn btn-quiet btn-small"
							disabled={Boolean(busy)}
							onClick={() => void library.addRemote(candidate, false)}
						>
							Save to Library
						</button>
					) : null}
					<button
						type="button"
						className="btn btn-primary btn-small"
						disabled={Boolean(busy)}
						onClick={() => void library.addRemote(candidate, true)}
					>
						Add to course
					</button>
				</div>
			) : null}
		</li>
	);
}
