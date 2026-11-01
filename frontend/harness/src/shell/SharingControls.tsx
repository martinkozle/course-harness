import { ConfirmDialog } from "@course-harness/ui";
import {
	AlertTriangle,
	ArrowDown,
	ArrowUp,
	Check,
	Loader2,
	RefreshCw,
	Send,
	Sparkles,
	X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import type { SharingStatus } from "../models";
import type { Sharing, SyncAction } from "../useSharing";

function plural(count: number, one: string, many = `${one}s`) {
	return `${count} ${count === 1 ? one : many}`;
}

/** Shown in the header and in Publish while the shared copy has Revisions this computer lacks. */
export const SYNC_FIRST = "Sync first to get the latest shared copy";

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
			title: "Share your Course to a Git host to sync it",
			inSync: false,
		};
	}
	const where = status.remote_label ?? status.remote_url;
	// A failed background check is part of the name, so screen readers hear it too.
	const problem = status.check_error ? `; couldn't check ${where}` : "";
	const reason = status.check_error ? `: ${status.check_error}` : "";
	if (syncing)
		return { label: "Syncing…", title: `Syncing with ${where}`, inSync: false };
	if (status.ahead === null || status.behind === null) {
		return {
			label: `Sync with ${where}${problem}`,
			title: `Sync with ${where}${problem ? `${problem}${reason}` : " · not checked yet"}`,
			inSync: false,
		};
	}
	if (status.ahead === 0 && status.behind === 0) {
		return {
			label: `In sync with ${where}${problem}`,
			title: `Up to date with ${where} · ${minutesAgo(status.checked_at)}${problem}${reason}`,
			inSync: true,
		};
	}
	const counts = `${plural(status.ahead, "change")} to send, ${status.behind} to receive`;
	return {
		label: `Sync: ${counts}${problem}`,
		title: `Sync with ${where}: ${counts} · ${minutesAgo(status.checked_at)}${problem}${reason}`,
		inSync: false,
	};
}

type Choice = Exclude<SyncAction, "sync">;

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
	const [choice, setChoice] = useState<Choice | null>(null);
	const counts = status?.ahead !== null && status?.ahead !== undefined;
	const behind = (status?.behind ?? 0) > 0;
	const where = status?.remote_label ?? "the shared copy";

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

	async function confirm(action: Choice) {
		setChoice(null);
		await sharing.sync(action);
	}

	return (
		<div className="sharing-controls">
			<div className="sharing-publish">
				<button type="button" className="btn btn-small" onClick={onPublish}>
					<Send aria-hidden="true" />
					<span className="sharing-publish-label">Publish</span>
				</button>
				{status ? (
					<span
						className={`sharing-status${behind ? " is-warning" : ""}`}
						aria-live="polite"
					>
						{behind ? (
							<>
								<AlertTriangle aria-hidden="true" />
								<span className="sharing-status-text">{SYNC_FIRST}</span>
							</>
						) : (
							releaseStatusText(status)
						)}
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
						role="dialog"
						aria-labelledby={`${popoverId}-title`}
						tabIndex={-1}
						onKeyDown={(event) => {
							if (event.key === "Escape") sharing.dismissFailure();
						}}
					>
						<div className="sync-popover-head">
							<strong id={`${popoverId}-title`}>
								{failure.ok
									? "Synced, with something to check"
									: failure.diverged
										? "Choose which version to keep"
										: "Sync didn't finish"}
							</strong>
							<button
								type="button"
								className="icon-btn"
								aria-label="Close"
								onClick={sharing.dismissFailure}
							>
								<X aria-hidden="true" />
							</button>
						</div>
						<p role={failure.ok ? "status" : "alert"}>{failure.message}</p>
						{failure.notices.map((notice) => (
							<p key={notice}>{notice}</p>
						))}
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
						{failure.diverged ? (
							<ul className="sync-choices">
								<li>
									<button
										type="button"
										className="btn btn-small"
										onClick={() => setChoice("use-shared-copy")}
									>
										Use the shared copy
									</button>
									<span className="meta">
										Your Course here becomes the version on {where}. This
										computer's version is kept as a backup.
									</span>
								</li>
								<li>
									<button
										type="button"
										className="btn btn-small"
										onClick={() => setChoice("replace-shared-copy")}
									>
										Replace the shared copy with this computer's
									</button>
									<span className="meta">
										{where} gets your version. Changes made elsewhere since your
										last Sync are no longer shared; releases stay.
									</span>
								</li>
							</ul>
						) : null}
						{failure.detail ? (
							<details className="sync-detail">
								<summary>Details</summary>
								<pre>{failure.detail}</pre>
							</details>
						) : null}
						{!failure.ok && !failure.diverged ? (
							<div className="sync-popover-actions">
								<button
									type="button"
									className="btn btn-small btn-primary"
									onClick={() => void sharing.sync()}
								>
									Retry
								</button>
							</div>
						) : null}
					</div>
				) : null}
			</div>
			{choice === "use-shared-copy" ? (
				<ConfirmDialog
					title="Use the shared copy?"
					confirmLabel="Use the shared copy"
					danger={false}
					onConfirm={() => void confirm("use-shared-copy")}
					onCancel={() => setChoice(null)}
				>
					<p>
						Your Course on this computer will become the version on {where}.
					</p>
					<p>
						Nothing is lost: this computer's version, with your latest changes,
						is kept as a backup in the Course folder.
					</p>
				</ConfirmDialog>
			) : null}
			{choice === "replace-shared-copy" ? (
				<ConfirmDialog
					title="Replace the shared copy?"
					confirmLabel="Replace the shared copy"
					onConfirm={() => void confirm("replace-shared-copy")}
					onCancel={() => setChoice(null)}
				>
					<p>
						{where} will get this computer's version of the Course. Changes made
						elsewhere since your last Sync will no longer be part of the shared
						copy.
					</p>
					<p>
						Releases are never removed or changed. If someone shares again
						before you confirm, nothing is replaced.
					</p>
				</ConfirmDialog>
			) : null}
		</div>
	);
}
