import {
	ArrowDown,
	ArrowUp,
	Check,
	Loader2,
	RefreshCw,
	Send,
	Sparkles,
	X,
} from "lucide-react";
import { useEffect, useId, useRef } from "react";

import type { SharingStatus } from "../models";
import type { Sharing } from "../useSharing";

function plural(count: number, one: string, many = `${one}s`) {
	return `${count} ${count === 1 ? one : many}`;
}

/** "3 changes since last release", "Released · up to date" or "Not released yet". */
export function releaseStatusText(status: SharingStatus): string {
	if (status.release_count === 0) return "Not released yet";
	if (status.changes_since_release === 0) return "Released · up to date";
	return `${plural(status.changes_since_release, "change")} since last release`;
}

function minutesAgo(iso: string | null): string {
	if (!iso) return "";
	const minutes = Math.round((Date.now() - Date.parse(iso)) / 60_000);
	if (!Number.isFinite(minutes) || minutes < 1) return "checked just now";
	if (minutes < 60) return `checked ${plural(minutes, "minute")} ago`;
	return `checked ${plural(Math.round(minutes / 60), "hour")} ago`;
}

type SyncView = {
	label: string;
	title: string;
	inSync: boolean;
};

export function syncView(
	status: SharingStatus | null,
	syncing: boolean,
): SyncView {
	if (!status?.remote_url) {
		return {
			label: "Sync: set up sharing first",
			title: "Share your Course to GitHub to sync it",
			inSync: false,
		};
	}
	const where = status.remote_label ?? status.remote_url;
	const problem = status.check_error
		? ` · Couldn't check ${where}: ${status.check_error}`
		: "";
	if (syncing)
		return { label: "Syncing…", title: `Syncing with ${where}`, inSync: false };
	if (status.ahead === null || status.behind === null) {
		return {
			label: `Sync with ${where}`,
			title: `Sync with ${where}${problem || " · not checked yet"}`,
			inSync: false,
		};
	}
	if (status.ahead === 0 && status.behind === 0) {
		return {
			label: `In sync with ${where}`,
			title: `Up to date with ${where} · ${minutesAgo(status.checked_at)}${problem}`,
			inSync: true,
		};
	}
	const counts = `${plural(status.ahead, "change")} to send, ${status.behind} to receive`;
	return {
		label: `Sync: ${counts}`,
		title: `Sync with ${where}: ${counts} · ${minutesAgo(status.checked_at)}${problem}`,
		inSync: false,
	};
}

/** Publish with its release status, and a Sync button with what it would send and receive. */
export function SharingControls({
	sharing,
	onPublish,
	onSetUpSharing,
	onAskAgent,
}: {
	sharing: Sharing;
	onPublish: () => void;
	onSetUpSharing: () => void;
	onAskAgent: (finding: string) => void;
}) {
	const { status, syncing, failure } = sharing;
	const view = syncView(status, syncing);
	const popoverId = useId();
	const popoverRef = useRef<HTMLDivElement>(null);
	const counts = status?.ahead !== null && status?.ahead !== undefined;

	useEffect(() => {
		if (failure) popoverRef.current?.focus();
	}, [failure]);

	function sync() {
		if (!status?.remote_url) {
			onSetUpSharing();
			return;
		}
		if (view.inSync || syncing) return;
		void sharing.sync();
	}

	return (
		<div className="sharing-controls">
			<div className="sharing-publish">
				<button type="button" className="btn btn-small" onClick={onPublish}>
					<Send aria-hidden="true" />
					<span className="sharing-publish-label">Publish</span>
				</button>
				{status ? (
					<span className="sharing-status" aria-live="polite">
						{releaseStatusText(status)}
					</span>
				) : null}
			</div>
			<div className="sharing-sync">
				<button
					type="button"
					className={`btn btn-small sync-btn${failure ? " has-problem" : ""}${
						status?.check_error ? " has-check-error" : ""
					}`}
					aria-label={view.label}
					title={view.title}
					aria-disabled={view.inSync || syncing ? true : undefined}
					aria-busy={syncing || undefined}
					aria-expanded={failure ? true : undefined}
					aria-controls={failure ? popoverId : undefined}
					onClick={sync}
				>
					{syncing ? (
						<Loader2 className="spin" aria-hidden="true" />
					) : view.inSync ? (
						<Check className="sync-check" aria-hidden="true" />
					) : (
						<RefreshCw aria-hidden="true" />
					)}
					<span aria-hidden="true" className="sync-label">
						{syncing ? "Syncing…" : "Sync"}
					</span>
					{counts && !view.inSync && !syncing ? (
						<span className="sync-counts" aria-hidden="true">
							<span>
								<ArrowUp />
								{status?.ahead}
							</span>
							<span>
								<ArrowDown />
								{status?.behind}
							</span>
						</span>
					) : null}
				</button>
				{failure ? (
					<div
						ref={popoverRef}
						id={popoverId}
						className="sync-popover"
						role="alert"
						tabIndex={-1}
						onKeyDown={(event) => {
							if (event.key === "Escape") sharing.dismissFailure();
						}}
					>
						<div className="sync-popover-head">
							<strong>Sync didn't finish</strong>
							<button
								type="button"
								className="icon-btn"
								aria-label="Close"
								onClick={sharing.dismissFailure}
							>
								<X aria-hidden="true" />
							</button>
						</div>
						<p>{failure.message}</p>
						{failure.findings.length > 0 ? (
							<ul className="sync-findings">
								{failure.findings.map((finding) => (
									<li key={finding}>
										<span>{finding}</span>
										<button
											type="button"
											className="btn btn-quiet btn-small"
											onClick={() => {
												sharing.dismissFailure();
												onAskAgent(finding);
											}}
										>
											<Sparkles aria-hidden="true" />
											Ask the agent to fix
										</button>
									</li>
								))}
							</ul>
						) : null}
						{failure.detail ? (
							<details className="sync-detail">
								<summary>Details</summary>
								<pre>{failure.detail}</pre>
							</details>
						) : null}
						<div className="sync-popover-actions">
							<button
								type="button"
								className="btn btn-small btn-primary"
								onClick={() => void sharing.sync()}
							>
								Retry
							</button>
						</div>
					</div>
				) : null}
			</div>
		</div>
	);
}
