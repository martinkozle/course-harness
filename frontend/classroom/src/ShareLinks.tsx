import { ConfirmDialog, errorMessage } from "@course-harness/ui";
import { Check, Copy, Link2, RotateCw } from "lucide-react";
import { type ReactNode, useEffect, useId, useState } from "react";

import type { Role, ShareLink } from "./api";

export const ROLE_LABEL: Record<Role, string> = {
	learner: "Learner",
	instructor: "Instructor",
};

const ROLES: Role[] = ["learner", "instructor"];

function formatTime(value: string): string {
	const date = new Date(value);
	return Number.isNaN(date.getTime())
		? value
		: date.toLocaleString(undefined, {
				dateStyle: "medium",
				timeStyle: "short",
			});
}

/** When a link was made or last rotated. */
function linkAge(link: ShareLink) {
	return link.rotated_at
		? `Rotated ${formatTime(link.rotated_at)}`
		: `Created ${formatTime(link.created_at)}`;
}

/**
 * A course's Learner and Instructor links: each can be shown and copied at any time, and rotated
 * after a confirmation by whoever may rotate it. The server decides who that is; `canRotate`
 * only mirrors its answer.
 */
export function ShareLinks({
	links,
	canRotate,
	onRotate,
}: {
	links: ShareLink[];
	canRotate: Role[];
	/** Create or rotate the link for a role; a rejection is shown in the confirmation. */
	onRotate: (role: Role) => Promise<void>;
}) {
	return (
		<div className="share-links">
			{ROLES.map((role) => (
				<ShareLinkRow
					key={role}
					role={role}
					link={links.find((item) => item.role === role) ?? null}
					canRotate={canRotate.includes(role)}
					onRotate={() => onRotate(role)}
				/>
			))}
		</div>
	);
}

function ShareLinkRow({
	role,
	link,
	canRotate,
	onRotate,
}: {
	role: Role;
	link: ShareLink | null;
	canRotate: boolean;
	onRotate: () => Promise<void>;
}) {
	const [confirming, setConfirming] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [copied, setCopied] = useState(false);
	const inputId = useId();
	const label = `${ROLE_LABEL[role]} link`;

	useEffect(() => {
		if (!copied) return;
		const timer = window.setTimeout(() => setCopied(false), 2_000);
		return () => window.clearTimeout(timer);
	}, [copied]);

	async function rotate() {
		setBusy(true);
		setError(null);
		try {
			await onRotate();
			setConfirming(false);
		} catch (caught) {
			setError(errorMessage(caught, "The link could not be changed."));
		} finally {
			setBusy(false);
		}
	}

	async function copy(url: string) {
		try {
			await navigator.clipboard.writeText(url);
			setCopied(true);
		} catch {
			// Without clipboard access, select the link so it can be copied by hand.
			const input = document.getElementById(inputId);
			if (input instanceof HTMLInputElement) input.select();
		}
	}

	const rotateButton = canRotate ? (
		<button
			type="button"
			className="btn btn-small"
			disabled={busy}
			onClick={() => (link ? setConfirming(true) : void rotate())}
		>
			{link ? <RotateCw aria-hidden="true" /> : <Link2 aria-hidden="true" />}
			{link ? "Rotate" : "Create"}
			<span className="visually-hidden"> {label}</span>
		</button>
	) : null;

	let body: ReactNode;
	if (link?.url) {
		body = (
			<div className="share-link-row">
				<input
					id={inputId}
					className="input"
					readOnly
					value={link.url}
					spellCheck={false}
					onFocus={(event) => event.target.select()}
				/>
				<button
					type="button"
					className="btn btn-small"
					onClick={() => link.url && void copy(link.url)}
				>
					{copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
					{copied ? "Copied" : "Copy"}
					<span className="visually-hidden"> {label}</span>
				</button>
				{rotateButton}
			</div>
		);
	} else {
		const missing = !link
			? canRotate
				? "No link yet."
				: "No link yet. The Administrator creates it."
			: canRotate
				? "Rotate to show this link again."
				: "This link was made before links could be shown again. The Administrator can rotate it to show it.";
		body = (
			<div className="share-link-row">
				<p className="share-link-missing">{missing}</p>
				{rotateButton}
			</div>
		);
	}

	return (
		<div className="share-link">
			<div className="share-link-head">
				{link?.url ? (
					<label htmlFor={inputId}>{label}</label>
				) : (
					<span className="field-label">{label}</span>
				)}
				{link ? <span className="meta">{linkAge(link)}</span> : null}
			</div>
			{body}
			<span className="visually-hidden" role="status">
				{copied ? `${label} copied` : ""}
			</span>
			{confirming && link ? (
				<ConfirmDialog
					title={`Rotate the ${label}?`}
					confirmLabel="Rotate link"
					busy={busy}
					error={error}
					onConfirm={() => void rotate()}
					onCancel={() => {
						setConfirming(false);
						setError(null);
					}}
				>
					<p>
						The current {label} stops working, and everyone who opened it must
						open the new one.
					</p>
				</ConfirmDialog>
			) : null}
			{error && !confirming ? (
				<p className="share-link-error" role="alert">
					{error}
				</p>
			) : null}
		</div>
	);
}
