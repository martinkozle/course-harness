import { errorMessage, Notice } from "@course-harness/ui";
import { CloudUpload } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import { responseError } from "../api";

type RemoteSettings = {
	url: string | null;
	push_command: string;
};

type PushResult = {
	ok: boolean;
	remote: string;
	message: string;
	releases_without_files: string[];
	releases_with_invalid_files: string[];
};

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

/** The Workspace's Git remote and an explicit Push of the Course and its releases. */
export function RemotePushPanel() {
	const [settings, setSettings] = useState<RemoteSettings | null>(null);
	const [url, setUrl] = useState("");
	const [saving, setSaving] = useState(false);
	const [pushing, setPushing] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [result, setResult] = useState<PushResult | null>(null);

	useEffect(() => {
		request<RemoteSettings>("/api/workspace/remote", "GET")
			.then((loaded) => {
				setSettings(loaded);
				setUrl(loaded.url ?? "");
			})
			.catch((caught) =>
				setError(errorMessage(caught, "The Git remote could not be loaded.")),
			);
	}, []);

	const changed = settings !== null && url.trim() !== (settings.url ?? "");

	async function save(event: FormEvent) {
		event.preventDefault();
		setSaving(true);
		setError(null);
		setResult(null);
		try {
			const saved = await request<RemoteSettings>(
				"/api/workspace/remote",
				"PUT",
				{ url: url.trim() || null },
			);
			setSettings(saved);
			setUrl(saved.url ?? "");
		} catch (caught) {
			setError(errorMessage(caught, "The Git remote could not be saved."));
		} finally {
			setSaving(false);
		}
	}

	async function push() {
		setPushing(true);
		setError(null);
		setResult(null);
		try {
			setResult(
				await request<PushResult>("/api/workspace/remote/push", "POST"),
			);
		} catch (caught) {
			setError(errorMessage(caught, "The push could not start."));
		} finally {
			setPushing(false);
		}
	}

	return (
		<section
			className="canvas-section release-step"
			aria-labelledby="release-remote-heading"
		>
			<div className="canvas-section-head">
				<CloudUpload aria-hidden="true" className="release-remote-icon" />
				<h2 id="release-remote-heading">Share on a Git remote</h2>
			</div>
			<p className="meta">
				Push sends your Course history, every release, and the release
				PowerPoint files to a Git repository you choose, where a Classroom can
				read them. Git signs in as you, with your SSH key or credential helper.
				Nothing is sent until you choose Push.
			</p>
			<form className="release-remote-form" onSubmit={(event) => void save(event)}>
				<div className="field">
					<label htmlFor="release-remote-url">Remote URL</label>
					<input
						id="release-remote-url"
						className="mono"
						value={url}
						maxLength={2048}
						placeholder="git@github.com:you/course.git"
						spellCheck={false}
						autoComplete="off"
						aria-describedby="release-remote-help"
						disabled={settings === null}
						onChange={(event) => setUrl(event.target.value)}
					/>
					<small id="release-remote-help">
						An HTTPS or SSH address. Leave passwords and tokens out; Git asks
						your credential helper.
					</small>
				</div>
				<button
					type="submit"
					className="btn"
					disabled={!changed || saving || pushing}
				>
					{saving ? "Saving…" : url.trim() ? "Save remote" : "Remove remote"}
				</button>
			</form>
			{error ? (
				<Notice tone="error" role="alert">
					{error}
				</Notice>
			) : null}
			{result ? (
				<Notice
					tone={result.ok ? "success" : "error"}
					role={result.ok ? "status" : "alert"}
				>
					{result.ok ? (
						<p>{result.message}</p>
					) : (
						<>
							<p>Git could not push to {result.remote}.</p>
							<pre className="release-remote-output">{result.message}</pre>
						</>
					)}
					{result.releases_without_files.length > 0 ? (
						<p>
							The PowerPoint files of{" "}
							{result.releases_without_files.join(", ")} are not on this
							computer, so the remote has those releases without them.
						</p>
					) : null}
					{result.releases_with_invalid_files.length > 0 ? (
						<p>
							The stored PowerPoint files of{" "}
							{result.releases_with_invalid_files.join(", ")} don't match
							their releases and aren't on this computer, so they were not
							pushed.
						</p>
					) : null}
				</Notice>
			) : null}
			<div className="release-publish">
				<button
					type="button"
					className="btn btn-primary"
					disabled={!settings?.url || changed || pushing || saving}
					onClick={() => void push()}
				>
					{pushing ? "Pushing…" : "Push"}
				</button>
				{settings ? (
					<p className="meta">
						Close to <code>{settings.push_command}</code> in a terminal, except
						that Push first records release files for older releases and
						never runs Git hooks.
					</p>
				) : null}
			</div>
		</section>
	);
}
