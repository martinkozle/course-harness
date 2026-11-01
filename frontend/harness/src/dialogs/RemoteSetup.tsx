import { Dialog, errorMessage, Notice } from "@course-harness/ui";
import { type FormEvent, useEffect, useId, useState } from "react";

import { responseError } from "../api";
import type { GitHubSetup, RemoteSettings } from "../models";

async function request<T>(path: string, method: string, body?: object) {
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
	return (await response.json()) as T;
}

type Mode = "create" | "existing";

/**
 * Choose where your Course is shared: a new GitHub repository made with your
 * own GitHub CLI, or the Git URL of a repository you already have.
 */
export function RemoteSetup({
	onSaved,
	onCancel,
	submitLabel = "Save",
}: {
	onSaved: (settings: RemoteSettings) => void;
	onCancel?: () => void;
	submitLabel?: string;
}) {
	const [github, setGithub] = useState<GitHubSetup | null>(null);
	const [mode, setMode] = useState<Mode>("existing");
	const [name, setName] = useState("");
	const [visibility, setVisibility] = useState<"public" | "private">("public");
	const [url, setUrl] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const id = useId();

	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch("/api/workspace/remote/github", {
					signal: controller.signal,
				});
				if (!response.ok) return;
				const setup = (await response.json()) as GitHubSetup;
				setGithub(setup);
				setName(setup.suggested_name);
				if (setup.available) setMode("create");
			} catch {
				// Pasting a URL always works.
			}
		})();
		return () => controller.abort();
	}, []);

	async function save(event: FormEvent) {
		event.preventDefault();
		setSaving(true);
		setError(null);
		try {
			const saved =
				mode === "create"
					? await request<RemoteSettings>(
							"/api/workspace/remote/github",
							"POST",
							{ name: name.trim(), visibility },
						)
					: await request<RemoteSettings>("/api/workspace/remote", "PUT", {
							url: url.trim(),
						});
			onSaved(saved);
		} catch (caught) {
			setError(
				errorMessage(
					caught,
					mode === "create"
						? "The GitHub repository could not be created."
						: "The repository address could not be saved.",
				),
			);
		} finally {
			setSaving(false);
		}
	}

	const canSave =
		!saving && (mode === "create" ? Boolean(name.trim()) : Boolean(url.trim()));

	return (
		<form className="remote-setup" onSubmit={(event) => void save(event)}>
			{github?.available ? (
				<fieldset className="remote-setup-modes">
					<legend className="visually-hidden">Where to share</legend>
					<label className="check-row">
						<input
							type="radio"
							name={`${id}-mode`}
							checked={mode === "create"}
							onChange={() => setMode("create")}
						/>
						Create a GitHub repository
					</label>
					<label className="check-row">
						<input
							type="radio"
							name={`${id}-mode`}
							checked={mode === "existing"}
							onChange={() => setMode("existing")}
						/>
						Use a repository you already have
					</label>
				</fieldset>
			) : null}

			{mode === "create" && github?.available ? (
				<div className="remote-setup-create">
					<div className="field">
						<label htmlFor={`${id}-name`}>Repository name</label>
						<div className="remote-setup-owner">
							<span className="mono" aria-hidden="true">
								{github.owner}/
							</span>
							<input
								id={`${id}-name`}
								className="mono"
								value={name}
								maxLength={100}
								spellCheck={false}
								autoComplete="off"
								onChange={(event) => setName(event.target.value)}
							/>
						</div>
					</div>
					<fieldset className="remote-setup-visibility">
						<legend>Visibility</legend>
						<label className="check-row">
							<input
								type="radio"
								name={`${id}-visibility`}
								checked={visibility === "public"}
								onChange={() => setVisibility("public")}
							/>
							Public
						</label>
						<label className="check-row">
							<input
								type="radio"
								name={`${id}-visibility`}
								checked={visibility === "private"}
								onChange={() => setVisibility("private")}
							/>
							Private
						</label>
						<small>A Classroom can only read public repositories.</small>
					</fieldset>
				</div>
			) : (
				<div className="field">
					<label htmlFor={`${id}-url`}>Repository address</label>
					<input
						id={`${id}-url`}
						className="mono"
						value={url}
						maxLength={2048}
						placeholder="git@github.com:you/course.git"
						spellCheck={false}
						autoComplete="off"
						aria-describedby={`${id}-url-help`}
						onChange={(event) => setUrl(event.target.value)}
					/>
					<small id={`${id}-url-help`}>
						An HTTPS or SSH address of an empty repository. Leave passwords and
						tokens out; Git signs in with your SSH key or credential helper.
					</small>
				</div>
			)}

			{github && !github.available ? (
				<p className="meta remote-setup-hint">
					{github.reason === "missing"
						? "To create a GitHub repository from here, install the GitHub CLI (gh) and sign in."
						: "To create a GitHub repository from here, sign in with gh auth login in a terminal."}
				</p>
			) : null}

			{error ? (
				<Notice tone="error" role="alert">
					{error}
				</Notice>
			) : null}

			<div className="remote-setup-actions">
				{onCancel ? (
					<button
						type="button"
						className="btn"
						onClick={onCancel}
						disabled={saving}
					>
						Cancel
					</button>
				) : null}
				<button type="submit" className="btn btn-primary" disabled={!canSave}>
					{saving
						? mode === "create"
							? "Creating…"
							: "Saving…"
						: mode === "create"
							? "Create repository"
							: submitLabel}
				</button>
			</div>
		</form>
	);
}

