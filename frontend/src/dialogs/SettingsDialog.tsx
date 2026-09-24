import {
	Bot,
	Check,
	FileText,
	Folder,
	FolderOpen,
	LayoutTemplate,
	Plug,
	Stethoscope,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { responseError } from "../api";
import { ConnectorSettings } from "../ConnectorSettings";
import type {
	ModelCatalog,
	TemplateProfileSummary,
	Workspace,
	WorkspaceEntry,
} from "../models";
import { ModelSettings } from "../ProviderSetup";
import { RuntimeDiagnosticsPanel } from "../RuntimeDiagnostics";
import { TemplateSettings } from "../TemplatesView";
import { Dialog, errorMessage, Notice, Tabs } from "../ui";
import type { Library } from "../useLibrary";

export type SettingsSection =
	| "models"
	| "connectors"
	| "templates"
	| "workspace"
	| "diagnostics";

const sections: { id: SettingsSection; label: string; icon: ReactNode }[] = [
	{ id: "models", label: "Models", icon: <Bot aria-hidden="true" /> },
	{
		id: "connectors",
		label: "Connectors",
		icon: <Plug aria-hidden="true" />,
	},
	{
		id: "templates",
		label: "Templates",
		icon: <LayoutTemplate aria-hidden="true" />,
	},
	{ id: "workspace", label: "Workspace", icon: <Folder aria-hidden="true" /> },
	{
		id: "diagnostics",
		label: "Diagnostics",
		icon: <Stethoscope aria-hidden="true" />,
	},
];

export function SettingsDialog({
	section,
	onSection,
	workspace,
	catalog,
	onCatalogChange,
	templates,
	onTemplatesChange,
	library,
	onClose,
}: {
	section: SettingsSection;
	onSection: (section: SettingsSection) => void;
	workspace: Workspace;
	catalog: ModelCatalog;
	onCatalogChange: (catalog: ModelCatalog) => void;
	templates: TemplateProfileSummary[];
	onTemplatesChange: (templates: TemplateProfileSummary[]) => void;
	library: Library;
	onClose: () => void;
}) {
	const current = sections.find((item) => item.id === section) ?? sections[0];
	return (
		<Dialog title="Settings" onClose={onClose} size="wide" tall>
			<div className="settings-layout">
				<nav className="settings-nav" aria-label="Settings sections">
					<ul>
						{sections.map((item) => (
							<li key={item.id}>
								<button
									type="button"
									className="settings-nav-item"
									aria-current={item.id === section ? "page" : undefined}
									onClick={() => onSection(item.id)}
								>
									{item.icon}
									{item.label}
								</button>
							</li>
						))}
					</ul>
				</nav>
				<div className="settings-tabs">
					<Tabs
						label="Settings sections"
						tabs={sections.map(({ id, label }) => ({ id, label }))}
						value={section}
						onChange={onSection}
					/>
				</div>
				<section
					className="settings-content"
					aria-labelledby="settings-section-heading"
				>
					<h2 id="settings-section-heading" className="settings-heading">
						{current.label}
					</h2>
					{section === "models" ? (
						<ModelSettings
							catalog={catalog}
							onCatalogChange={onCatalogChange}
						/>
					) : section === "connectors" ? (
						<ConnectorSettings />
					) : section === "templates" ? (
						<TemplateSettings
							templates={templates}
							catalog={catalog}
							onTemplatesChange={onTemplatesChange}
						/>
					) : section === "workspace" ? (
						<WorkspaceDetails workspace={workspace} />
					) : (
						<Diagnostics library={library} />
					)}
				</section>
			</div>
		</Dialog>
	);
}

function WorkspaceDetails({ workspace }: { workspace: Workspace }) {
	const [files, setFiles] = useState<WorkspaceEntry[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch("/api/workspace/files", {
					signal: controller.signal,
				});
				if (!response.ok) throw new Error(await responseError(response));
				setFiles((await response.json()) as WorkspaceEntry[]);
			} catch (caught) {
				if (caught instanceof DOMException && caught.name === "AbortError")
					return;
				setError(errorMessage(caught, "Course files could not be listed."));
			}
		})();
		return () => controller.abort();
	}, []);

	return (
		<div className="settings-workspace">
			<div className="settings-block">
				<h3>Course folder</h3>
				<p className="settings-folder">
					<FolderOpen aria-hidden="true" />
					<strong>{workspace.name}</strong>
				</p>
				<p className="mono settings-path">{workspace.path}</p>
				<p className="meta">
					Your Course is saved here as readable files. Caches, chat history, and
					credentials are kept elsewhere on this computer.
				</p>
			</div>
			<section className="settings-block" aria-label="Course files">
				<h3>Course files</h3>
				{error ? <Notice tone="error">{error}</Notice> : null}
				{files === null && !error ? (
					<p className="meta" role="status">
						Listing files…
					</p>
				) : null}
				{files && files.length === 0 ? (
					<p className="meta">This Course folder has no files yet.</p>
				) : null}
				{files && files.length > 0 ? (
					<ul className="settings-files">
						{files.map((entry) => (
							<li key={entry.path}>
								{entry.kind === "directory" ? (
									<Folder aria-hidden="true" />
								) : (
									<FileText aria-hidden="true" />
								)}
								<span className="mono">{entry.path}</span>
							</li>
						))}
					</ul>
				) : null}
			</section>
		</div>
	);
}

function Diagnostics({ library }: { library: Library }) {
	return (
		<div className="settings-diagnostics-wrap">
			<section
				className="settings-block"
				aria-labelledby="document-processing-heading"
			>
				<h3 id="document-processing-heading">Document processing</h3>
				<div className="settings-control">
					<div>
						<strong>PDF support</strong>
						<p className="meta">
							Local models read layout, tables, and scanned text in PDFs.
						</p>
					</div>
					{library.modelStatus?.ready ? (
						<span className="state is-success">
							<Check aria-hidden="true" />
							PDF support is ready
						</span>
					) : (
						<button
							type="button"
							className="btn btn-small"
							onClick={library.requestModelDownload}
						>
							Download PDF models
						</button>
					)}
				</div>
				<div className="settings-control">
					<div>
						<strong>Search index</strong>
						<p className="meta">
							Rebuild it if search results look incomplete or out of date.
						</p>
					</div>
					<button
						type="button"
						className="btn btn-small"
						disabled={library.regenerating}
						onClick={() => void library.regenerateIndex()}
					>
						{library.regenerating ? "Rebuilding…" : "Rebuild search index"}
					</button>
				</div>
				{library.indexError ? (
					<Notice tone="error">{library.indexError}</Notice>
				) : null}
			</section>
			<RuntimeDiagnosticsPanel />
		</div>
	);
}
