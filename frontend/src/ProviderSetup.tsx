import { KeyRound, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import { responseError } from "./api";
import type { ModelCatalog, ProviderKind } from "./models";
import { Notice } from "./ui";

/** A model an OpenAI-compatible endpoint reports. */
type ModelSuggestion = {
	id: string;
	vision: boolean;
	context_window: number | null;
};

function suggestionDetail(suggestion: ModelSuggestion): string {
	return [
		suggestion.context_window
			? `${Math.round(suggestion.context_window / 1024)}K context`
			: null,
		suggestion.vision ? "vision" : null,
	]
		.filter(Boolean)
		.join(" · ");
}

const providerLabels: Record<ProviderKind, string> = {
	openrouter: "OpenRouter",
	anthropic: "Anthropic",
	"openai-compatible": "OpenAI-compatible",
};

/** Provider Accounts and Model Presets. Shared by Settings and the composer's model setup. */
export function ModelSettings({
	catalog,
	onCatalogChange,
	onPresetSaved,
}: {
	catalog: ModelCatalog;
	onCatalogChange: (catalog: ModelCatalog) => void;
	onPresetSaved?: () => void;
}) {
	const hasAccounts = catalog.provider_accounts.length > 0;
	const [providerName, setProviderName] = useState("My OpenRouter");
	const [kind, setKind] = useState<ProviderKind>("openrouter");
	const [baseUrl, setBaseUrl] = useState("");
	const [allowInsecureHttp, setAllowInsecureHttp] = useState(false);
	const [apiKey, setApiKey] = useState("");
	const [presetName, setPresetName] = useState(
		hasAccounts && catalog.model_presets.length === 0 ? "Course planning" : "",
	);
	const [model, setModel] = useState("");
	const [providerId, setProviderId] = useState(
		catalog.provider_accounts[0]?.id ?? "",
	);
	const [addingAccount, setAddingAccount] = useState(false);
	const [addingPreset, setAddingPreset] = useState(
		hasAccounts && catalog.model_presets.length === 0,
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

	const showAccountForm = !hasAccounts || addingAccount;
	const verificationDestination =
		kind === "openrouter"
			? "https://openrouter.ai/api/v1"
			: kind === "anthropic"
				? "https://api.anthropic.com"
				: baseUrl.trim() || "the configured API base URL";
	const selectedProviderAccount = catalog.provider_accounts.find(
		(account) => account.id === providerId,
	);
	const [suggestions, setSuggestions] = useState<ModelSuggestion[]>([]);
	const suggestFrom =
		addingPreset && selectedProviderAccount?.kind === "openai-compatible"
			? selectedProviderAccount.id
			: null;
	useEffect(() => {
		setSuggestions([]);
		if (!suggestFrom) return;
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch(
					`/api/provider-accounts/${encodeURIComponent(suggestFrom)}/models`,
					{ signal: controller.signal },
				);
				if (!response.ok) return;
				const listed = (await response.json()) as ModelSuggestion[];
				setSuggestions(listed);
				// A single-model server, such as llama.cpp, needs no choice at all.
				if (listed.length === 1) setModel((current) => current || listed[0].id);
			} catch {
				// Suggestions are optional; the Model ID can still be typed.
			}
		})();
		return () => controller.abort();
	}, [suggestFrom]);
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
			setAddingAccount(false);
			if (next.model_presets.length === 0) {
				setPresetName("Course planning");
				setAddingPreset(true);
			}
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
			setAddingPreset(false);
			onPresetSaved?.();
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
		<div className="model-settings">
			{error ? (
				<div id="models-error">
					<Notice tone="error">{error}</Notice>
				</div>
			) : null}

			<section className="model-section" aria-labelledby="presets-heading">
				<div className="canvas-section-head">
					<h3 id="presets-heading">Model Presets</h3>
					{hasAccounts && !addingPreset ? (
						<button
							type="button"
							className="btn btn-small"
							onClick={() => {
								setError(null);
								setAddingPreset(true);
							}}
						>
							<Plus aria-hidden="true" />
							Add Model Preset
						</button>
					) : null}
				</div>
				{catalog.model_presets.length > 0 ? (
					<ul className="row-list">
						{catalog.model_presets.map((preset) => {
							const account = catalog.provider_accounts.find(
								(candidate) => candidate.id === preset.provider_account_id,
							);
							return (
								<li key={preset.id} className="row">
									<div className="row-main">
										<span className="row-title">{preset.name}</span>
										<span className="row-meta">
											<span className="mono">{preset.model}</span>
											<span>{account?.name ?? "Missing Provider Account"}</span>
										</span>
									</div>
									{preset.id === catalog.selected_model_id ? (
										<span className="state is-included">In use</span>
									) : null}
								</li>
							);
						})}
					</ul>
				) : (
					<p className="meta">
						{hasAccounts
							? "Add a Model Preset to choose which model the Course Agent uses."
							: "Add a Model Preset after saving a Provider Account."}
					</p>
				)}

				{hasAccounts && addingPreset ? (
					<form
						className="model-form panel panel-pad"
						onSubmit={addModel}
						aria-busy={busy}
					>
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
								placeholder={
									suggestions[0]?.id ??
									"nvidia/llama-3.3-nemotron-super-49b-v1:free"
								}
								required
								autoComplete="off"
								list={
									suggestions.length ? "provider-model-suggestions" : undefined
								}
								aria-describedby="preset-network-disclosure"
							/>
							{suggestions.length ? (
								<>
									<datalist id="provider-model-suggestions">
										{suggestions.map((suggestion) => (
											<option key={suggestion.id} value={suggestion.id}>
												{suggestionDetail(suggestion)}
											</option>
										))}
									</datalist>
									<small>
										{suggestions.length === 1
											? `${selectedProviderAccount?.name} serves one model${
													suggestionDetail(suggestions[0])
														? ` (${suggestionDetail(suggestions[0])})`
														: ""
												}.`
											: `${suggestions.length} models are available from ${selectedProviderAccount?.name}.`}
									</small>
								</>
							) : null}
						</div>
						<p
							className="field-help"
							id="preset-network-disclosure"
							role="note"
						>
							Saving sends this Model ID and uses the saved credential for{" "}
							{selectedProviderAccount?.name ?? "the selected Provider Account"}{" "}
							to contact{" "}
							{selectedProviderAccount?.base_url ?? "its API endpoint"} and
							verify capabilities.
							{suggestFrom
								? " The model list above was requested from the same endpoint."
								: null}
						</p>
						<div className="form-actions">
							{catalog.model_presets.length > 0 ? (
								<button
									type="button"
									className="btn"
									disabled={busy}
									onClick={() => setAddingPreset(false)}
								>
									Cancel
								</button>
							) : null}
							<button className="btn btn-primary" type="submit" disabled={busy}>
								{busy ? "Verifying…" : "Save Model Preset"}
							</button>
						</div>
					</form>
				) : null}
			</section>

			<section className="model-section" aria-labelledby="accounts-heading">
				<div className="canvas-section-head">
					<h3 id="accounts-heading">Provider Accounts</h3>
					{hasAccounts && !addingAccount ? (
						<button
							type="button"
							className="btn btn-small"
							onClick={() => {
								setError(null);
								setAddingAccount(true);
							}}
						>
							<Plus aria-hidden="true" />
							Add Provider Account
						</button>
					) : null}
				</div>
				{hasAccounts ? (
					<ul className="row-list">
						{catalog.provider_accounts.map((account) => {
							const presetCount = presetCountByProvider.get(account.id) ?? 0;
							const isEditing = editingProviderId === account.id;
							const isDeleting = deletingProviderId === account.id;
							return (
								<li key={account.id} className="model-account">
									<div className="row">
										<div className="row-main">
											<span className="row-title">{account.name}</span>
											<span className="row-meta">
												<span>{providerLabels[account.kind]}</span>
												<span className="mono">{account.base_url}</span>
											</span>
										</div>
										<div className="row-actions">
											<button
												className="btn btn-quiet btn-small"
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
												<KeyRound aria-hidden="true" />
												{isEditing ? "Cancel replacement" : "Replace key"}
											</button>
											<button
												className="icon-btn is-small is-danger"
												type="button"
												disabled={busy}
												aria-label={isDeleting ? "Cancel deletion" : "Delete"}
												title={
													isDeleting
														? "Cancel deletion"
														: `Delete ${account.name}`
												}
												aria-expanded={isDeleting}
												aria-controls={`delete-confirmation-${account.id}`}
												onClick={() => {
													setError(null);
													setEditingProviderId(null);
													setReplacementApiKey("");
													setDeletingProviderId(isDeleting ? null : account.id);
												}}
											>
												<Trash2 aria-hidden="true" />
											</button>
										</div>
									</div>
									{isEditing ? (
										<form
											id={`replacement-form-${account.id}`}
											className="model-inline-form"
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
												className="field-help"
												id={`replacement-disclosure-${account.id}`}
												role="note"
											>
												Saving sends this new key to {account.base_url} to
												verify it before storage. Existing Model Presets keep
												using this account.
											</p>
											<div className="form-actions">
												<button
													className="btn btn-primary btn-small"
													type="submit"
													disabled={busy}
												>
													{busy ? "Verifying…" : "Save new key"}
												</button>
											</div>
										</form>
									) : null}
									{isDeleting ? (
										<div
											id={`delete-confirmation-${account.id}`}
											className="model-inline-form model-delete-confirmation"
										>
											<p>
												Delete <strong>{account.name}</strong> and its saved
												credential?
												{presetCount > 0
													? ` This will also delete ${presetCount} attached Model ${presetCount === 1 ? "Preset" : "Presets"}.`
													: ""}
											</p>
											<div className="form-actions">
												<button
													className="btn btn-small"
													type="button"
													disabled={busy}
													onClick={() => setDeletingProviderId(null)}
												>
													Keep account
												</button>
												<button
													className="btn btn-danger btn-small"
													type="button"
													disabled={busy}
													onClick={() => void deleteProvider(account.id)}
												>
													{busy ? "Deleting…" : "Delete account"}
												</button>
											</div>
										</div>
									) : null}
								</li>
							);
						})}
					</ul>
				) : (
					<p className="meta">No Provider Accounts saved yet.</p>
				)}

				{showAccountForm ? (
					<form
						className="model-form panel panel-pad"
						onSubmit={addProvider}
						aria-busy={busy}
					>
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
									<label className="check-row">
										<input
											type="checkbox"
											checked={allowInsecureHttp}
											onChange={(event) =>
												setAllowInsecureHttp(event.target.checked)
											}
										/>
										<span>
											Allow HTTP to another host. Your API key and Course
											requests will be sent without encryption.
										</span>
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
							className="field-help"
							id="provider-network-disclosure"
							role="note"
						>
							Saving sends this key to {verificationDestination} to verify
							access before storing it outside every Course Workspace.
						</p>
						<div className="form-actions">
							{hasAccounts ? (
								<button
									type="button"
									className="btn"
									disabled={busy}
									onClick={() => setAddingAccount(false)}
								>
									Cancel
								</button>
							) : null}
							<button className="btn btn-primary" type="submit" disabled={busy}>
								{busy ? "Verifying…" : "Save Provider Account"}
							</button>
						</div>
					</form>
				) : null}
			</section>
		</div>
	);
}