export function RemoteSetupDialog({
	onSaved,
	onClose,
}: {
	onSaved: (settings: RemoteSettings) => void;
	onClose: () => void;
}) {
	return (
		<Dialog title="Share to GitHub" onClose={onClose}>
			<p className="meta remote-setup-intro">
				Sync and Publish send your Course, its history and every release to this
				repository, where a Classroom can read them.
			</p>
			<RemoteSetup onSaved={onSaved} onCancel={onClose} />
		</Dialog>
	);
}

/** Settings: see, change or remove where your Course is shared. */
export function SharingSettings({ onChange }: { onChange?: () => void }) {
	const [settings, setSettings] = useState<RemoteSettings | null>(null);
	const [editing, setEditing] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [removing, setRemoving] = useState(false);

	useEffect(() => {
		request<RemoteSettings>("/api/workspace/remote", "GET")
			.then(setSettings)
			.catch((caught) =>
				setError(errorMessage(caught, "Sharing settings could not be read.")),
			);
	}, []);

	async function remove() {
		setRemoving(true);
		setError(null);
		try {
			setSettings(
				await request<RemoteSettings>("/api/workspace/remote", "PUT", {
					url: null,
				}),
			);
			onChange?.();
		} catch (caught) {
			setError(errorMessage(caught, "Sharing could not be turned off."));
		} finally {
			setRemoving(false);
		}
	}

	return (
		<div className="settings-block">
			<h3>Shared repository</h3>
			{error ? <Notice tone="error">{error}</Notice> : null}
			{settings === null && !error ? (
				<p className="meta" role="status">
					Reading…
				</p>
			) : null}
			{settings && !editing ? (
				settings.url ? (
					<>
						<p className="mono settings-path">{settings.url}</p>
						<p className="meta">
							Sync and Publish send your Course and its releases here.
						</p>
						<div className="remote-setup-actions">
							<button
								type="button"
								className="btn btn-small"
								onClick={() => setEditing(true)}
							>
								Change
							</button>
							<button
								type="button"
								className="btn btn-small btn-quiet"
								disabled={removing}
								onClick={() => void remove()}
							>
								{removing ? "Removing…" : "Stop sharing"}
							</button>
						</div>
					</>
				) : (
					<>
						<p className="meta">
							Your Course is not shared yet. Share it to a GitHub repository so
							a Classroom can serve your releases.
						</p>
						<button
							type="button"
							className="btn btn-small"
							onClick={() => setEditing(true)}
						>
							Set up sharing
						</button>
					</>
				)
			) : null}
			{editing ? (
				<RemoteSetup
					onSaved={(saved) => {
						setSettings(saved);
						setEditing(false);
						onChange?.();
					}}
					onCancel={() => setEditing(false)}
				/>
			) : null}
		</div>
	);
}
