import {
	AlertTriangle,
	Check,
	ChevronRight,
	Images,
	Pencil,
	Trash2,
	Upload,
} from "lucide-react";
import { type ChangeEvent, type FormEvent, useState } from "react";

import { responseError } from "./api";
import type {
	CalibrationSlide,
	ModelCatalog,
	SlideLayout,
	TemplateInspection,
	TemplateLayoutInspection,
	TemplateProfile,
	TemplateProfileSummary,
	TemplateValidationFinding,
} from "./models";
import { ConfirmDialog, Notice } from "./ui";

const BUILTIN_ID = "_builtin-default";
const REVIEW_THRESHOLD = 0.8;

function snakeToTitle(value: string): string {
	return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function confidenceLabel(confidence: number): string {
	if (confidence >= REVIEW_THRESHOLD) return "Confident match";
	if (confidence >= 0.5) return "Likely match";
	return "Needs review";
}

export function withBuiltinTemplate(
	templates: TemplateProfileSummary[],
): TemplateProfileSummary[] {
	return templates.some((template) => template.id === BUILTIN_ID)
		? templates
		: [
				{
					id: BUILTIN_ID,
					name: "Built-in default",
					version: 1,
					slide_count: 11,
					mapped_layouts: 9,
				},
				...templates,
			];
}

/** Settings → Templates: import, inspect, rename, and correct Template Profiles. */
export function TemplateSettings({
	templates,
	catalog,
	onTemplatesChange,
}: {
	templates: TemplateProfileSummary[];
	catalog: ModelCatalog;
	onTemplatesChange: (templates: TemplateProfileSummary[]) => void;
}) {
	const [selectedId, setSelectedId] = useState<string>(BUILTIN_ID);
	const [uploading, setUploading] = useState(false);
	const [uploadError, setUploadError] = useState<string | null>(null);
	const [selectedProfile, setSelectedProfile] =
		useState<TemplateProfile | null>(null);
	const [inspection, setInspection] = useState<TemplateInspection | null>(null);
	const [selectedLayouts, setSelectedLayouts] = useState<
		Map<SlideLayout, number>
	>(new Map());
	const [openMappings, setOpenMappings] = useState<Set<SlideLayout>>(new Set());
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
	const [suggestionNotice, setSuggestionNotice] = useState<string | null>(null);
	const [validating, setValidating] = useState(false);
	const [validationFindings, setValidationFindings] = useState<
		TemplateValidationFinding[] | null
	>(null);
	const [mappingsDirty, setMappingsDirty] = useState(false);
	const [confirmingDelete, setConfirmingDelete] = useState(false);
	const [deleting, setDeleting] = useState(false);
	const [deleteError, setDeleteError] = useState<string | null>(null);

	const selectedPreset = catalog.model_presets.find(
		(preset) => preset.id === catalog.selected_model_id,
	);
	const selectedAccount = catalog.provider_accounts.find(
		(account) => account.id === selectedPreset?.provider_account_id,
	);
	const allTemplates = withBuiltinTemplate(templates);

	function resetDetail() {
		setRenaming(false);
		setRenameError(null);
		setCalibrationSlides([]);
		setCalibrationError(null);
		setSuggestionConsent(false);
		setSuggestionError(null);
		setSuggestionNotice(null);
		setValidationFindings(null);
		setMappingsDirty(false);
	}

	function showProfile(profile: TemplateProfile, detail: TemplateInspection) {
		setSelectedProfile(profile);
		setInspection(detail);
		setSelectedId(profile.id);
		setRenameValue(profile.name);
		setSelectedLayouts(
			new Map(
				profile.layouts.map((mapping) => [
					mapping.semantic_layout,
					mapping.template_layout_index,
				]),
			),
		);
		setOpenMappings(
			new Set(
				profile.layouts
					.filter((mapping) => mapping.confidence < REVIEW_THRESHOLD)
					.map((mapping) => mapping.semantic_layout),
			),
		);
	}

	async function refreshList() {
		const listResp = await fetch("/api/templates");
		if (listResp.ok) {
			onTemplatesChange((await listResp.json()) as TemplateProfileSummary[]);
		}
	}

	async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		if (!file) return;
		setUploading(true);
		setUploadError(null);
		resetDetail();
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
			showProfile(
				body.profile,
				body.inspection as unknown as TemplateInspection,
			);
			await refreshList();
		} catch (caught) {
			setUploadError(
				caught instanceof Error
					? caught.message
					: "The template could not be imported.",
			);
		} finally {
			setUploading(false);
			event.target.value = "";
		}
	}

	async function handleSelect(profileId: string) {
		setUploadError(null);
		resetDetail();
		if (profileId === BUILTIN_ID) {
			setSelectedId(BUILTIN_ID);
			setSelectedProfile(null);
			setInspection(null);
			setSelectedLayouts(new Map());
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
			showProfile(
				(await response.json()) as TemplateProfile,
				(await inspectionResponse.json()) as TemplateInspection,
			);
		} catch (caught) {
			setSelectedProfile(null);
			setInspection(null);
			setUploadError(
				caught instanceof Error
					? caught.message
					: "The template could not be opened.",
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
				caught instanceof Error
					? caught.message
					: "The name could not be saved.",
			);
		} finally {
			setRenameSaving(false);
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

	function handleMappingChange(semantic: SlideLayout, index: number) {
		setValidationFindings(null);
		setMappingsDirty(true);
		setSelectedLayouts((prev) => new Map(prev).set(semantic, index));
		const availableSlots = new Set(
			layoutInspection(index)?.placeholders.map(
				(placeholder) => placeholder.idx,
			),
		);
		setSelectedProfile((profile) =>
			profile
				? {
						...profile,
						layouts: profile.layouts.map((mapping) =>
							mapping.semantic_layout === semantic
								? {
										...mapping,
										template_layout_index: index,
										confidence: 1,
										rationale: `Manually corrected to ${layoutOptionLabel(index)}`,
										slot_mappings: Object.fromEntries(
											Object.entries(mapping.slot_mappings ?? {}).filter(
												([, placeholderIndex]) =>
													availableSlots.has(placeholderIndex),
											),
										),
									}
								: mapping,
						),
					}
				: profile,
		);
	}

	function handleSlotChange(
		semantic: SlideLayout,
		slot: string,
		placeholderIndex: number,
	) {
		setValidationFindings(null);
		setMappingsDirty(true);
		setSelectedProfile((profile) =>
			profile
				? {
						...profile,
						layouts: profile.layouts.map((mapping) =>
							mapping.semantic_layout === semantic
								? {
										...mapping,
										confidence: 1,
										rationale: `Course Author corrected the ${slot} slot.`,
										slot_mappings: {
											...(mapping.slot_mappings ?? {}),
											[slot]: placeholderIndex,
										},
									}
								: mapping,
						),
					}
				: profile,
		);
	}

	async function handleSaveMappings() {
		if (!selectedProfile) return;
		setSaving(true);
		setValidationFindings(null);
		try {
			const mappings = selectedProfile.layouts.map((mapping) => ({
				...mapping,
				template_layout_index:
					selectedLayouts.get(mapping.semantic_layout) ??
					mapping.template_layout_index,
			}));
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
			await refreshList();
		} catch (caught) {
			setUploadError(
				caught instanceof Error
					? caught.message
					: "The mappings could not be saved.",
			);
		} finally {
			setSaving(false);
		}
	}

	async function handleSuggestMappings() {
		if (!selectedProfile || !suggestionConsent || !selectedPreset) return;
		setSuggesting(true);
		setSuggestionError(null);
		setSuggestionNotice(null);
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
				source: "ai" | "heuristic-fallback";
				notice: string | null;
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
			setSuggestionNotice(body.notice);
		} catch (caught) {
			setSuggestionError(
				caught instanceof Error ? caught.message : "The AI suggestion failed.",
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
				caught instanceof Error
					? caught.message
					: "The mappings could not be checked.",
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
				caught instanceof Error
					? caught.message
					: "The preview could not be rendered.",
			);
		} finally {
			setCalibrating(false);
		}
	}

	async function handleDelete() {
		if (!selectedProfile) return;
		setDeleting(true);
		setDeleteError(null);
		try {
			const response = await fetch(`/api/templates/${selectedProfile.id}`, {
				method: "DELETE",
			});
			if (!response.ok) throw new Error(await responseError(response));
			setConfirmingDelete(false);
			setSelectedProfile(null);
			setInspection(null);
			setSelectedId(BUILTIN_ID);
			resetDetail();
			await refreshList();
		} catch (caught) {
			setDeleteError(
				caught instanceof Error
					? caught.message
					: "The template could not be deleted.",
			);
		} finally {
			setDeleting(false);
		}
	}

	const layoutIndices = Array.from(
		{ length: selectedProfile?.slide_count ?? 0 },
		(_, i) => i,
	);
	const orderedMappings = selectedProfile
		? [...selectedProfile.layouts].sort(
				(a, b) =>
					Number(a.confidence >= REVIEW_THRESHOLD) -
					Number(b.confidence >= REVIEW_THRESHOLD),
			)
		: [];
	const reviewCount = orderedMappings.filter(
		(mapping) => mapping.confidence < REVIEW_THRESHOLD,
	).length;

	return (
		<div className="template-settings">
			<div className="template-settings-list">
				<ul className="template-list" aria-label="Templates">
					{allTemplates.map((template) => (
						<li key={template.id}>
							<button
								type="button"
								className="template-list-item"
								aria-current={template.id === selectedId ? "true" : undefined}
								title={template.name}
								onClick={() => void handleSelect(template.id)}
							>
								<span className="template-list-name">{template.name}</span>
								<small>
									{template.id === BUILTIN_ID
										? "Always available"
										: `v${template.version} · ${template.mapped_layouts} layouts`}
								</small>
							</button>
						</li>
					))}
				</ul>
				<label className="btn template-import" htmlFor="template-upload">
					<Upload aria-hidden="true" />
					{uploading ? "Inspecting…" : "Import template"}
				</label>
				<input
					id="template-upload"
					className="visually-hidden"
					type="file"
					accept=".pptx,.potx,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.presentationml.template"
					disabled={uploading}
					onChange={handleUpload}
				/>
			</div>

			<div className="template-settings-detail">
				{uploadError ? <Notice tone="error">{uploadError}</Notice> : null}

				{selectedProfile && selectedProfile.id !== BUILTIN_ID ? (
					<>
						<div className="template-detail-head">
							{renaming ? (
								<form className="template-rename" onSubmit={handleRename}>
									<div className="field">
										<label htmlFor="template-profile-name">Template name</label>
										<input
											id="template-profile-name"
											value={renameValue}
											onChange={(event) => setRenameValue(event.target.value)}
											maxLength={200}
											disabled={renameSaving}
										/>
									</div>
									<div className="form-actions">
										<button
											className="btn btn-small"
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
										<button
											className="btn btn-primary btn-small"
											type="submit"
											disabled={renameSaving || !renameValue.trim()}
										>
											{renameSaving ? "Saving…" : "Save name"}
										</button>
									</div>
								</form>
							) : (
								<div className="template-detail-title">
									<h3 title={selectedProfile.name}>{selectedProfile.name}</h3>
									<button
										className="icon-btn is-small"
										type="button"
										aria-label="Rename template"
										title="Rename template"
										onClick={() => {
											setRenameValue(selectedProfile.name);
											setRenameError(null);
											setRenaming(true);
										}}
									>
										<Pencil aria-hidden="true" />
									</button>
									<button
										className="icon-btn is-small is-danger template-delete"
										type="button"
										aria-label="Delete template"
										title="Delete template"
										onClick={() => {
											setDeleteError(null);
											setConfirmingDelete(true);
										}}
									>
										<Trash2 aria-hidden="true" />
									</button>
								</div>
							)}
							{renameError ? (
								<p className="field-error">{renameError}</p>
							) : null}
							<p className="meta">
								Version {selectedProfile.version} ·{" "}
								{selectedProfile.slide_count} slide layouts ·{" "}
								<span
									className="mono"
									title={selectedProfile.template_filename}
								>
									{selectedProfile.template_filename}
								</span>
							</p>
						</div>

						<section
							className="settings-block"
							aria-labelledby="calibration-heading"
						>
							<div className="canvas-section-head">
								<h3 id="calibration-heading">Preview</h3>
								<button
									className="btn btn-small"
									type="button"
									disabled={calibrating}
									onClick={() => void handleCalibrate()}
								>
									<Images aria-hidden="true" />
									{calibrating ? "Rendering…" : "Render sample Slides"}
								</button>
							</div>
							{calibrationError ? (
								<Notice tone="error">{calibrationError}</Notice>
							) : null}
							{calibrationSlides.length > 0 ? (
								<div className="template-calibration">
									{calibrationSlides.map((slide) => (
										<figure key={slide.semantic_layout}>
											{slide.image_url ? (
												<img
													src={slide.image_url}
													alt={`${snakeToTitle(slide.semantic_layout)} sample`}
												/>
											) : (
												<div className="template-calibration-empty">
													No preview
												</div>
											)}
											<figcaption>
												{snakeToTitle(slide.semantic_layout)}
											</figcaption>
										</figure>
									))}
								</div>
							) : (
								<p className="meta">
									Render sample Slides to see how each layout looks with this
									template.
								</p>
							)}
						</section>

						<section
							className="settings-block"
							aria-labelledby="mapping-heading"
						>
							<div className="canvas-section-head">
								<h3 id="mapping-heading">Layout mappings</h3>
								{reviewCount > 0 ? (
									<span className="state is-warning">
										<AlertTriangle aria-hidden="true" />
										{reviewCount}{" "}
										{reviewCount === 1 ? "layout needs" : "layouts need"} review
									</span>
								) : (
									<span className="state is-success">
										<Check aria-hidden="true" />
										All layouts matched
									</span>
								)}
							</div>
							<ul className="template-mappings">
								{orderedMappings.map((m) => {
									const selectedIndex =
										selectedLayouts.get(m.semantic_layout) ??
										m.template_layout_index;
									const selectedInspection = layoutInspection(selectedIndex);
									const needsReview = m.confidence < REVIEW_THRESHOLD;
									const open = openMappings.has(m.semantic_layout);
									return (
										<li
											key={m.semantic_layout}
											className={`template-mapping${needsReview ? " needs-review" : ""}`}
										>
											<button
												type="button"
												className="template-mapping-toggle"
												aria-expanded={open}
												onClick={() =>
													setOpenMappings((current) => {
														const next = new Set(current);
														if (next.has(m.semantic_layout))
															next.delete(m.semantic_layout);
														else next.add(m.semantic_layout);
														return next;
													})
												}
											>
												<ChevronRight aria-hidden="true" />
												<span className="template-mapping-name">
													{snakeToTitle(m.semantic_layout)}
												</span>
												<span className="template-mapping-target">
													{selectedInspection?.name ||
														`Layout ${selectedIndex}`}
												</span>
												<span
													className={`state ${needsReview ? "is-warning" : "is-success"}`}
												>
													{confidenceLabel(m.confidence)}
												</span>
											</button>
											{open ? (
												<div className="template-mapping-body">
													<div className="field">
														<label
															htmlFor={`layout-${m.semantic_layout}`}
															className="visually-hidden"
														>
															Template layout for {m.semantic_layout}
														</label>
														<span className="field-label" aria-hidden="true">
															Template layout
														</span>
														<select
															id={`layout-${m.semantic_layout}`}
															value={selectedIndex}
															onChange={(e) =>
																handleMappingChange(
																	m.semantic_layout,
																	Number(e.target.value),
																)
															}
														>
															{layoutIndices.map((i) => (
																<option key={i} value={i}>
																	{layoutOptionLabel(i)}
																</option>
															))}
														</select>
														{selectedInspection ? (
															<small>
																Placeholders:{" "}
																{selectedInspection.placeholders
																	.map((slot) => slot.name)
																	.join(", ") || "none"}
															</small>
														) : null}
													</div>
													<div className="template-slots">
														{(
															inspection?.semantic_slots[m.semantic_layout] ??
															[]
														).map((slot) => (
															<div className="field" key={slot}>
																<label
																	htmlFor={`slot-${m.semantic_layout}-${slot}`}
																>
																	<span className="visually-hidden">
																		{snakeToTitle(slot)} slot for{" "}
																		{m.semantic_layout}
																	</span>
																	<span aria-hidden="true">
																		{snakeToTitle(slot)}
																	</span>
																</label>
																<select
																	id={`slot-${m.semantic_layout}-${slot}`}
																	value={m.slot_mappings?.[slot] ?? ""}
																	onChange={(event) =>
																		handleSlotChange(
																			m.semantic_layout,
																			slot,
																			Number(event.target.value),
																		)
																	}
																>
																	<option value="" disabled>
																		Choose a placeholder
																	</option>
																	{selectedInspection?.placeholders.map(
																		(placeholder) => (
																			<option
																				key={placeholder.idx}
																				value={placeholder.idx}
																			>
																				{placeholder.idx}: {placeholder.name}
																			</option>
																		),
																	)}
																</select>
															</div>
														))}
													</div>
													<p className="meta">{m.rationale}</p>
												</div>
											) : null}
										</li>
									);
								})}
							</ul>

							<div className="form-actions template-mapping-actions">
								<button
									className="btn"
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
								<button
									className="btn btn-primary"
									type="button"
									disabled={saving || !mappingsDirty}
									onClick={() => void handleSaveMappings()}
								>
									{saving ? "Saving…" : "Save mapping corrections"}
								</button>
							</div>

							{validationFindings ? (
								<section className="template-validation" aria-live="polite">
									<h4>Mapping check</h4>
									{validationFindings.length === 0 ? (
										<Notice tone="success">
											Mappings are ready for export.
										</Notice>
									) : (
										<ul>
											{validationFindings.map((finding) => (
												<li key={`${finding.level}-${finding.message}`}>
													<Notice
														tone={
															finding.level === "blocking" ? "error" : "warning"
														}
														role="note"
													>
														{finding.message}
													</Notice>
												</li>
											))}
										</ul>
									)}
								</section>
							) : null}
						</section>

						<section
							className="settings-block"
							aria-labelledby="mapping-assistance-heading"
						>
							<h3 id="mapping-assistance-heading">Improve mappings with AI</h3>
							{selectedPreset && selectedAccount ? (
								<>
									<p className="meta">
										Template metadata will leave this device and be sent to{" "}
										<strong>{selectedAccount.name}</strong> using{" "}
										<strong>{selectedPreset.name}</strong>. This includes layout
										and placeholder names, identifiers, and types. The
										PowerPoint file, slide content, and calibration images stay
										on this device.
									</p>
									<label className="check-row">
										<input
											type="checkbox"
											checked={suggestionConsent}
											onChange={(event) =>
												setSuggestionConsent(event.target.checked)
											}
										/>
										<span>
											I agree to send this template metadata for this
											suggestion.
										</span>
									</label>
									<div className="form-actions">
										<button
											className="btn"
											type="button"
											disabled={!suggestionConsent || suggesting}
											onClick={() => void handleSuggestMappings()}
										>
											{suggesting
												? "Asking model…"
												: "Improve mappings with AI"}
										</button>
									</div>
								</>
							) : (
								<p className="meta">
									Add and select a Model Preset to ask for AI suggestions.
								</p>
							)}
							{suggestionError ? (
								<Notice tone="error">{suggestionError}</Notice>
							) : null}
							{suggestionNotice ? (
								<Notice role="status">{suggestionNotice}</Notice>
							) : null}
						</section>
					</>
				) : (
					<div className="template-detail-head">
						<div className="template-detail-title">
							<h3>Built-in default</h3>
						</div>
						<p className="meta">
							The standard Office theme. It is always available and needs no
							mapping. Import your own PowerPoint template to match your
							institution&apos;s design.
						</p>
					</div>
				)}
			</div>

			{confirmingDelete && selectedProfile ? (
				<ConfirmDialog
					title="Delete template?"
					confirmLabel="Delete template"
					busy={deleting}
					error={deleteError}
					onCancel={() => setConfirmingDelete(false)}
					onConfirm={() => void handleDelete()}
				>
					<p>
						“{selectedProfile.name}” will be removed from your templates. This
						cannot be undone.
					</p>
				</ConfirmDialog>
			) : null}
		</div>
	);
}
