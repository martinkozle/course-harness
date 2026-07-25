import { type ChangeEvent, useState } from "react";
import { responseError } from "./api";
import type { CalibrationSlide, TemplateProfile, TemplateProfileSummary } from "./models";

type TemplatesViewProps = {
	templates: TemplateProfileSummary[];
	onTemplatesChange: (templates: TemplateProfileSummary[]) => void;
};

function snakeToTitle(value: string): string {
	return value
		.replace(/_/g, " ")
		.replace(/\b\w/g, (c) => c.toUpperCase());
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
	onTemplatesChange,
}: TemplatesViewProps) {
	const [uploading, setUploading] = useState(false);
	const [uploadError, setUploadError] = useState<string | null>(null);
	const [selectedProfile, setSelectedProfile] =
		useState<TemplateProfile | null>(null);
	const [selectedLayouts, setSelectedLayouts] = useState<Map<string, number>>(
		new Map(),
	);
	const [saving, setSaving] = useState(false);
	const [calibrating, setCalibrating] = useState(false);
	const [calibrationSlides, setCalibrationSlides] = useState<
		CalibrationSlide[]
	>([]);
	const [calibrationError, setCalibrationError] = useState<string | null>(null);

	async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0];
		if (!file) return;
		setUploading(true);
		setUploadError(null);
		setSelectedProfile(null);
		setCalibrationSlides([]);
		setSelectedLayouts(new Map());
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
			setSelectedLayouts(new Map());
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
		}
	}

	async function handleSelect(profileId: string) {
		if (profileId === "_builtin-default") {
			setSelectedProfile(null);
			setCalibrationSlides([]);
			setSelectedLayouts(new Map());
			return;
		}
		try {
			const response = await fetch(`/api/templates/${profileId}`);
			if (!response.ok) throw new Error(await responseError(response));
			const profile = (await response.json()) as TemplateProfile;
			setSelectedProfile(profile);
			setCalibrationSlides([]);
			setCalibrationError(null);
			const layoutMap = new Map<string, number>();
			for (const m of profile.layouts) {
				layoutMap.set(m.semantic_layout, m.template_layout_index);
			}
			setSelectedLayouts(layoutMap);
		} catch (caught) {
			setSelectedProfile(null);
		}
	}

	function handleMappingChange(semantic: string, index: number) {
		setSelectedLayouts((prev) => {
			const next = new Map(prev);
			next.set(semantic, index);
			return next;
		});
	}

	async function handleSaveMappings() {
		if (!selectedProfile) return;
		setSaving(true);
		try {
			const mappings = Array.from(selectedLayouts.entries()).map(
				([semantic_layout, template_layout_index]) => ({
					semantic_layout,
					template_layout_index,
				}),
			);
			const response = await fetch(
				`/api/templates/${selectedProfile.id}`,
				{
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ mappings }),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			const updated = (await response.json()) as TemplateProfile;
			setSelectedProfile(updated);
			setCalibrationSlides([]);
			setCalibrationError(null);
			const listResp = await fetch("/api/templates");
			if (listResp.ok) {
				onTemplatesChange((await listResp.json()) as TemplateProfileSummary[]);
			}
		} catch (caught) {
			setUploadError(
				caught instanceof Error ? caught.message : "Save failed.",
			);
		} finally {
			setSaving(false);
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
			setCalibrationSlides(
				(await response.json()) as CalibrationSlide[],
			);
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
		try {
			const response = await fetch(
				`/api/templates/${selectedProfile.id}`,
				{ method: "DELETE" },
			);
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

	const layoutCount = selectedProfile?.slide_count ?? 0;
	const layoutIndices = Array.from(
		{ length: Math.max(layoutCount, 11) },
		(_, i) => i,
	);

	const allTemplates = [
		{
			id: "_builtin-default",
			name: "Built-in default",
			version: 1,
			slide_count: 11,
			mapped_layouts: 9,
		} as TemplateProfileSummary,
		...templates,
	];

	return (
		<div className="templates-view">
			<h2 className="section-title">Templates</h2>
			<p className="section-lede">
				Import and configure PowerPoint templates for course export.
			</p>

			{uploadError ? (
				<p className="notice error-notice" role="alert">
					{uploadError}
				</p>
			) : null}

			<div className="template-selector">
				<label htmlFor="template-select">Active template:</label>
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
						<div>
							<h3>{selectedProfile.name}</h3>
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
										<div className="calibration-placeholder">
											No preview
										</div>
									)}
								</div>
							))}
						</div>
					) : null}

					<h4 className="mapping-heading">Layout mappings</h4>
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
											value={selectedLayouts.get(m.semantic_layout) ?? m.template_layout_index}
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
													{i}: Layout {i}
												</option>
											))}
										</select>
									</td>
									<td>
										<span
											className="confidence-badge"
											style={{
												backgroundColor: confidenceColor(m.confidence),
											}}
										>
											{confidenceLabel(m.confidence)} ({(m.confidence * 100).toFixed(0)}%)
										</span>
									</td>
								</tr>
							))}
						</tbody>
					</table>

					<div className="mapping-actions">
						<button
							className="primary-action"
							type="button"
							disabled={saving}
							onClick={() => void handleSaveMappings()}
						>
							{saving ? "Saving…" : "Save mapping corrections"}
						</button>
					</div>
				</div>
			) : selectedProfile === null ? null : (
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
					{uploading ? "Inspecting template…" : "Import a template (.pptx or .potx)"}
				</label>
				<input
					id="template-upload"
					type="file"
					accept=".pptx,.potx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
					disabled={uploading}
					onChange={handleUpload}
					className="file-input"
				/>
			</div>
		</div>
	);
}
