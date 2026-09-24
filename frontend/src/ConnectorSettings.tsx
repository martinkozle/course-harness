import { Pencil, Plug, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import { responseError } from "./api";
import type { Connector, ConnectorInput, ConnectorTool } from "./models";
import { errorMessage, Notice } from "./ui";

type HeaderDraft = {
	key: string;
	name: string;
	value: string;
	secret: boolean;
	configured: boolean;
};

type Draft = {
	name: string;
	url: string;
	urlSecret: boolean;
	headers: HeaderDraft[];
};

export const storageLabels: Record<string, string> = {
	"os-keyring": "Keys are in the operating-system keyring.",
	"private-json-file": "Keys are in a private file on this computer.",
	unavailable: "Saved keys are unavailable. Enter them again.",
};

function inputFor(connector: Connector, changes: Partial<ConnectorInput> = {}) {
	return {
		name: connector.name,
		url: connector.url_secret ? "" : connector.url,
		url_secret: connector.url_secret,
		headers: connector.headers.map((header) => ({
			name: header.name,
			value: header.secret ? "" : header.value,
			secret: header.secret,
		})),
		enabled: connector.enabled,
		hidden_tools: connector.hidden_tools,
		fetch_tool: connector.fetch_tool,
		...changes,
	} satisfies ConnectorInput;
}

function draftFor(connector: Connector | null): Draft {
	return {
		name: connector?.name ?? "",
		url: connector && !connector.url_secret ? connector.url : "",
		urlSecret: connector?.url_secret ?? false,
		headers: (connector?.headers ?? []).map((header) => ({
			key: crypto.randomUUID(),
			name: header.name,
			value: header.secret ? "" : (header.value ?? ""),
			secret: header.secret,
			configured: header.configured,
		})),
	};
}

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
	return response;
}

