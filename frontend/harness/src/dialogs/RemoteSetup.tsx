import { Dialog, errorMessage, isAbort, Notice } from "@course-harness/ui";
import { Loader2 } from "lucide-react";
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

const REPOSITORY_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/;

/**
 * Choose your Course's Git remote: a new GitHub repository made with your
 * own GitHub CLI, or the Git URL of a repository you already have.
 */
export function RemoteSetup({
	onSaved,
	onCancel,
	submitLabel = "Save",
	onHostChange,
}: {
	onSaved: (settings: RemoteSettings) => void;
	onCancel?: () => void;
	submitLabel?: string;
	/** Whether the repository will be on GitHub, for the surrounding title. */
	onHostChange?: (github: boolean) => void;
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
				if (!response.ok) throw new Error();
				const setup = (await response.json()) as GitHubSetup;
				setName(setup.suggested_name);
				setMode(setup.available ? "create" : "existing");
				setGithub(setup);
			} catch (caught) {
				if (isAbort(caught)) return;
				// Pasting a URL always works.
				setGithub({
					available: false,
					reason: null,
					owner: null,
					protocol: "https",
					suggested_name: "",
				});
			}
		})();
		return () => controller.abort();
	}, []);

	const onGithub = github !== null && mode === "create";
	const toGithub = onGithub || /github\.com/i.test(url);
	useEffect(() => onHostChange?.(toGithub), [toGithub, onHostChange]);

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
		!saving &&
		(mode === "create"
			? REPOSITORY_NAME.test(name.trim())
			: Boolean(url.trim()));

	if (github === null) {
		return (
			<div className="remote-setup">
				<p className="meta remote-setup-checking" role="status">
					<Loader2 className="spin" aria-hidden="true" />
					Checking GitHub CLI…
				</p>
				{onCancel ? (
					<div className="remote-setup-actions">
						<button type="button" className="btn" onClick={onCancel}>
							Cancel
						</button>
					</div>
				) : null}
			</div>
		);
	}

	const nameProblem =
		mode === "create" && name.trim() && !REPOSITORY_NAME.test(name.trim())
			? "Use up to 100 letters, numbers, hyphens, dots or underscores, starting with a letter, number or underscore."
			: null;

	return (
		<form className="remote-setup" onSubmit={(event) => void save(event)}>
			{github?.available ? (
				<fieldset className="remote-setup-modes">
					<legend className="visually-hidden">Which remote to connect</legend>
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
							<span className="mono" id={`${id}-owner`}>
								<span className="visually-hidden">
									Created in the GitHub account{" "}
								</span>
								{github.owner}/
							</span>
							<input
								id={`${id}-name`}
								className="mono"
								value={name}
								maxLength={100}
								spellCheck={false}
								autoComplete="off"
								aria-invalid={nameProblem ? true : undefined}
								aria-describedby={`${id}-owner${nameProblem ? ` ${id}-name-problem` : ""}`}
								onChange={(event) => setName(event.target.value)}
							/>
						</div>
						{nameProblem ? (
							<small id={`${id}-name-problem`} className="field-error">
								{nameProblem}
							</small>
						) : null}
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
						The address of an empty repository, such as
						https://github.com/you/course. Leave passwords out: your computer
						signs in to the host the way it usually does.
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
	const [github, setGithub] = useState(false);
	return (
		<Dialog
			title={github ? "Connect a GitHub repository" : "Connect a Git remote"}
			onClose={onClose}
		>
			<p className="meta remote-setup-intro">
				Sync and Publish push your Course, its history and releases to this
				remote, where a Classroom can read them.
			</p>
			<RemoteSetup
				onSaved={onSaved}
				onCancel={onClose}
				onHostChange={setGithub}
			/>
		</Dialog>
	);
}

/** Settings: see, change or remove your Course's Git remote. */
export function SharingSettings({ onChange }: { onChange?: () => void }) {
	const [settings, setSettings] = useState<RemoteSettings | null>(null);
	const [editing, setEditing] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [removing, setRemoving] = useState(false);

	useEffect(() => {
		request<RemoteSettings>("/api/workspace/remote", "GET")
			.then(setSettings)
			.catch((caught) =>
				setError(errorMessage(caught, "The remote could not be read.")),
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
			setError(errorMessage(caught, "The remote could not be removed."));
		} finally {
			setRemoving(false);
		}
	}

	return (
		<div className="settings-block">
			<h3>Git remote</h3>
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
							Sync and Publish push your Course, its history and releases to
							this remote.
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
								{removing ? "Removing…" : "Remove remote"}
							</button>
						</div>
					</>
				) : (
					<>
						<p className="meta">
							Your Course has no remote yet. Connect a GitHub repository or
							another Git remote so you can sync and a Classroom can serve your
							releases.
						</p>
						<button
							type="button"
							className="btn btn-small"
							onClick={() => setEditing(true)}
						>
							Connect a remote
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
