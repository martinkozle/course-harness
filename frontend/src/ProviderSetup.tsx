import { type FormEvent, useState } from "react";

import { responseError } from "./api";
import type { ModelCatalog, ProviderKind } from "./models";

export function ModelsView({
	catalog,
	onCatalogChange,
}: {
	catalog: ModelCatalog;
	onCatalogChange: (catalog: ModelCatalog) => void;
}) {
	const [providerName, setProviderName] = useState("My OpenRouter");
	const [kind, setKind] = useState<ProviderKind>("openrouter");
	const [baseUrl, setBaseUrl] = useState("");
	const [allowInsecureHttp, setAllowInsecureHttp] = useState(false);
	const [apiKey, setApiKey] = useState("");
	const [presetName, setPresetName] = useState("");
	const [model, setModel] = useState("");
	const [providerId, setProviderId] = useState(
		catalog.provider_accounts[0]?.id ?? "",
	);
	const [editingProviderId, setEditingProviderId] = useState<string | null>(
		null,
	);
	const [replacementApiKey, setReplacementApiKey] = useState("");
	const [deletingProviderId, setDeletingProviderId] = useState<string | null>(
		null,
	);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const verificationDestination =
		kind === "openrouter"
			? "https://openrouter.ai/api/v1"
			: kind === "anthropic"
				? "https://api.anthropic.com"
				: baseUrl.trim() || "the configured API base URL";
	const selectedProviderAccount = catalog.provider_accounts.find(
		(account) => account.id === providerId,
	);
	const presetCountByProvider = new Map<string, number>();
	for (const preset of catalog.model_presets) {
		presetCountByProvider.set(
			preset.provider_account_id,
			(presetCountByProvider.get(preset.provider_account_id) ?? 0) + 1,
		);
	}

	async function reloadCatalog() {
		const response = await fetch("/api/models");
		if (!response.ok) throw new Error(await responseError(response));
		const next = (await response.json()) as ModelCatalog;
		onCatalogChange(next);
		return next;
	}

	async function addProvider(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		setBusy(true);
		setError(null);
		try {
			const response = await fetch("/api/provider-accounts", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					name: providerName.trim(),
					kind,
					api_key: apiKey,
					base_url: kind === "openai-compatible" ? baseUrl.trim() : null,
					allow_insecure_http:
						kind === "openai-compatible" && allowInsecureHttp,
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const account = (await response.json()) as { id: string };
			const next = await reloadCatalog();
			setProviderId(account.id);
			setApiKey("");
			setAllowInsecureHttp(false);
			if (next.provider_accounts.length === 1) setPresetName("Course planning");
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The Provider Account was not saved.",
			);
		} finally {
			setBusy(false);
		}
	}

	async function addModel(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		setBusy(true);
		setError(null);
		try {
			const response = await fetch("/api/models", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					name: presetName.trim(),
					provider_account_id: providerId,
					model: model.trim(),
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			await reloadCatalog();
			setPresetName("");
			setModel("");
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The Model Preset was not saved.",
			);
		} finally {
			setBusy(false);
		}
	}

	async function replaceCredential(
		event: FormEvent<HTMLFormElement>,
		accountId: string,
	) {
		event.preventDefault();
		setBusy(true);
		setError(null);
		try {
			const response = await fetch(
				`/api/provider-accounts/${encodeURIComponent(accountId)}/credential`,
				{
					method: "PATCH",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ api_key: replacementApiKey }),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			setEditingProviderId(null);
			setReplacementApiKey("");
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The Provider Account credential was not replaced.",
			);
		} finally {
			setBusy(false);
		}
	}

	async function deleteProvider(accountId: string) {
		setBusy(true);
		setError(null);
		try {
			const response = await fetch(
				`/api/provider-accounts/${encodeURIComponent(accountId)}?delete_model_presets=true`,
				{ method: "DELETE" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			const next = (await response.json()) as ModelCatalog;
			onCatalogChange(next);
			setProviderId((current) =>
				current === accountId ? (next.provider_accounts[0]?.id ?? "") : current,
			);
			setDeletingProviderId(null);
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The Provider Account was not deleted.",
			);
		} finally {
			setBusy(false);
		}
	}

	return (
		<main className="page-main models-main" aria-labelledby="models-heading">
			<header className="page-heading models-heading">
				<p className="eyebrow">Agent settings</p>
				<h1 id="models-heading">Models</h1>
				<p>
					Save a provider credential once, then attach as many named Model
					Presets as you need.
				</p>
			</header>

			{error ? (
				<p className="notice error-notice" id="models-error" role="alert">
					{error}
				</p>
			) : null}

			<section
				className="model-settings-section"
				aria-labelledby="accounts-heading"
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">Reusable credentials</p>
						<h2 id="accounts-heading">Provider Accounts</h2>
					</div>
					<span className="count-badge">
						{catalog.provider_accounts.length}
					</span>
				</div>
				{catalog.provider_accounts.length > 0 ? (
					<ul className="provider-account-list">
						{catalog.provider_accounts.map((account) => {
							const presetCount = presetCountByProvider.get(account.id) ?? 0;
							const isEditing = editingProviderId === account.id;
							const isDeleting = deletingProviderId === account.id;
							return (
								<li key={account.id}>
									<div className="provider-account-copy">
										<strong>{account.name}</strong>
										<span>{account.kind}</span>
										<small>{account.base_url}</small>
									</div>
									<div className="provider-account-actions">
										<button
											className="quiet-action compact-action"
											type="button"
											disabled={busy}
											aria-expanded={isEditing}
											aria-controls={`replacement-form-${account.id}`}
											onClick={() => {
												setError(null);
												setDeletingProviderId(null);
												setReplacementApiKey("");
												setEditingProviderId(isEditing ? null : account.id);
											}}
										>
											{isEditing ? "Cancel replacement" : "Replace key"}
										</button>
										<button
											className="quiet-action destructive-action compact-action"
											type="button"
											disabled={busy}
											aria-expanded={isDeleting}
											aria-controls={`delete-confirmation-${account.id}`}
											onClick={() => {
												setError(null);
												setEditingProviderId(null);
												setReplacementApiKey("");
												setDeletingProviderId(isDeleting ? null : account.id);
											}}
										>
											{isDeleting ? "Cancel deletion" : "Delete"}
										</button>
									</div>
									{isEditing ? (
										<form
											id={`replacement-form-${account.id}`}
											className="provider-account-inline-form"
											onSubmit={(event) =>
												void replaceCredential(event, account.id)
											}
											aria-busy={busy}
										>
											<div className="field">
												<label htmlFor={`replacement-key-${account.id}`}>
													New API key for {account.name}
												</label>
												<input
													id={`replacement-key-${account.id}`}
													type="password"
													value={replacementApiKey}
													onChange={(event) =>
														setReplacementApiKey(event.target.value)
													}
													required
													autoComplete="off"
													aria-invalid={error ? true : undefined}
													aria-describedby={`replacement-disclosure-${account.id}${error ? " models-error" : ""}`}
												/>
											</div>
											<p
												className="form-help"
												id={`replacement-disclosure-${account.id}`}
												role="note"
											>
												Saving sends this new key to {account.base_url} to
												verify it before storage. Existing Model Presets will
												keep using this account.
											</p>
											<button
												className="primary-action compact-action"
												type="submit"
												disabled={busy}
											>
												{busy ? "Verifying…" : "Save new key"}
											</button>
										</form>
									) : null}
									{isDeleting ? (
										<div
											id={`delete-confirmation-${account.id}`}
											className="provider-account-delete-confirmation"
										>
											<p>
												Delete <strong>{account.name}</strong> and its saved
												credential?
												{presetCount > 0
													? ` This will also delete ${presetCount} attached Model ${presetCount === 1 ? "Preset" : "Presets"}.`
													: ""}
											</p>
											<button
												className="secondary-action destructive-action compact-action"
												type="button"
												disabled={busy}
												onClick={() => void deleteProvider(account.id)}
											>
												{busy ? "Deleting…" : "Delete account"}
											</button>
										</div>
									) : null}
								</li>
							);
						})}
					</ul>
				) : (
					<p className="empty-note">No Provider Accounts saved yet.</p>
				)}

				<details
					className="settings-form-card"
					open={catalog.provider_accounts.length === 0}
				>
					<summary>Add Provider Account</summary>
					<form onSubmit={addProvider} aria-busy={busy}>
						<div className="field">
							<label htmlFor="provider-name">Account name</label>
							<input
								id="provider-name"
								value={providerName}
								onChange={(event) => setProviderName(event.target.value)}
								required
							/>
						</div>
						<div className="field">
							<label htmlFor="provider-kind">Provider</label>
							<select
								id="provider-kind"
								value={kind}
								onChange={(event) =>
									setKind(event.target.value as ProviderKind)
								}
							>
								<option value="openrouter">OpenRouter</option>
								<option value="anthropic">Anthropic</option>
								<option value="openai-compatible">OpenAI-compatible</option>
							</select>
						</div>
						{kind === "openai-compatible" ? (
							<>
								<div className="field">
									<label htmlFor="provider-url">API base URL</label>
									<input
										id="provider-url"
										type="url"
										value={baseUrl}
										onChange={(event) => setBaseUrl(event.target.value)}
										required
									/>
								</div>
								{baseUrl.trim().toLowerCase().startsWith("http://") ? (
									<label className="consent-control">
										<input
											type="checkbox"
											checked={allowInsecureHttp}
											onChange={(event) =>
												setAllowInsecureHttp(event.target.checked)
											}
										/>
										Allow HTTP to another host. Your API key and Course requests
										will be sent without encryption.
									</label>
								) : null}
							</>
						) : null}
						<div className="field">
							<label htmlFor="provider-key">API key</label>
							<input
								id="provider-key"
								type="password"
								value={apiKey}
								onChange={(event) => setApiKey(event.target.value)}
								required
								autoComplete="off"
								aria-describedby="provider-network-disclosure"
							/>
						</div>
						<p
							className="form-help"
							id="provider-network-disclosure"
							role="note"
						>
							Saving sends this key to {verificationDestination} to verify
							access before storing it outside every Course Workspace.
						</p>
						<button
							className="primary-action compact-action"
							type="submit"
							disabled={busy}
						>
							{busy ? "Verifying…" : "Save Provider Account"}
						</button>
					</form>
				</details>
			</section>

			<section
				className="model-settings-section"
				aria-labelledby="presets-heading"
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">Chat choices</p>
						<h2 id="presets-heading">Model Presets</h2>
					</div>
					<span className="count-badge">{catalog.model_presets.length}</span>
				</div>
				{catalog.model_presets.length > 0 ? (
					<ul className="model-preset-list">
						{catalog.model_presets.map((preset) => {
							const account = catalog.provider_accounts.find(
								(candidate) => candidate.id === preset.provider_account_id,
							);
							return (
								<li key={preset.id}>
									<div>
										<strong>{preset.name}</strong>
										<span>{preset.model}</span>
									</div>
									<small>{account?.name ?? "Missing Provider Account"}</small>
								</li>
							);
						})}
					</ul>
				) : (
					<p className="empty-note">
						Add a Model Preset after saving a Provider Account.
					</p>
				)}

				<details
					className="settings-form-card"
					open={
						catalog.provider_accounts.length > 0 &&
						catalog.model_presets.length === 0
					}
				>
					<summary>Add Model Preset</summary>
					<form onSubmit={addModel} aria-busy={busy}>
						<div className="field">
							<label htmlFor="model-preset-name">Preset name</label>
							<input
								id="model-preset-name"
								value={presetName}
								onChange={(event) => setPresetName(event.target.value)}
								placeholder="Fast planning"
								required
							/>
						</div>
						<div className="field">
							<label htmlFor="model-provider">Provider Account</label>
							<select
								id="model-provider"
								value={providerId}
								onChange={(event) => setProviderId(event.target.value)}
								required
							>
								<option value="" disabled>
									Select an account
								</option>
								{catalog.provider_accounts.map((account) => (
									<option key={account.id} value={account.id}>
										{account.name}
									</option>
								))}
							</select>
						</div>
						<div className="field">
							<label htmlFor="provider-model">Model ID</label>
							<input
								id="provider-model"
								value={model}
								onChange={(event) => setModel(event.target.value)}
								placeholder="nvidia/llama-3.3-nemotron-super-49b-v1:free"
								required
								autoComplete="off"
								aria-describedby="preset-network-disclosure"
							/>
						</div>
						<p className="form-help" id="preset-network-disclosure" role="note">
							Saving sends this Model ID and uses the saved credential for{" "}
							{selectedProviderAccount?.name ?? "the selected Provider Account"}{" "}
							to contact{" "}
							{selectedProviderAccount?.base_url ?? "its API endpoint"} and
							verify capabilities.
						</p>
						<button
							className="primary-action compact-action"
							type="submit"
							disabled={busy || catalog.provider_accounts.length === 0}
						>
							{busy ? "Verifying…" : "Save Model Preset"}
						</button>
					</form>
				</details>
			</section>
		</main>
	);
}
