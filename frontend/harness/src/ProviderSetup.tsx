import { KeyRound, Plus, RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import { responseError, responseErrorDetail } from "./api";
import type {
	DetectedCredential,
	ModelCatalog,
	ProviderAccount,
	ProviderKind,
} from "./models";
import { Notice } from "./ui";

/** A model an OpenAI-compatible endpoint reports. */
type ModelSuggestion = {
	id: string;
	vision: boolean;
	context_window: number | null;
};

function contextLabel(tokens: number): string {
	return `${Math.round(tokens / 1024)}K context`;
}

function suggestionDetail(suggestion: ModelSuggestion): string {
	return [
		suggestion.context_window ? contextLabel(suggestion.context_window) : null,
		suggestion.vision ? "vision" : null,
	]
		.filter(Boolean)
		.join(" · ");
}

const providerLabels: Record<ProviderKind, string> = {
	openrouter: "OpenRouter",
	openai: "OpenAI",
	anthropic: "Anthropic",
	bedrock: "Amazon Bedrock",
	"openai-compatible": "OpenAI-compatible",
};

const fixedEndpoints: Partial<Record<ProviderKind, string>> = {
	openrouter: "https://openrouter.ai/api/v1",
	openai: "https://api.openai.com/v1",
	anthropic: "https://api.anthropic.com",
};

const modelPlaceholders: Record<ProviderKind, string> = {
	openrouter: "nvidia/llama-3.3-nemotron-super-49b-v1:free",
	openai: "gpt-4.1",
	anthropic: "claude-sonnet-4-5",
	bedrock: "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
	"openai-compatible": "local-model",
};

/** Where an account's credential comes from, when it is not a stored key. */
function credentialOrigin(account: ProviderAccount): string | null {
	if (account.credential_source === "aws-profile")
		return `AWS profile ${account.aws_profile}`;
	if (account.credential_source === "aws-environment")
		return "AWS session from the environment";
	return null;
}

/** Credentials found in the environment or AWS configuration, each added with one click. */
function DetectedCredentials({
	accountCount,
	disabled,
	onAdded,
	onError,
}: {
	accountCount: number;
	disabled: boolean;
	onAdded: (account: ProviderAccount) => Promise<void>;
	onError: (message: string | null) => void;
}) {
	const [detected, setDetected] = useState<DetectedCredential[]>([]);
	const [profiles, setProfiles] = useState<Record<string, string>>({});
	const [regions, setRegions] = useState<Record<string, string>>({});
	const [addingId, setAddingId] = useState<string | null>(null);

	// Adding or deleting an account changes what is still on offer.
	// biome-ignore lint/correctness/useExhaustiveDependencies: accountCount is the refresh signal
	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch("/api/provider-detections", {
					signal: controller.signal,
				});
				if (!response.ok) return;
				setDetected((await response.json()) as DetectedCredential[]);
			} catch {
				// Detection is a convenience; accounts can still be added by hand.
			}
		})();
		return () => controller.abort();
	}, [accountCount]);

	if (detected.length === 0) return null;

	function profileFor(item: DetectedCredential) {
		return profiles[item.id] ?? item.aws_profiles[0]?.name ?? "";
	}

	function regionFor(item: DetectedCredential) {
		const profileRegion = item.aws_profiles.find(
			(profile) => profile.name === profileFor(item),
		)?.region;
		return regions[item.id] ?? profileRegion ?? item.region ?? "us-east-1";
	}

	async function add(item: DetectedCredential) {
		setAddingId(item.id);
		onError(null);
		const profile =
			item.credential_source === "aws-profile" ? profileFor(item) : null;
		try {
			const response = await fetch(
				`/api/provider-detections/${encodeURIComponent(item.id)}`,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						name: profile ? `${item.name} (${profile})` : item.name,
						region: item.kind === "bedrock" ? regionFor(item) : null,
						aws_profile: profile,
					}),
				},
			);
			if (!response.ok) throw new Error(await responseError(response));
			await onAdded((await response.json()) as ProviderAccount);
		} catch (caught) {
			onError(
				caught instanceof Error
					? caught.message
					: "The detected credential was not added.",
			);
		} finally {
			setAddingId(null);
		}
	}

	return (
		<section className="model-detected" aria-labelledby="detected-heading">
			<h4 id="detected-heading">Found on this computer</h4>
			<p className="meta">
				Course Harness uses none of these until you add them.
			</p>
			<ul className="row-list">
				{detected.map((item) => {
					const busy = addingId === item.id;
					const stored = item.credential_source === "stored-key";
					return (
						<li key={item.id} className="model-account">
							<div className="row">
								<div className="row-main">
									<span className="row-title">{item.name}</span>
									<span className="row-meta">
										<span>{providerLabels[item.kind]}</span>
										<span>{item.origin}</span>
										{item.expires_at ? (
											<span>Expires {item.expires_at}</span>
										) : null}
									</span>
								</div>
								<div className="row-actions">
									<button
										type="button"
										className="btn btn-small"
										disabled={disabled || addingId !== null}
										aria-describedby={`detected-disclosure-${item.id}`}
										onClick={() => void add(item)}
									>
										<Plus aria-hidden="true" />
										{busy ? "Verifying…" : "Add"}
									</button>
								</div>
							</div>
							{item.kind === "bedrock" ? (
								<div className="model-inline-form model-detected-options">
									{item.credential_source === "aws-profile" ? (
										<div className="field">
											<label htmlFor={`detected-profile-${item.id}`}>
												AWS profile
											</label>
											<select
												id={`detected-profile-${item.id}`}
												value={profileFor(item)}
												onChange={(event) =>
													setProfiles((current) => ({
														...current,
														[item.id]: event.target.value,
													}))
												}
											>
												{item.aws_profiles.map((profile) => (
													<option key={profile.name} value={profile.name}>
														{profile.name}
													</option>
												))}
											</select>
										</div>
									) : null}
									<div className="field">
										<label htmlFor={`detected-region-${item.id}`}>
											AWS region
										</label>
										<input
											id={`detected-region-${item.id}`}
											value={regionFor(item)}
											onChange={(event) =>
												setRegions((current) => ({
													...current,
													[item.id]: event.target.value.trim(),
												}))
											}
											autoComplete="off"
										/>
									</div>
								</div>
							) : null}
							<p
								className="field-help model-detected-disclosure"
								id={`detected-disclosure-${item.id}`}
							>
								{stored
									? "Adding copies this key into credential storage and verifies it with the provider."
									: item.credential_source === "aws-profile"
										? "Adding stores only the profile name. The AWS SDK reads your AWS configuration each time, so a refreshed sign-in keeps working."
										: "Adding stores nothing: these session credentials are read from the environment each time and stop working when they expire."}
							</p>
						</li>
					);
				})}
			</ul>
		</section>
	);
}

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
	const [region, setRegion] = useState("us-east-1");
	const [awsAuth, setAwsAuth] = useState<"stored-key" | "aws-profile">(
		"stored-key",
	);
	const [awsProfile, setAwsProfile] = useState("");
	const [baseUrl, setBaseUrl] = useState("");
	const [allowInsecureHttp, setAllowInsecureHttp] = useState(false);
	const [apiKey, setApiKey] = useState("");
	const [presetName, setPresetName] = useState(
		hasAccounts && catalog.model_presets.length === 0 ? "Course planning" : "",
	);
	const [model, setModel] = useState("");
	// Shown once the provider turns out not to report the model's context window.
	const [askContextWindow, setAskContextWindow] = useState(false);
	const [contextWindow, setContextWindow] = useState("");
	// Shown once the model ID turns out not to tell whether prompts can be cached.
	const [askPromptCaching, setAskPromptCaching] = useState(false);
	const [promptCaching, setPromptCaching] = useState(false);
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
	const [verifyingId, setVerifyingId] = useState<string | null>(null);
	const [verified, setVerified] = useState<string | null>(null);

	const showAccountForm = !hasAccounts || addingAccount;
	const usesAwsProfile = kind === "bedrock" && awsAuth === "aws-profile";
	const verificationDestination =
		kind === "bedrock"
			? `Amazon Bedrock in ${region.trim() || "the selected region"}`
			: (fixedEndpoints[kind] ??
				(baseUrl.trim() || "the configured API base URL"));
	const selectedProviderAccount = catalog.provider_accounts.find(
		(account) => account.id === providerId,
	);
	const [suggestions, setSuggestions] = useState<ModelSuggestion[]>([]);
	const suggestFrom =
		addingPreset &&
		(selectedProviderAccount?.kind === "openai-compatible" ||
			selectedProviderAccount?.kind === "bedrock")
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

	/** Continue to the first Model Preset once an account exists. */
	async function accountSaved(account: { id: string }) {
		const next = await reloadCatalog();
		setProviderId(account.id);
		setAddingAccount(false);
		if (next.model_presets.length === 0) {
			setPresetName("Course planning");
			setAddingPreset(true);
		}
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
					api_key: usesAwsProfile ? null : apiKey,
					base_url: kind === "openai-compatible" ? baseUrl.trim() : null,
					allow_insecure_http:
						kind === "openai-compatible" && allowInsecureHttp,
					credential_source: usesAwsProfile ? "aws-profile" : "stored-key",
					region: kind === "bedrock" ? region.trim() : null,
					aws_profile: usesAwsProfile ? awsProfile.trim() : null,
				}),
			});
			if (!response.ok) throw new Error(await responseError(response));
			const account = (await response.json()) as { id: string };
			setApiKey("");
			setAllowInsecureHttp(false);
			await accountSaved(account);
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
					context_window:
						askContextWindow && contextWindow ? Number(contextWindow) : null,
					prompt_caching: askPromptCaching ? promptCaching : null,
				}),
			});
			if (!response.ok) {
				const detail = await responseErrorDetail(response);
				if (detail.code === "context_window_unknown") setAskContextWindow(true);
				if (detail.code === "prompt_caching_unknown") setAskPromptCaching(true);
				throw new Error(detail.message);
			}
			await reloadCatalog();
			setPresetName("");
			setModel("");
			resetModelQuestions();
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

	function resetModelQuestions() {
		setAskContextWindow(false);
		setContextWindow("");
		setAskPromptCaching(false);
		setPromptCaching(false);
	}

	/** Re-check a saved Model Preset, e.g. after its server gained vision support. */
	async function verifyPreset(presetId: string, name: string) {
		setVerifyingId(presetId);
		setError(null);
		setVerified(null);
		try {
			const response = await fetch(
				`/api/models/${encodeURIComponent(presetId)}/verify`,
				{ method: "POST" },
			);
			if (!response.ok) throw new Error(await responseError(response));
			const next = (await response.json()) as ModelCatalog;
			onCatalogChange(next);
			const preset = next.model_presets.find((item) => item.id === presetId);
			setVerified(
				`${name} was checked again${
					preset?.capabilities.vision
						? " and can look at images."
						: ". It does not report image input."
				}`,
			);
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The Model Preset could not be checked.",
			);
		} finally {
			setVerifyingId(null);
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
			{verified ? <Notice role="status">{verified}</Notice> : null}

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
											<span>
												{preset.capabilities.vision
													? "Sees images"
													: "Text only"}
											</span>
											<span
												title={
													preset.entered_context_window
														? "You entered this context size"
														: undefined
												}
											>
												{contextLabel(preset.capabilities.context_window)}
											</span>
											{preset.capabilities.prompt_caching ? (
												<span
													title={
														preset.entered_prompt_caching
															? "You said this model supports prompt caching"
															: undefined
													}
												>
													Prompt caching
												</span>
											) : null}
										</span>
									</div>
									{preset.id === catalog.selected_model_id ? (
										<span className="state is-included">In use</span>
									) : null}
									<button
										type="button"
										className="icon-btn is-small"
										aria-label={`Check ${preset.name} again`}
										title="Check capabilities again"
										disabled={busy || verifyingId !== null || !account}
										onClick={() => void verifyPreset(preset.id, preset.name)}
									>
										<RefreshCw
											aria-hidden="true"
											className={verifyingId === preset.id ? "spin" : undefined}
										/>
									</button>
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
									modelPlaceholders[
										selectedProviderAccount?.kind ?? "openrouter"
									]
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
						{askContextWindow ? (
							<div className="field">
								<label htmlFor="provider-context-window">
									Context size (tokens)
								</label>
								<input
									id="provider-context-window"
									type="number"
									inputMode="numeric"
									min={16384}
									step={1}
									value={contextWindow}
									onChange={(event) => setContextWindow(event.target.value)}
									placeholder="32768"
									required
									aria-describedby="provider-context-window-help"
								/>
								<small id="provider-context-window-help">
									{selectedProviderAccount?.name ?? "This provider"} does not
									report how much context the model accepts. Enter the size the
									server runs it with, such as vLLM’s --max-model-len.
								</small>
							</div>
						) : null}
						{askPromptCaching ? (
							<div className="field">
								<label className="check-row">
									<input
										type="checkbox"
										checked={promptCaching}
										onChange={(event) => setPromptCaching(event.target.checked)}
										aria-describedby="provider-prompt-caching-help"
									/>
									<span>This model supports prompt caching</span>
								</label>
								<small id="provider-prompt-caching-help">
									The Model ID does not reveal the model family, as with an
									application inference profile ARN. When caching is on,
									repeated instructions, tools and conversation are cached,
									which lowers cost and latency. Leave it off if you are unsure:
									a model without caching support rejects cached requests.
								</small>
							</div>
						) : null}
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
							{selectedProviderAccount?.kind === "bedrock"
								? " Amazon Bedrock is asked for one short test reply, billed to that AWS account."
								: null}
						</p>
						<div className="form-actions">
							{catalog.model_presets.length > 0 ? (
								<button
									type="button"
									className="btn"
									disabled={busy}
									onClick={() => {
										resetModelQuestions();
										setAddingPreset(false);
									}}
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
												{credentialOrigin(account) ? (
													<span>{credentialOrigin(account)}</span>
												) : null}
												<span className="mono">{account.base_url}</span>
											</span>
										</div>
										<div className="row-actions">
											{credentialOrigin(account) ? null : (
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
											)}
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

				<DetectedCredentials
					accountCount={catalog.provider_accounts.length}
					disabled={busy}
					onAdded={accountSaved}
					onError={setError}
				/>

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
								<option value="openai">OpenAI</option>
								<option value="anthropic">Anthropic</option>
								<option value="bedrock">Amazon Bedrock</option>
								<option value="openai-compatible">OpenAI-compatible</option>
							</select>
						</div>
						{kind === "bedrock" ? (
							<>
								<div className="field">
									<label htmlFor="provider-region">AWS region</label>
									<input
										id="provider-region"
										value={region}
										onChange={(event) => setRegion(event.target.value)}
										placeholder="us-east-1"
										required
										autoComplete="off"
									/>
								</div>
								<div className="field">
									<label htmlFor="provider-aws-auth">Sign in with</label>
									<select
										id="provider-aws-auth"
										value={awsAuth}
										onChange={(event) =>
											setAwsAuth(
												event.target.value as "stored-key" | "aws-profile",
											)
										}
									>
										<option value="stored-key">Bedrock API key</option>
										<option value="aws-profile">AWS profile</option>
									</select>
								</div>
								{usesAwsProfile ? (
									<div className="field">
										<label htmlFor="provider-aws-profile">
											AWS profile name
										</label>
										<input
											id="provider-aws-profile"
											value={awsProfile}
											onChange={(event) => setAwsProfile(event.target.value)}
											placeholder="default"
											required
											autoComplete="off"
										/>
									</div>
								) : null}
							</>
						) : null}
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
						{usesAwsProfile ? null : (
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
						)}
						<p
							className="field-help"
							id="provider-network-disclosure"
							role="note"
						>
							{usesAwsProfile
								? "Saving asks AWS to confirm who this profile signs in as. Only the profile name is stored; the AWS SDK reads your AWS configuration each time the model is used."
								: `Saving sends this key to ${verificationDestination} to verify access before storing it outside every Course Workspace.`}
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
