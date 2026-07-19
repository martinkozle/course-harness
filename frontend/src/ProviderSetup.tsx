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
	const [apiKey, setApiKey] = useState("");
	const [presetName, setPresetName] = useState("");
	const [model, setModel] = useState("");
	const [providerId, setProviderId] = useState(
		catalog.provider_accounts[0]?.id ?? "",
	);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

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
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const account = (await response.json()) as { id: string };
			const next = await reloadCatalog();
			setProviderId(account.id);
			setApiKey("");
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
				<p className="notice error-notice" role="alert">
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
						{catalog.provider_accounts.map((account) => (
							<li key={account.id}>
								<strong>{account.name}</strong>
								<span>{account.kind}</span>
								<small>{account.base_url}</small>
							</li>
						))}
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
							/>
						</div>
						<p className="form-help">
							The key stays outside every Course Workspace.
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
							/>
						</div>
						<p className="form-help">
							Capabilities are verified before this preset is saved.
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