/** Remote MCP servers whose tools the Course Agent may use for research. */
export function ConnectorSettings() {
	const [connectors, setConnectors] = useState<Connector[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [editing, setEditing] = useState<string | "new" | null>(null);
	const [deleting, setDeleting] = useState<string | null>(null);
	const [tools, setTools] = useState<Record<string, ConnectorTool[]>>({});
	const [testing, setTesting] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	async function reload() {
		const response = await send("/api/connectors", "GET");
		setConnectors((await response.json()) as Connector[]);
	}

	// biome-ignore lint/correctness/useExhaustiveDependencies: load once when the section opens
	useEffect(() => {
		reload().catch((caught) =>
			setError(errorMessage(caught, "Connectors could not be loaded.")),
		);
	}, []);

	async function act(action: () => Promise<void>, fallback: string) {
		setBusy(true);
		setError(null);
		try {
			await action();
		} catch (caught) {
			setError(errorMessage(caught, fallback));
		} finally {
			setBusy(false);
		}
	}

	function update(connector: Connector, changes: Partial<ConnectorInput>) {
		return act(async () => {
			await send(
				`/api/connectors/${connector.id}`,
				"PUT",
				inputFor(connector, changes),
			);
			await reload();
		}, `${connector.name} could not be saved.`);
	}

	async function test(connector: Connector) {
		setTesting(connector.id);
		setError(null);
		try {
			const response = await send(
				`/api/connectors/${connector.id}/test`,
				"POST",
			);
			const result = (await response.json()) as { tools: ConnectorTool[] };
			setTools((current) => ({ ...current, [connector.id]: result.tools }));
		} catch (caught) {
			setError(errorMessage(caught, `${connector.name} could not be reached.`));
		} finally {
			setTesting(null);
		}
	}

	const hasDefault = connectors?.some((connector) => connector.preset);

	return (
		<div className="model-settings">
			<section className="model-section" aria-labelledby="connectors-heading">
				<div className="canvas-section-head">
					<h3 id="connectors-heading">Web research</h3>
					{editing !== "new" ? (
						<button
							type="button"
							className="btn btn-small"
							disabled={busy}
							onClick={() => {
								setError(null);
								setEditing("new");
							}}
						>
							<Plus aria-hidden="true" />
							Add Connector
						</button>
					) : null}
				</div>
				<p className="meta" role="note">
					Connectors are web services the Course Agent can use to search and
					read the web. Your requests, including search queries, are sent to
					each enabled Connector. Settings and keys stay on this computer,
					outside every Course folder. Anything a Connector finds is a result,
					not a Source, until it is added to the course.
				</p>
				{error ? (
					<Notice tone="error" role="alert">
						{error}
					</Notice>
				) : null}
				{connectors === null && !error ? (
					<p className="meta" role="status">
						Loading Connectors…
					</p>
				) : null}
				{connectors?.length === 0 ? (
					<p className="meta">No Connectors. Web search is unavailable.</p>
				) : null}
				{connectors?.length ? (
					<ul className="row-list">
						{connectors.map((connector) => (
							<ConnectorRow
								key={connector.id}
								connector={connector}
								busy={busy}
								testing={testing === connector.id}
								tools={tools[connector.id]}
								editing={editing === connector.id}
								deleting={deleting === connector.id}
								onEdit={() => {
									setError(null);
									setDeleting(null);
									setEditing(editing === connector.id ? null : connector.id);
								}}
								onDelete={() => {
									setError(null);
									setEditing(null);
									setDeleting(deleting === connector.id ? null : connector.id);
								}}
								onConfirmDelete={() =>
									act(async () => {
										await send(`/api/connectors/${connector.id}`, "DELETE");
										setDeleting(null);
										await reload();
									}, `${connector.name} could not be deleted.`)
								}
								onTest={() => void test(connector)}
								onUpdate={(changes) => void update(connector, changes)}
								onSave={(input) =>
									act(async () => {
										await send(`/api/connectors/${connector.id}`, "PUT", input);
										setEditing(null);
										await reload();
									}, `${connector.name} could not be saved.`)
								}
							/>
						))}
					</ul>
				) : null}
				{editing === "new" ? (
					<ConnectorForm
						connector={null}
						busy={busy}
						onCancel={() => setEditing(null)}
						onSave={(input) =>
							act(async () => {
								await send("/api/connectors", "POST", input);
								setEditing(null);
								await reload();
							}, "The Connector could not be added.")
						}
					/>
				) : null}
				{connectors && !hasDefault ? (
					<button
						type="button"
						className="btn btn-quiet btn-small"
						disabled={busy}
						onClick={() =>
							void act(async () => {
								const response = await send(
									"/api/connectors/restore-defaults",
									"POST",
								);
								setConnectors((await response.json()) as Connector[]);
							}, "Exa could not be restored.")
						}
					>
						<RotateCcw aria-hidden="true" />
						Restore Exa
					</button>
				) : null}
			</section>
		</div>
	);
}

function ConnectorRow({
	connector,
	busy,
	testing,
	tools,
	editing,
	deleting,
	onEdit,
	onDelete,
	onConfirmDelete,
	onTest,
	onUpdate,
	onSave,
}: {
	connector: Connector;
	busy: boolean;
	testing: boolean;
	tools: ConnectorTool[] | undefined;
	editing: boolean;
	deleting: boolean;
	onEdit: () => void;
	onDelete: () => void;
	onConfirmDelete: () => Promise<void>;
	onTest: () => void;
	onUpdate: (changes: Partial<ConnectorInput>) => void;
	onSave: (input: ConnectorInput) => Promise<void>;
}) {
	const secrets = connector.headers.filter((header) => header.secret);
	const keyState =
		connector.preset === "exa" && !secrets.some((header) => header.configured)
			? "No key: free, rate-limited use"
			: secrets.some((header) => header.configured)
				? "Key saved"
				: null;
	const hidden = new Set(connector.hidden_tools);
	return (
		<li className="model-account">
			<div className="row">
				<div className="row-main">
					<span className="row-title">{connector.name}</span>
					<span className="row-meta">
						<span className="mono">
							{connector.url}
							{connector.url_secret ? "/…" : ""}
						</span>
						{keyState ? <span>{keyState}</span> : null}
						{!connector.enabled ? <span className="state">Off</span> : null}
					</span>
				</div>
				<div className="row-actions">
					<label className="check-row">
						<input
							type="checkbox"
							checked={connector.enabled}
							disabled={busy}
							onChange={(event) => onUpdate({ enabled: event.target.checked })}
						/>
						<span>Enabled</span>
					</label>
					<button
						type="button"
						className="btn btn-quiet btn-small"
						disabled={busy || testing}
						onClick={onTest}
					>
						<Plug aria-hidden="true" />
						{testing ? "Connecting…" : "Test"}
					</button>
					<button
						type="button"
						className="btn btn-quiet btn-small"
						disabled={busy}
						aria-expanded={editing}
						onClick={onEdit}
					>
						<Pencil aria-hidden="true" />
						{editing ? "Close" : "Edit"}
					</button>
					<button
						type="button"
						className="icon-btn is-small is-danger"
						disabled={busy}
						aria-label={
							deleting ? "Cancel deletion" : `Delete ${connector.name}`
						}
						title={deleting ? "Cancel deletion" : `Delete ${connector.name}`}
						aria-expanded={deleting}
						onClick={onDelete}
					>
						<Trash2 aria-hidden="true" />
					</button>
				</div>
			</div>
			{tools ? (
				<div className="model-inline-form connector-tools">
					<p className="meta">
						{connector.name} offers {tools.length} tool
						{tools.length === 1 ? "" : "s"}. Choose which the Course Agent may
						use, and which one reads a page when a site blocks direct downloads.
					</p>
					<ul>
						{tools.map((tool) => (
							<li key={tool.name}>
								<label className="check-row">
									<input
										type="checkbox"
										checked={!hidden.has(tool.name)}
										disabled={busy}
										onChange={(event) => {
											const next = new Set(hidden);
											if (event.target.checked) next.delete(tool.name);
											else next.add(tool.name);
											onUpdate({ hidden_tools: [...next] });
										}}
									/>
									<span>
										<span className="mono">{tool.name}</span>
										{tool.description ? (
											<span className="connector-tool-description">
												{tool.description.split("\n")[0]}
											</span>
										) : null}
									</span>
								</label>
							</li>
						))}
					</ul>
					<div className="field">
						<label htmlFor={`fetch-tool-${connector.id}`}>Page reader</label>
						<select
							id={`fetch-tool-${connector.id}`}
							value={connector.fetch_tool ?? ""}
							disabled={busy}
							onChange={(event) =>
								onUpdate({ fetch_tool: event.target.value || null })
							}
						>
							<option value="">None</option>
							{tools.map((tool) => (
								<option key={tool.name} value={tool.name}>
									{tool.name}
								</option>
							))}
						</select>
					</div>
				</div>
			) : null}
			{editing ? (
				<ConnectorForm
					connector={connector}
					busy={busy}
					onCancel={onEdit}
					onSave={onSave}
				/>
			) : null}
			{deleting ? (
				<div className="model-inline-form model-delete-confirmation">
					<p>
						Delete <strong>{connector.name}</strong> and its saved keys?
						{connector.preset ? " You can restore it later." : ""}
					</p>
					<div className="form-actions">
						<button
							className="btn btn-small"
							type="button"
							disabled={busy}
							onClick={onDelete}
						>
							Keep Connector
						</button>
						<button
							className="btn btn-danger btn-small"
							type="button"
							disabled={busy}
							onClick={() => void onConfirmDelete()}
						>
							{busy ? "Deleting…" : "Delete Connector"}
						</button>
					</div>
				</div>
			) : null}
		</li>
	);
}

function ConnectorForm({
	connector,
	busy,
	onCancel,
	onSave,
}: {
	connector: Connector | null;
	busy: boolean;
	onCancel: () => void;
	onSave: (input: ConnectorInput) => Promise<void>;
}) {
	const [draft, setDraft] = useState<Draft>(() => draftFor(connector));
	const prefix = connector?.id ?? "new-connector";

	function setHeader(key: string, changes: Partial<HeaderDraft>) {
		setDraft((current) => ({
			...current,
			headers: current.headers.map((header) =>
				header.key === key ? { ...header, ...changes } : header,
			),
		}));
	}

	function submit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		void onSave({
			name: draft.name.trim(),
			url: draft.url.trim(),
			url_secret: draft.urlSecret,
			headers: draft.headers
				.filter((header) => header.name.trim())
				.map((header) => ({
					name: header.name.trim(),
					value: header.value,
					secret: header.secret,
				})),
			enabled: connector?.enabled ?? true,
			hidden_tools: connector?.hidden_tools ?? [],
			fetch_tool: connector?.fetch_tool ?? null,
		});
	}

	const storage = connector?.credential_storage
		? storageLabels[connector.credential_storage]
		: null;

	return (
		<form
			className={connector ? "model-inline-form" : "model-form panel panel-pad"}
			onSubmit={submit}
			aria-busy={busy}
		>
			<div className="field">
				<label htmlFor={`${prefix}-name`}>Name</label>
				<input
					id={`${prefix}-name`}
					value={draft.name}
					onChange={(event) => setDraft({ ...draft, name: event.target.value })}
					required
				/>
			</div>
			<div className="field">
				<label htmlFor={`${prefix}-url`}>MCP server URL</label>
				<input
					id={`${prefix}-url`}
					type={draft.urlSecret ? "password" : "url"}
					value={draft.url}
					placeholder={
						connector?.url_secret
							? "Saved. Leave blank to keep it."
							: "https://mcp.example.com/mcp"
					}
					onChange={(event) => setDraft({ ...draft, url: event.target.value })}
					required={!connector?.url_secret}
					autoComplete="off"
				/>
			</div>
			<label className="check-row">
				<input
					type="checkbox"
					checked={draft.urlSecret}
					onChange={(event) =>
						setDraft({ ...draft, urlSecret: event.target.checked })
					}
				/>
				<span>The URL contains a key. Store it as a secret.</span>
			</label>
			<fieldset className="connector-headers">
				<legend>Headers</legend>
				{draft.headers.map((header) => (
					<div key={header.key} className="connector-header">
						<input
							aria-label="Header name"
							placeholder="Name"
							value={header.name}
							onChange={(event) =>
								setHeader(header.key, { name: event.target.value })
							}
						/>
						<input
							aria-label={`${header.name || "Header"} value`}
							type={header.secret ? "password" : "text"}
							placeholder={
								header.secret && header.configured
									? "Saved. Leave blank to keep it."
									: "Value"
							}
							value={header.value}
							onChange={(event) =>
								setHeader(header.key, { value: event.target.value })
							}
							autoComplete="off"
						/>
						<label className="check-row">
							<input
								type="checkbox"
								checked={header.secret}
								onChange={(event) =>
									setHeader(header.key, { secret: event.target.checked })
								}
							/>
							<span>Secret</span>
						</label>
						<button
							type="button"
							className="icon-btn is-small"
							aria-label={`Remove ${header.name || "header"}`}
							onClick={() =>
								setDraft((current) => ({
									...current,
									headers: current.headers.filter(
										(item) => item.key !== header.key,
									),
								}))
							}
						>
							<X aria-hidden="true" />
						</button>
					</div>
				))}
				<button
					type="button"
					className="btn btn-quiet btn-small"
					onClick={() =>
						setDraft((current) => ({
							...current,
							headers: [
								...current.headers,
								{
									key: crypto.randomUUID(),
									name: "",
									value: "",
									secret: true,
									configured: false,
								},
							],
						}))
					}
				>
					<Plus aria-hidden="true" />
					Add header
				</button>
			</fieldset>
			<p className="field-help" role="note">
				{connector?.preset === "exa"
					? "Exa works without a key at a free, rate-limited tier. To raise the limits, put your Exa API key in the x-api-key header. "
					: "Put an API key in a secret header, for example Authorization with the value Bearer and your key. "}
				{storage}
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
				<button className="btn btn-primary" type="submit" disabled={busy}>
					{busy ? "Saving…" : connector ? "Save Connector" : "Add Connector"}
				</button>
			</div>
		</form>
	);
}
