import { Check, KeyRound, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";

import { responseError } from "./api";
import { storageLabels } from "./ConnectorSettings";
import type { DocumentProcessing, ProcessorKey } from "./models";
import { errorMessage, Notice } from "./ui";
import type { Library } from "./useLibrary";

const keyHelp: Record<ProcessorKey["vendor"], string> = {
	llamaparse:
		"Create a key at cloud.llamaindex.ai. The free plan includes 10,000 credits a month.",
	mistral:
		"Create a key at console.mistral.ai. OCR is billed per page to your Mistral account.",
};

const vendorLabels: Record<string, string> = {
	llamaparse: "LlamaParse",
	mistral: "Mistral",
	anthropic: "Anthropic",
	openai: "OpenAI",
	openrouter: "OpenRouter",
	bedrock: "Amazon Bedrock",
};

async function send(path: string, method: string, body?: object) {
	const response = await fetch(path, {
		method,
		...(body
			? {
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(body),
				}
			: {}),
	});
	if (!response.ok) throw new Error(await responseError(response));
	return (await response.json()) as DocumentProcessing;
}

/** How documents are read: the local defaults, hosted processors, and their keys. */
export function DocumentProcessingSettings({ library }: { library: Library }) {
	const settings = library.processing;
	const [error, setError] = useState<string | null>(null);
	const [editing, setEditing] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	async function act(
		action: () => Promise<DocumentProcessing>,
		fallback: string,
	) {
		setBusy(true);
		setError(null);
		try {
			library.setProcessing(await action());
			setEditing(null);
		} catch (caught) {
			setError(errorMessage(caught, fallback));
		} finally {
			setBusy(false);
		}
	}

	if (!settings) {
		return (
			<p className="meta" role="status">
				Loading document processing settings…
			</p>
		);
	}

	const upgrades = settings.processors.filter(
		(processor) => processor.id !== "local-quick",
	);
	const preferred = upgrades.find(
		(processor) => processor.id === settings.preferred_processor,
	);
	const consentedVendors = [
		...new Set(
			settings.processors
				.filter((processor) => processor.hosted && processor.consented)
				.map((processor) => processor.vendor ?? ""),
		),
	].filter(Boolean);
	const docling = settings.processors.find((p) => p.id === "docling");

	function savePreferences(preferredProcessor: string | null, auto: boolean) {
		return act(
			() =>
				send("/api/document-processing/preferences", "PUT", {
					preferred_processor: preferredProcessor,
					auto_upgrade: preferredProcessor ? auto : false,
				}),
			"Your choice could not be saved.",
		);
	}

	return (
		<div className="model-settings">
			{error ? (
				<Notice tone="error" role="alert">
					{error}
				</Notice>
			) : null}
			<section className="model-section" aria-labelledby="documents-local">
				<h3 id="documents-local">On this device</h3>
				<p className="meta" role="note">
					Every PDF, PowerPoint, Word, and web page is first read quickly on
					this computer, in seconds and without models. It reads the text
					already in the file, but not text inside images, tables, or reading
					order across columns. A file that looks scanned, image-heavy, or
					garbled is marked “May be incomplete”.
				</p>
				<div className="settings-control">
					<div>
						<strong>Docling</strong>
						<p className="meta">
							Reads scans, tables, and multi-column layouts with local models.
							It takes minutes per document and needs gigabytes of memory.
						</p>
					</div>
					{docling?.available ? (
						<span className="state is-success">
							<Check aria-hidden="true" />
							Ready
						</span>
					) : docling?.unavailable_action === "download-models" ? (
						<button
							type="button"
							className="btn btn-small"
							onClick={library.requestModelDownload}
						>
							Download Docling models
						</button>
					) : (
						<span className="state">Not installed</span>
					)}
				</div>
				{docling && docling.unavailable_action === "install" ? (
					<p className="meta">
						To install it, run{" "}
						<code className="mono">
							uv tool install --torch-backend cpu 'course-harness[docling]'
						</code>{" "}
						and restart Course Harness.
					</p>
				) : null}
			</section>

			<section className="model-section" aria-labelledby="documents-upgrade">
				<h3 id="documents-upgrade">When a file may be incomplete</h3>
				<div className="field">
					<label htmlFor="preferred-processor">
						Offer to reprocess it with
					</label>
					<select
						id="preferred-processor"
						value={settings.preferred_processor ?? ""}
						disabled={busy}
						onChange={(event) =>
							void savePreferences(
								event.target.value || null,
								settings.auto_upgrade,
							)
						}
					>
						<option value="">
							{docling?.unavailable_action === "install"
								? "Nothing: I will choose each time"
								: "Docling (the default)"}
						</option>
						{upgrades.map((processor) => (
							<option key={processor.id} value={processor.id}>
								{processor.label}
								{processor.hosted ? " (online)" : ""}
								{processor.available ? "" : " (needs setup)"}
							</option>
						))}
					</select>
				</div>
				{preferred ? (
					<p className="field-help" role="note">
						{preferred.description}
						{preferred.available
							? ""
							: ` ${preferred.unavailable_reason ?? ""}`}
					</p>
				) : null}
				<label className="check-row">
					<input
						type="checkbox"
						checked={settings.auto_upgrade}
						disabled={
							busy ||
							!preferred ||
							!preferred.available ||
							(preferred.hosted && !preferred.consented)
						}
						onChange={(event) =>
							void savePreferences(
								settings.preferred_processor,
								event.target.checked,
							)
						}
					/>
					<span>Reprocess it automatically, without asking</span>
				</label>
				{preferred?.hosted && !preferred.consented ? (
					<p className="field-help">
						<button
							type="button"
							className="link-btn"
							onClick={() => library.requestConsent(preferred)}
						>
							Allow sending documents to {preferred.label}
						</button>{" "}
						to reprocess automatically.
					</p>
				) : null}
				<p className="meta">
					You can also reprocess any file from its menu in Sources. Model
					Presets that accept images on Anthropic, OpenAI, OpenRouter, or Amazon
					Bedrock can transcribe PDFs page by page.
				</p>
			</section>

			<section className="model-section" aria-labelledby="documents-keys">
				<h3 id="documents-keys">Online processors</h3>
				<p className="meta" role="note">
					Keys stay on this computer, outside every Course folder. A file is
					sent to a vendor only after you allow that vendor.
				</p>
				<ul className="row-list">
					{settings.keys.map((key) => (
						<li key={key.vendor} className="model-account">
							<div className="row">
								<div className="row-main">
									<span className="row-title">{key.label}</span>
									<span className="row-meta">
										{key.configured ? "Key saved" : "No key"}
										{consentedVendors.includes(key.vendor)
											? " · Allowed to receive documents"
											: ""}
									</span>
								</div>
								<div className="row-actions">
									<button
										type="button"
										className="btn btn-quiet btn-small"
										disabled={busy}
										aria-expanded={editing === key.vendor}
										onClick={() => {
											setError(null);
											setEditing(editing === key.vendor ? null : key.vendor);
										}}
									>
										<KeyRound aria-hidden="true" />
										{editing === key.vendor
											? "Close"
											: key.configured
												? "Replace key"
												: "Add key"}
									</button>
									{key.configured ? (
										<button
											type="button"
											className="icon-btn is-small is-danger"
											disabled={busy}
											aria-label={`Remove the ${key.label} key`}
											title={`Remove the ${key.label} key`}
											onClick={() =>
												void act(
													() =>
														send(
															`/api/document-processing/keys/${key.vendor}`,
															"DELETE",
														),
													`The ${key.label} key could not be removed.`,
												)
											}
										>
											<Trash2 aria-hidden="true" />
										</button>
									) : null}
								</div>
							</div>
							{editing === key.vendor ? (
								<KeyForm
									processorKey={key}
									busy={busy}
									onCancel={() => setEditing(null)}
									onSave={(apiKey) =>
										act(
											() =>
												send(
													`/api/document-processing/keys/${key.vendor}`,
													"PUT",
													{
														api_key: apiKey,
													},
												),
											`The ${key.label} key could not be saved.`,
										)
									}
								/>
							) : null}
						</li>
					))}
				</ul>
				{consentedVendors.length > 0 ? (
					<>
						<h4 className="settings-subheading">
							Allowed to receive documents
						</h4>
						<ul className="row-list">
							{consentedVendors.map((vendor) => (
								<li key={vendor} className="row">
									<div className="row-main">
										<span className="row-title">
											{vendorLabels[vendor] ?? vendor}
										</span>
									</div>
									<div className="row-actions">
										<button
											type="button"
											className="btn btn-quiet btn-small"
											disabled={busy}
											onClick={() =>
												void act(
													() =>
														send(
															`/api/document-processing/consent/${vendor}`,
															"DELETE",
														),
													"Your choice could not be saved.",
												)
											}
										>
											Stop sending
										</button>
									</div>
								</li>
							))}
						</ul>
					</>
				) : null}
			</section>
		</div>
	);
}

function KeyForm({
	processorKey,
	busy,
	onCancel,
	onSave,
}: {
	processorKey: ProcessorKey;
	busy: boolean;
	onCancel: () => void;
	onSave: (apiKey: string) => Promise<void>;
}) {
	const [value, setValue] = useState("");
	const id = `processor-key-${processorKey.vendor}`;
	const storage = processorKey.credential_storage
		? (storageLabels[processorKey.credential_storage] ?? "")
		: "";

	function submit(event: FormEvent) {
		event.preventDefault();
		void onSave(value.trim());
	}

	return (
		<form className="model-inline-form" onSubmit={submit} aria-busy={busy}>
			<div className="field">
				<label htmlFor={id}>{processorKey.label} API key</label>
				<input
					id={id}
					type="password"
					value={value}
					onChange={(event) => setValue(event.target.value)}
					required
					autoComplete="off"
				/>
			</div>
			<p className="field-help" role="note">
				{keyHelp[processorKey.vendor]} {storage}
			</p>
			<div className="form-actions">
				<button
					type="button"
					className="btn"
					disabled={busy}
					onClick={onCancel}
				>
					Cancel
				</button>
				<button
					className="btn btn-primary"
					type="submit"
					disabled={busy || !value.trim()}
				>
					{busy ? "Saving…" : "Save key"}
				</button>
			</div>
		</form>
	);
}
