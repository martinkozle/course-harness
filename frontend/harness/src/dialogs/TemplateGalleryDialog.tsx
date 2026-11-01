import { Upload } from "lucide-react";
import { type ChangeEvent, type FormEvent, useRef, useState } from "react";

import { responseError } from "../api";
import type {
	CoursePlan,
	TemplateProfile,
	TemplateProfileSummary,
} from "../models";
import { withBuiltinTemplate } from "../TemplatesView";
import { Dialog, errorMessage, Notice } from "../ui";

const BUILTIN_ID = "_builtin-default";

/** Suggest a readable display name from an uploaded template filename. */
export function suggestTemplateName(filename: string): string {
	const stem = filename.replace(/\.(pptx|potx)$/i, "").trim();
	const compact = stem.replace(/[\s_-]/g, "");
	const machineLike =
		compact.length === 0 ||
		/^[0-9a-f]{12,}$/i.test(compact) ||
		/^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i.test(
			stem,
		) ||
		(compact.length > 16 &&
			(compact.match(/\d/g)?.length ?? 0) / compact.length > 0.3);
	if (machineLike) return "Imported template";
	const words = stem.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
	const readable = words.charAt(0).toUpperCase() + words.slice(1);
	return readable.length > 60
		? `${readable.slice(0, 57).trimEnd()}…`
		: readable;
}

export function TemplateGalleryDialog({
	course,
	templates,
	onTemplatesChange,
	onApplied,
	onManage,
	onClose,
}: {
	course: CoursePlan;
	templates: TemplateProfileSummary[];
	onTemplatesChange: (templates: TemplateProfileSummary[]) => void;
	onApplied: () => Promise<void>;
	onManage: () => void;
	onClose: () => void;
}) {
	const pinnedId = course.template_profile_id ?? BUILTIN_ID;
	const [selectedId, setSelectedId] = useState(pinnedId);
	const [applying, setApplying] = useState(false);
	const [importing, setImporting] = useState(false);
	const [naming, setNaming] = useState<TemplateProfile | null>(null);
	const [nameValue, setNameValue] = useState("");
	const [savingName, setSavingName] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const fileRef = useRef<HTMLInputElement>(null);
	const all = withBuiltinTemplate(templates);
	const selected = all.find((template) => template.id === selectedId);

	async function refreshList() {
		const response = await fetch("/api/templates");
		if (!response.ok) throw new Error(await responseError(response));
		onTemplatesChange((await response.json()) as TemplateProfileSummary[]);
	}

	async function apply() {
		if (!selected) return;
		setApplying(true);
		setError(null);
		try {
			const builtin = selected.id === BUILTIN_ID;
			const response = await fetch("/api/course/profile", {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					template_profile_id: builtin ? null : selected.id,
					template_profile_version: builtin ? null : selected.version,
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			await onApplied();
			onClose();
		} catch (caught) {
			setError(errorMessage(caught, "The template could not be applied."));
		} finally {
			setApplying(false);
		}
	}

	async function importTemplate(event: ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		event.target.value = "";
		if (!file) return;
		setImporting(true);
		setError(null);
		try {
			const form = new FormData();
			form.append("file", file);
			const response = await fetch("/api/templates/upload", {
				method: "POST",
				body: form,
			});
			if (!response.ok) throw new Error(await responseError(response));
			const body = (await response.json()) as { profile: TemplateProfile };
			await refreshList();
			setSelectedId(body.profile.id);
			setNaming(body.profile);
			setNameValue(suggestTemplateName(file.name));
		} catch (caught) {
			setError(errorMessage(caught, "The template could not be imported."));
		} finally {
			setImporting(false);
		}
	}

	async function saveName(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		if (!naming) return;
		const name = nameValue.trim();
		if (!name) return;
		setSavingName(true);
		setError(null);
		try {
			if (name !== naming.name) {
				const response = await fetch(`/api/templates/${naming.id}`, {
					method: "PATCH",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ name }),
				});
				if (!response.ok) throw new Error(await responseError(response));
				await refreshList();
			}
			setNaming(null);
		} catch (caught) {
			setError(errorMessage(caught, "The name could not be saved."));
		} finally {
			setSavingName(false);
		}
	}

	return (
		<Dialog
			title="Choose a template"
			onClose={onClose}
			size="wide"
			dismissible={!applying}
			footer={
				<>
					<button
						type="button"
						className="btn btn-quiet"
						onClick={() => {
							onClose();
							onManage();
						}}
					>
						Manage templates
					</button>
					<span className="spacer" />
					<button
						type="button"
						className="btn"
						onClick={onClose}
						disabled={applying}
					>
						Cancel
					</button>
					<button
						type="button"
						className="btn btn-primary"
						disabled={applying || naming !== null || selectedId === pinnedId}
						onClick={() => void apply()}
					>
						{applying ? "Applying…" : "Use template"}
					</button>
				</>
			}
		>
			<p className="meta">
				The template sets how exported PowerPoint files look. Using one pins its
				current version to this course.
			</p>

			{naming ? (
				<form className="template-naming panel panel-pad" onSubmit={saveName}>
					<div className="field">
						<label htmlFor="template-display-name">Name this template</label>
						<input
							id="template-display-name"
							value={nameValue}
							maxLength={200}
							required
							autoFocus
							onChange={(event) => setNameValue(event.target.value)}
						/>
						<small>Shown in menus instead of the file name.</small>
					</div>
					<div className="form-actions">
						<button
							type="submit"
							className="btn btn-primary btn-small"
							disabled={savingName || !nameValue.trim()}
						>
							{savingName ? "Saving…" : "Save name"}
						</button>
					</div>
				</form>
			) : null}

			<fieldset className="template-gallery">
				<legend className="visually-hidden">Templates</legend>
				{all.map((template) => {
					const isBuiltin = template.id === BUILTIN_ID;
					return (
						<label
							key={template.id}
							className="template-card"
							data-selected={template.id === selectedId}
							title={template.name}
						>
							<input
								type="radio"
								name="template-choice"
								className="visually-hidden"
								value={template.id}
								checked={template.id === selectedId}
								onChange={() => setSelectedId(template.id)}
							/>
							<span
								className={`template-thumb${isBuiltin ? " is-builtin" : ""}`}
								aria-hidden="true"
							>
								<span className="template-thumb-title" />
								<span className="template-thumb-line" />
								<span className="template-thumb-line is-short" />
							</span>
							<span className="template-card-name">{template.name}</span>
							<span className="template-card-meta">
								{isBuiltin
									? "Always available"
									: `v${template.version} · ${template.mapped_layouts} layouts`}
							</span>
							{template.id === pinnedId ? (
								<span className="template-card-badge">In use</span>
							) : null}
						</label>
					);
				})}
				<button
					type="button"
					className="template-card template-card-import"
					disabled={importing}
					onClick={() => fileRef.current?.click()}
				>
					<Upload aria-hidden="true" />
					<span className="template-card-name">
						{importing ? "Inspecting…" : "Import template…"}
					</span>
					<span className="template-card-meta">.pptx or .potx</span>
				</button>
			</fieldset>
			<input
				ref={fileRef}
				type="file"
				hidden
				aria-label="Import template"
				accept=".pptx,.potx,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.presentationml.template"
				onChange={(event) => void importTemplate(event)}
			/>
			{error ? <Notice tone="error">{error}</Notice> : null}
		</Dialog>
	);
}
