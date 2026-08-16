import { type ChangeEvent, type FormEvent, useState } from "react";
import { responseError } from "./api";
import type {
	CalibrationSlide,
	ModelCatalog,
	TemplateInspection,
	TemplateLayoutInspection,
	TemplateProfile,
	TemplateProfileSummary,
	TemplateValidationFinding,
} from "./models";

type TemplatesViewProps = {
	templates: TemplateProfileSummary[];
	catalog: ModelCatalog;
	onTemplatesChange: (templates: TemplateProfileSummary[]) => void;
};

function snakeToTitle(value: string): string {
	return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function confidenceColor(confidence: number): string {
	if (confidence >= 0.8) return "var(--color-success, #22c55e)";
	if (confidence >= 0.5) return "var(--color-warning, #eab308)";
	return "var(--color-error, #ef4444)";
}

function confidenceLabel(confidence: number): string {
	if (confidence >= 0.8) return "high";
	if (confidence >= 0.5) return "medium";
	return "low";
}

export function TemplatesView({
	templates,
	catalog,
	onTemplatesChange,
}: TemplatesViewProps) {
	const [uploading, setUploading] = useState(false);
	const [uploadError, setUploadError] = useState<string | null>(null);
	const [selectedProfile, setSelectedProfile] =
		useState<TemplateProfile | null>(null);
	const [inspection, setInspection] = useState<TemplateInspection | null>(null);
	const [selectedLayouts, setSelectedLayouts] = useState<Map<string, number>>(
		new Map(),
	);
	const [saving, setSaving] = useState(false);
	const [calibrating, setCalibrating] = useState(false);
	const [calibrationSlides, setCalibrationSlides] = useState<
		CalibrationSlide[]
	>([]);
	const [calibrationError, setCalibrationError] = useState<string | null>(null);
	const [renaming, setRenaming] = useState(false);
	const [renameValue, setRenameValue] = useState("");
	const [renameSaving, setRenameSaving] = useState(false);
	const [renameError, setRenameError] = useState<string | null>(null);
	const [suggestionConsent, setSuggestionConsent] = useState(false);
	const [suggesting, setSuggesting] = useState(false);
	const [suggestionError, setSuggestionError] = useState<string | null>(null);
	const [validating, setValidating] = useState(false);
	const [validationFindings, setValidationFindings] = useState<
		TemplateValidationFinding[] | null
	>(null);
	const [mappingsDirty, setMappingsDirty] = useState(false);

	const selectedPreset = catalog.model_presets.find(
		(preset) => preset.id === catalog.selected_model_id,
	);
	const selectedAccount = catalog.provider_accounts.find(
		(account) => account.id === selectedPreset?.provider_account_id,
	);

	function resetAssistance() {
		setSuggestionConsent(false);
		setSuggestionError(null);
		setValidationFindings(null);
		setMappingsDirty(false);
	}

	async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		if (!file) return;
		setUploading(true);
		setUploadError(null);
		setSelectedProfile(null);
		setInspection(null);
		setRenaming(false);
		setRenameError(null);
		setCalibrationSlides([]);
		setSelectedLayouts(new Map());
		resetAssistance();
		try {
			const form = new FormData();
			form.append("file", file);
			const response = await fetch("/api/templates/upload", {
				method: "POST",
				body: form,
			});
			if (!response.ok) throw new Error(await responseError(response));
			const body = (await response.json()) as {
				profile: TemplateProfile;
				inspection: Record<string, unknown>;
			};
			setSelectedProfile(body.profile);
			setInspection(body.inspection as unknown as TemplateInspection);
			setRenameValue(body.profile.name);
			setSelectedLayouts(
				new Map(
					body.profile.layouts.map((mapping) => [
						mapping.semantic_layout,
						mapping.template_layout_index,
					]),
				),
			);
			const listResp = await fetch("/api/templates");
			if (listResp.ok) {
				onTemplatesChange((await listResp.json()) as TemplateProfileSummary[]);
			}
		} catch (caught) {
			setUploadError(
				caught instanceof Error ? caught.message : "Upload failed.",
			);
		} finally {
			setUploading(false);
			event.target.value = "";
		}
	}

	async function handleSelect(profileId: string) {
		setUploadError(null);
		if (profileId === "_builtin-default") {
			setSelectedProfile(null);
			setInspection(null);
			setRenaming(false);
			setRenameError(null);
			setCalibrationSlides([]);
			setSelectedLayouts(new Map());
			resetAssistance();
			return;
		}
		try {
			const [response, inspectionResponse] = await Promise.all([
				fetch(`/api/templates/${profileId}`),
				fetch(`/api/templates/${profileId}/inspection`),
			]);
			if (!response.ok) throw new Error(await responseError(response));
			if (!inspectionResponse.ok) {
				throw new Error(await responseError(inspectionResponse));
			}
			const profile = (await response.json()) as TemplateProfile;
			setSelectedProfile(profile);
			setInspection((await inspectionResponse.json()) as TemplateInspection);
			setRenameValue(profile.name);
			setRenaming(false);
			setRenameError(null);
			setCalibrationSlides([]);
			setCalibrationError(null);
			resetAssistance();
			const layoutMap = new Map<string, number>();
			for (const m of profile.layouts) {
				layoutMap.set(m.semantic_layout, m.template_layout_index);
			}
			setSelectedLayouts(layoutMap);
		} catch (caught) {
			setSelectedProfile(null);
			setInspection(null);
			setUploadError(
				caught instanceof Error ? caught.message : "Could not load template.",
			);
		}
	}

	async function handleRename(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		if (!selectedProfile) return;
		const name = renameValue.trim();
		if (!name) {
			setRenameError("Enter a template name.");
			return;
		}
		setRenameSaving(true);
		setRenameError(null);
		try {
			const response = await fetch(`/api/templates/${selectedProfile.id}`, {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ name }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const updated = (await response.json()) as TemplateProfile;
			setSelectedProfile(updated);
			setRenameValue(updated.name);
			setRenaming(false);
			onTemplatesChange(
				templates.map((template) =>
					template.id === updated.id
						? { ...template, name: updated.name }
						: template,
				),
			);
		} catch (caught) {
			setRenameError(
				caught instanceof Error ? caught.message : "Rename failed.",
			);
		} finally {
			setRenameSaving(false);
		}
	}

	function handleMappingChange(semantic: string, index: number) {
		setValidationFindings(null);
		setMappingsDirty(true);
		setSelectedLayouts((prev) => {
			const next = new Map(prev);
			next.set(semantic, index);
			return next;
		});
	}

	async function handleSaveMappings() {
		if (!selectedProfile) return;
		setSaving(true);
		setValidationFindings(null);
		try {
			const mappings = Array.from(selectedLayouts.entries()).map(
				([semantic_layout, template_layout_index]) => ({
					semantic_layout,
					template_layout_index,
				}),
			);
			const response = await fetch(`/api/templates/${selectedProfile.id}`, {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ mappings }),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const updated = (await response.json()) as TemplateProfile;
			setSelectedProfile(updated);
			setMappingsDirty(false);
			setCalibrationSlides([]);
			setCalibrationError(null);
			const listResp = await fetch("/api/templates");
			if (listResp.ok) {
				onTemplatesChange((await listResp.json()) as TemplateProfileSummary[]);
			}
		} catch (caught) {
			setUploadError(caught instanceof Error ? caught.message : "Save failed.");
		} finally {
			setSaving(false);
		}
	}

	async function handleSuggestMappings() {
		if (!selectedProfile || !suggestionConsent || !selectedPreset) return;
		setSuggesting(true);
		setSuggestionError(null);
		setValidationFindings(null);
		try {
			const response = await fetch(
				`/api/templates/${selectedProfile.id}/suggest-mappings`,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ consent: true }),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			const body = (await response.json()) as {
				mappings: TemplateProfile["layouts"];
			};
			setSelectedProfile({ ...selectedProfile, layouts: body.mappings });
			setSelectedLayouts(
				new Map(
					body.mappings.map((mapping) => [
						mapping.semantic_layout,
						mapping.template_layout_index,
					]),
				),
			);
			setMappingsDirty(true);
		} catch (caught) {
			setSuggestionError(
				caught instanceof Error ? caught.message : "AI suggestion failed.",
			);
		} finally {
			setSuggesting(false);
		}
	}

	async function handleValidateMappings() {
		if (!selectedProfile) return;
		setValidating(true);
		setSuggestionError(null);
		try {
			const response = await fetch(
				`/api/templates/${selectedProfile.id}/validate`,
				{ method: "POST" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			setValidationFindings(
				(await response.json()) as TemplateValidationFinding[],
			);
		} catch (caught) {
			setSuggestionError(
				caught instanceof Error ? caught.message : "Validation failed.",
			);
		} finally {
			setValidating(false);
		}
	}

	async function handleCalibrate() {
		if (!selectedProfile) return;
		setCalibrating(true);
		setCalibrationError(null);
		setCalibrationSlides([]);
		try {
			const response = await fetch(
				`/api/templates/${selectedProfile.id}/calibrate`,
				{ method: "POST" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			setCalibrationSlides((await response.json()) as CalibrationSlide[]);
		} catch (caught) {
			setCalibrationError(
				caught instanceof Error ? caught.message : "Calibration failed.",
			);
		} finally {
			setCalibrating(false);
		}
	}

	async function handleDelete() {
		if (!selectedProfile) return;
		if (
			!window.confirm(`Delete the template profile “${selectedProfile.name}”?`)
		) {
			return;
		}
		try {
			const response = await fetch(`/api/templates/${selectedProfile.id}`, {
				method: "DELETE",
			});
			if (!response.ok) throw new Error(await responseError(response));
			setSelectedProfile(null);
			setCalibrationSlides([]);
			const listResp = await fetch("/api/templates");
			if (listResp.ok) {
				onTemplatesChange((await listResp.json()) as TemplateProfileSummary[]);
			}
		} catch (caught) {
			setUploadError(
				caught instanceof Error ? caught.message : "Delete failed.",
			);
		}
	}

	function layoutInspection(
		index: number,
	): TemplateLayoutInspection | undefined {
		return inspection?.layouts.find((layout) => layout.index === index);
	}

	function layoutOptionLabel(index: number): string {
		const layout = layoutInspection(index);
		if (!layout) return `${index}: Layout ${index}`;
		const slotLabel =
			layout.placeholders.length === 1 ? "placeholder" : "placeholders";
		return `${index}: ${layout.name || `Layout ${index}`} — ${layout.placeholders.length} ${slotLabel}`;
	}

	const layoutCount = selectedProfile?.slide_count ?? 0;
	const layoutIndices = Array.from({ length: layoutCount }, (_, i) => i);

	const allTemplates = templates.some(
		(template) => template.id === "_builtin-default",
	)
		? templates
		: [
				{
					id: "_builtin-default",
					name: "Built-in default",
					version: 1,
					slide_count: 11,
					mapped_layouts: 9,
				},
				...templates,
			];

	return (
		<main
			className="page-main templates-main"
			aria-labelledby="templates-heading"
		>
			<header className="page-heading templates-heading">
				<p className="eyebrow">Presentation design</p>
				<h1 id="templates-heading">Templates</h1>
				<p className="lede">
					Import and configure PowerPoint templates for course export.
				</p>
			</header>

			{uploadError ? (
				<p className="notice error-notice" role="alert">
					{uploadError}
				</p>
			) : null}

			<div className="template-selector">
				<label htmlFor="template-select">Inspect template</label>
				<select
					id="template-select"
					value={selectedProfile?.id ?? "_builtin-default"}
					onChange={(e) => void handleSelect(e.target.value)}
				>
					{allTemplates.map((t) => (
						<option key={t.id} value={t.id}>
							{t.name} {t.id !== "_builtin-default" ? `(v${t.version})` : ""}
						</option>
					))}
				</select>
			</div>

			{selectedProfile && selectedProfile.id !== "_builtin-default" ? (
				<div className="profile-detail">
					<div className="detail-header">
						<div className="template-identity">
							{renaming ? (
								<form className="template-rename-form" onSubmit={handleRename}>
									<label htmlFor="template-profile-name">Template name</label>
									<input
										id="template-profile-name"
										value={renameValue}
										onChange={(event) => setRenameValue(event.target.value)}
										maxLength={200}
										disabled={renameSaving}
									/>
									<button
										className="primary-action compact-action"
										type="submit"
										disabled={renameSaving || !renameValue.trim()}
									>
										{renameSaving ? "Saving…" : "Save"}
									</button>
									<button
										className="quiet-action compact-action"
										type="button"
										disabled={renameSaving}
										onClick={() => {
											setRenameValue(selectedProfile.name);
											setRenameError(null);
											setRenaming(false);
										}}
									>
										Cancel
									</button>
								</form>
							) : (
								<div className="template-name-row">
									<h3>{selectedProfile.name}</h3>
									<button
										className="quiet-action compact-action"
										type="button"
										onClick={() => {
											setRenameValue(selectedProfile.name);
											setRenameError(null);
											setRenaming(true);
										}}
									>
										Rename
									</button>
								</div>
							)}
							{renameError ? (
								<p className="template-rename-error" role="alert">
									{renameError}
								</p>
							) : null}
							<p className="detail-meta">
								{selectedProfile.template_filename} •{" "}
								{selectedProfile.slide_count} slide layouts • Version{" "}
								{selectedProfile.version}
							</p>
						</div>
						<div className="detail-actions">
							<button
								className="quiet-action"
								type="button"
								disabled={calibrating}
								onClick={() => void handleCalibrate()}
							>
								{calibrating ? "Rendering…" : "Calibration preview"}
							</button>
							<button
								className="quiet-action destructive-action"
								type="button"
								onClick={() => void handleDelete()}
							>
								Delete profile
							</button>
						</div>
					</div>

					{calibrationError ? (
						<p className="notice error-notice" role="alert">
							{calibrationError}
						</p>
					) : null}

					{calibrationSlides.length > 0 ? (
						<div className="calibration-grid">
							{calibrationSlides.map((slide) => (
								<div key={slide.semantic_layout} className="calibration-slide">
									<span className="calibration-label">
										{snakeToTitle(slide.semantic_layout)}
									</span>
									{slide.image_url ? (
										<img
											src={slide.image_url}
											alt={`${slide.semantic_layout} calibration`}
											className="calibration-image"
										/>
									) : (
										<div className="calibration-placeholder">No preview</div>
									)}
								</div>
							))}
						</div>
					) : null}

					<h4 className="mapping-heading">Layout mappings</h4>
					<p className="section-note">
						Choose the concrete template layout and review its named slots. Use
						calibration previews above to confirm the visual result.
					</p>
					<div className="mapping-table-scroll">
						<table className="mapping-table">
							<thead>
								<tr>
									<th>Semantic layout</th>
									<th>Template layout</th>
									<th>Confidence</th>
								</tr>
							</thead>
							<tbody>
								{selectedProfile.layouts.map((m) => (
									<tr key={m.semantic_layout}>
										<td className="layout-semantic">
											{snakeToTitle(m.semantic_layout)}
										</td>
										<td>
											<select
												value={
													selectedLayouts.get(m.semantic_layout) ??
													m.template_layout_index
												}
												onChange={(e) =>
													handleMappingChange(
														m.semantic_layout,
														Number(e.target.value),
													)
												}
												aria-label={`Template layout for ${m.semantic_layout}`}
											>
												{layoutIndices.map((i) => (
													<option key={i} value={i}>
														{layoutOptionLabel(i)}
													</option>
												))}
											</select>
											{layoutInspection(
												selectedLayouts.get(m.semantic_layout) ??
													m.template_layout_index,
											) ? (
												<small className="layout-slot-summary">
													Slots:{" "}
													{layoutInspection(
														selectedLayouts.get(m.semantic_layout) ??
															m.template_layout_index,
													)
														?.placeholders.map((slot) => slot.name)
														.join(", ") || "none"}
												</small>
											) : null}
										</td>
										<td>
											<span
												className="confidence-badge"
												style={{
													backgroundColor: confidenceColor(m.confidence),
												}}
											>
												{confidenceLabel(m.confidence)} (
												{(m.confidence * 100).toFixed(0)}%)
											</span>
											<small className="mapping-rationale">{m.rationale}</small>
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>

					<div className="mapping-actions">
						<button
							className="primary-action"
							type="button"
							disabled={saving}
							onClick={() => void handleSaveMappings()}
						>
							{saving ? "Saving…" : "Save mapping corrections"}
						</button>
						<button
							className="quiet-action"
							type="button"
							disabled={validating || saving || mappingsDirty}
							title={
								mappingsDirty
									? "Save mapping corrections before checking"
									: undefined
							}
							onClick={() => void handleValidateMappings()}
						>
							{validating ? "Checking…" : "Check mappings"}
						</button>
					</div>

					{validationFindings ? (
						<section className="mapping-validation" aria-live="polite">
							<h4>Mapping check</h4>
							{validationFindings.length === 0 ? (
								<p className="notice success-notice">
									Mappings are ready for export.
								</p>
							) : (
								<ul>
									{validationFindings.map((finding) => (
										<li key={`${finding.level}-${finding.message}`}>
											<strong>{snakeToTitle(finding.level)}:</strong>{" "}
											{finding.message}
										</li>
									))}
								</ul>
							)}
						</section>
					) : null}

					<section
						className="mapping-assistance"
						aria-labelledby="mapping-assistance-heading"
					>
						<h4 id="mapping-assistance-heading">Optional AI assistance</h4>
						{selectedPreset && selectedAccount ? (
							<>
								<p>
									Template metadata will leave this device and be sent to{" "}
									<strong>{selectedAccount.name}</strong> using{" "}
									<strong>{selectedPreset.name}</strong>. This includes layout
									and placeholder names, identifiers, and types. The PowerPoint
									file, slide content, and calibration images stay on this
									device.
								</p>
								<label className="consent-control">
									<input
										type="checkbox"
										checked={suggestionConsent}
										onChange={(event) =>
											setSuggestionConsent(event.target.checked)
										}
									/>
									I agree to send this template metadata for this suggestion.
								</label>
								<button
									className="quiet-action"
									type="button"
									disabled={!suggestionConsent || suggesting}
									onClick={() => void handleSuggestMappings()}
								>
									{suggesting ? "Asking model…" : "Improve mappings with AI"}
								</button>
							</>
						) : (
							<p>
								Configure and select a Model Preset to request AI suggestions.
							</p>
						)}
						{suggestionError ? (
							<p className="notice error-notice" role="alert">
								{suggestionError}
							</p>
						) : null}
					</section>
				</div>
			) : (
				<div className="profile-detail">
					<div className="detail-header">
						<div>
							<h3>Built-in default</h3>
							<p className="detail-meta">
								The default python-pptx Office Theme template. Always available.
							</p>
						</div>
					</div>
					<p className="section-note">
						The built-in template has predefined mappings. Import a custom
						template to customize your course exports.
					</p>
				</div>
			)}

			<div className="upload-section">
				<label className="upload-label" htmlFor="template-upload">
					{uploading
						? "Inspecting template…"
						: "Import a template (.pptx or .potx)"}
				</label>
				<input
					id="template-upload"
					type="file"
					accept=".pptx,.potx,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.presentationml.template"
					disabled={uploading}
					onChange={handleUpload}
					className="file-input"
				/>
			</div>
		</main>
	);
}
