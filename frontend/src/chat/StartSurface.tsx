import {
	AlertCircle,
	FileText,
	LibraryBig,
	Loader2,
	RotateCcw,
	Search,
	Upload,
	X,
} from "lucide-react";
import { type DragEvent, useRef, useState } from "react";

import type { CanvasTarget } from "../App";
import type { Source } from "../models";
import { type Library, MATERIAL_ACCEPT, type UploadItem } from "../useLibrary";

export function StartSurface({
	sources,
	library,
	onOpen,
}: {
	sources: Source[];
	library: Library;
	onOpen: (target: CanvasTarget) => void;
}) {
	return (
		<div className="start">
			<h2 className="display-title">Start with your material</h2>
			<DropZone library={library} />
			<div className="start-alternatives">
				<button
					type="button"
					className="btn"
					onClick={() => onOpen({ kind: "sources", tab: "discover" })}
				>
					<Search aria-hidden="true" />
					Discover papers
				</button>
				<button
					type="button"
					className="btn"
					onClick={() => onOpen({ kind: "sources", tab: "library" })}
				>
					<LibraryBig aria-hidden="true" />
					Choose from Library
				</button>
			</div>
			<UploadList library={library} />
			{sources.length > 0 ? (
				<section
					className="start-sources"
					aria-labelledby="start-sources-heading"
				>
					<h3 id="start-sources-heading" className="section-label">
						In this course
					</h3>
					<ul>
						{sources.map((source) => (
							<li key={source.id}>
								<FileText aria-hidden="true" />
								<span>{source.label}</span>
							</li>
						))}
					</ul>
				</section>
			) : null}
			<p className="start-next">
				{sources.length > 0
					? "Tell the agent who the Course is for and what it should teach. It will draft a Course Plan from these Sources."
					: "Or skip ahead: describe the Course below and the agent will draft a plan."}{" "}
				<button
					type="button"
					className="link-btn"
					onClick={() => onOpen({ kind: "plan" })}
				>
					Write the plan yourself
				</button>
			</p>
		</div>
	);
}

export function DropZone({
	library,
	compact = false,
}: {
	library: Library;
	compact?: boolean;
}) {
	const inputRef = useRef<HTMLInputElement>(null);
	const [dragging, setDragging] = useState(false);

	function onDrop(event: DragEvent<HTMLDivElement>) {
		event.preventDefault();
		setDragging(false);
		const files = Array.from(event.dataTransfer.files);
		void library.addFiles(files, true);
	}

	return (
		// biome-ignore lint/a11y/noStaticElementInteractions: dropping is a shortcut; the Add files button is the accessible path
		<div
			className={`dropzone${dragging ? " is-dragging" : ""}${compact ? " is-compact" : ""}`}
			onDragOver={(event) => {
				event.preventDefault();
				setDragging(true);
			}}
			onDragLeave={() => setDragging(false)}
			onDrop={onDrop}
		>
			<Upload aria-hidden="true" />
			<div className="dropzone-copy">
				<strong>Drop papers, notes, or slides</strong>
				<span>PDF, PowerPoint, Word, or Markdown · added to this course</span>
			</div>
			<button
				type="button"
				className="btn btn-primary"
				onClick={() => inputRef.current?.click()}
			>
				Add files
			</button>
			<input
				ref={inputRef}
				type="file"
				multiple
				hidden
				accept={MATERIAL_ACCEPT}
				aria-label="Add files"
				onChange={(event) => {
					const files = Array.from(event.target.files ?? []);
					event.target.value = "";
					void library.addFiles(files, true);
				}}
			/>
		</div>
	);
}

export function UploadList({ library }: { library: Library }) {
	if (library.uploads.length === 0) return null;
	return (
		<ul className="upload-list" aria-label="Files being added">
			{library.uploads.map((item) => (
				<UploadRow key={item.id} item={item} library={library} />
			))}
		</ul>
	);
}

function UploadRow({ item, library }: { item: UploadItem; library: Library }) {
	const label =
		item.state === "uploading"
			? "Reading…"
			: item.state === "including"
				? "Adding to course…"
				: item.state === "needs-models"
					? "Needs PDF support"
					: "Couldn't add";
	const working = item.state === "uploading" || item.state === "including";
	return (
		<li className={`upload-row is-${item.state}`}>
			{working ? (
				<Loader2 className="spin" aria-hidden="true" />
			) : (
				<AlertCircle aria-hidden="true" />
			)}
			<span className="upload-name">{item.name}</span>
			<span className="upload-state">{label}</span>
			{item.error ? <span className="upload-error">{item.error}</span> : null}
			{!working ? (
				<span className="upload-actions">
					<button
						type="button"
						className="btn btn-small"
						onClick={() => library.retryUpload(item.id)}
					>
						<RotateCcw aria-hidden="true" />
						{item.state === "needs-models" ? "Set up PDF support" : "Try again"}
					</button>
					<button
						type="button"
						className="icon-btn is-small"
						aria-label={`Dismiss ${item.name}`}
						onClick={() => library.dismissUpload(item.id)}
					>
						<X aria-hidden="true" />
					</button>
				</span>
			) : null}
		</li>
	);
}
