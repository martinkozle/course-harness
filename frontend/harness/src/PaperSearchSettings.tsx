import { KeyRound, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import { responseError } from "./api";
import { storageLabels } from "./ConnectorSettings";
import type { PaperSearchKey } from "./models";
import { errorMessage, Notice } from "./ui";

const keyHelp: Record<PaperSearchKey["provider"], string> = {
	semantic_scholar:
		"Semantic Scholar works without a key, but shares a public rate limit that is often busy. A free key from semanticscholar.org gives you your own limit.",
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
	return (await response.json()) as PaperSearchKey[];
}

/** Optional API keys for the paper indexes the Course Agent and Sources search use. */
export function PaperSearchSettings() {
	const [keys, setKeys] = useState<PaperSearchKey[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [editing, setEditing] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	useEffect(() => {
		send("/api/paper-search-keys", "GET")
			.then(setKeys)
			.catch((caught) =>
				setError(
					errorMessage(caught, "Paper search keys could not be loaded."),
				),
			);
	}, []);

	async function act(
		action: () => Promise<PaperSearchKey[]>,
		fallback: string,
	) {
		setBusy(true);
		setError(null);
		try {
			setKeys(await action());
			setEditing(null);
		} catch (caught) {
			setError(errorMessage(caught, fallback));
		} finally {
			setBusy(false);
		}
	}

	return (
		<section className="model-section" aria-labelledby="paper-search-heading">
			<h3 id="paper-search-heading">Paper search</h3>
			<p className="meta" role="note">
				Paper search uses arXiv, Crossref, Semantic Scholar, and OpenAlex
				directly, without a Connector. Keys are optional. They stay on this
				computer, outside every Course folder.
			</p>
			{error ? (
				<Notice tone="error" role="alert">
					{error}
				</Notice>
			) : null}
			{keys === null && !error ? (
				<p className="meta" role="status">
					Loading paper search keys…
				</p>
			) : null}
			{keys?.length ? (
				<ul className="row-list">
					{keys.map((key) => (
						<li key={key.provider} className="model-account">
							<div className="row">
								<div className="row-main">
									<span className="row-title">{key.label}</span>
									<span className="row-meta">
										{key.configured
											? "Key saved"
											: "No key: shared, rate-limited use"}
									</span>
								</div>
								<div className="row-actions">
									<button
										type="button"
										className="btn btn-quiet btn-small"
										disabled={busy}
										aria-expanded={editing === key.provider}
										onClick={() => {
											setError(null);
											setEditing(
												editing === key.provider ? null : key.provider,
											);
										}}
									>
										<KeyRound aria-hidden="true" />
										{editing === key.provider
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
															`/api/paper-search-keys/${key.provider}`,
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
							{editing === key.provider ? (
								<KeyForm
									paperKey={key}
									busy={busy}
									onCancel={() => setEditing(null)}
									onSave={(apiKey) =>
										act(
											() =>
												send(`/api/paper-search-keys/${key.provider}`, "PUT", {
													api_key: apiKey,
												}),
											`The ${key.label} key could not be saved.`,
										)
									}
								/>
							) : null}
						</li>
					))}
				</ul>
			) : null}
		</section>
	);
}

function KeyForm({
	paperKey,
	busy,
	onCancel,
	onSave,
}: {
	paperKey: PaperSearchKey;
	busy: boolean;
	onCancel: () => void;
	onSave: (apiKey: string) => Promise<void>;
}) {
	const [value, setValue] = useState("");
	const id = `paper-search-key-${paperKey.provider}`;
	const storage = paperKey.credential_storage
		? (storageLabels[paperKey.credential_storage] ?? "")
		: "";

	function submit(event: FormEvent) {
		event.preventDefault();
		void onSave(value.trim());
	}

	return (
		<form className="model-inline-form" onSubmit={submit} aria-busy={busy}>
			<div className="field">
				<label htmlFor={id}>{paperKey.label} API key</label>
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
				{keyHelp[paperKey.provider]} {storage}
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
