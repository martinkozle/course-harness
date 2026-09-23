import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import {
	type KeyboardEvent,
	type ReactNode,
	useEffect,
	useId,
	useRef,
	useState,
} from "react";

export function errorMessage(caught: unknown, fallback: string): string {
	return caught instanceof Error ? caught.message : fallback;
}

export function isAbort(caught: unknown): boolean {
	return caught instanceof DOMException && caught.name === "AbortError";
}

/* ---------- Notice ---------- */

export function Notice({
	tone = "info",
	children,
	actions,
	role,
}: {
	tone?: "info" | "error" | "warning" | "success";
	children: ReactNode;
	actions?: ReactNode;
	role?: "alert" | "status" | "note";
}) {
	const Icon =
		tone === "error"
			? XCircle
			: tone === "warning"
				? AlertTriangle
				: tone === "success"
					? CheckCircle2
					: Info;
	return (
		<div
			className={`notice is-${tone}`}
			role={role ?? (tone === "error" ? "alert" : undefined)}
		>
			<Icon aria-hidden="true" />
			<div className="notice-body">{children}</div>
			{actions ? <div className="notice-actions">{actions}</div> : null}
		</div>
	);
}

/* ---------- Menu ---------- */

export type MenuItem =
	| {
			label: string;
			icon?: ReactNode;
			detail?: string;
			onSelect: () => void;
			disabled?: boolean;
			title?: string;
			danger?: boolean;
	  }
	| "separator";

export function Menu({
	label,
	trigger,
	items,
	align = "end",
	direction = "down",
	triggerClassName = "icon-btn",
	disabled,
}: {
	label: string;
	trigger: ReactNode;
	items: MenuItem[];
	align?: "start" | "end";
	direction?: "down" | "up";
	triggerClassName?: string;
	disabled?: boolean;
}) {
	const [open, setOpen] = useState(false);
	const anchorRef = useRef<HTMLDivElement>(null);
	const triggerRef = useRef<HTMLButtonElement>(null);
	const menuRef = useRef<HTMLDivElement>(null);
	const menuId = useId();

	useEffect(() => {
		if (!open) return;
		const first = menuRef.current?.querySelector<HTMLButtonElement>(
			'[role="menuitem"]:not(:disabled)',
		);
		first?.focus();
		function closeOnOutside(event: PointerEvent) {
			if (!anchorRef.current?.contains(event.target as Node)) setOpen(false);
		}
		document.addEventListener("pointerdown", closeOnOutside);
		return () => document.removeEventListener("pointerdown", closeOnOutside);
	}, [open]);

	function close(returnFocus = true) {
		setOpen(false);
		if (returnFocus) triggerRef.current?.focus();
	}

	function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
		const entries = Array.from(
			menuRef.current?.querySelectorAll<HTMLButtonElement>(
				'[role="menuitem"]:not(:disabled)',
			) ?? [],
		);
		const index = entries.indexOf(document.activeElement as HTMLButtonElement);
		if (event.key === "Escape") {
			event.preventDefault();
			event.stopPropagation();
			close();
		} else if (event.key === "ArrowDown") {
			event.preventDefault();
			entries[(index + 1) % entries.length]?.focus();
		} else if (event.key === "ArrowUp") {
			event.preventDefault();
			entries[(index - 1 + entries.length) % entries.length]?.focus();
		} else if (event.key === "Home") {
			event.preventDefault();
			entries[0]?.focus();
		} else if (event.key === "End") {
			event.preventDefault();
			entries.at(-1)?.focus();
		} else if (event.key === "Tab") {
			close(false);
		}
	}

	return (
		<div className="menu-anchor" ref={anchorRef}>
			<button
				ref={triggerRef}
				type="button"
				className={triggerClassName}
				aria-label={label}
				title={label}
				aria-haspopup="menu"
				aria-expanded={open}
				aria-controls={open ? menuId : undefined}
				disabled={disabled}
				onClick={() => setOpen((current) => !current)}
			>
				{trigger}
			</button>
			{open ? (
				<div
					ref={menuRef}
					id={menuId}
					className={`menu${align === "start" ? " align-start" : ""}${direction === "up" ? " opens-up" : ""}`}
					role="menu"
					aria-label={label}
					tabIndex={-1}
					onKeyDown={onMenuKeyDown}
				>
					{items.map((item, index) =>
						item === "separator" ? (
							<hr
								// biome-ignore lint/suspicious/noArrayIndexKey: separators have no identity
								key={`separator-${index}`}
								className="menu-separator"
							/>
						) : (
							<button
								key={item.label}
								type="button"
								role="menuitem"
								className={item.danger ? "is-danger" : undefined}
								disabled={item.disabled}
								title={item.title}
								onClick={() => {
									close();
									item.onSelect();
								}}
							>
								{item.icon}
								{item.detail ? (
									<span className="menu-item-copy">
										<span>{item.label}</span>
										<small>{item.detail}</small>
									</span>
								) : (
									item.label
								)}
							</button>
						),
					)}
				</div>
			) : null}
		</div>
	);
}

/* ---------- Dialog ---------- */

export function Dialog({
	title,
	onClose,
	children,
	footer,
	size = "normal",
	tall = false,
	dismissible = true,
	headerExtra,
}: {
	title: string;
	onClose: () => void;
	children: ReactNode;
	footer?: ReactNode;
	size?: "normal" | "wide";
	tall?: boolean;
	dismissible?: boolean;
	headerExtra?: ReactNode;
}) {
	const ref = useRef<HTMLDialogElement>(null);
	const headingId = useId();
	const onCloseRef = useRef(onClose);
	onCloseRef.current = onClose;

	useEffect(() => {
		const dialog = ref.current;
		if (!dialog) return;
		const previouslyFocused = document.activeElement;
		if (!dialog.open) dialog.showModal();
		return () => {
			if (dialog.open) dialog.close();
			if (
				previouslyFocused instanceof HTMLElement &&
				previouslyFocused.isConnected
			) {
				previouslyFocused.focus({ preventScroll: true });
			}
		};
	}, []);

	return (
		<dialog
			ref={ref}
			className={`dialog${size === "wide" ? " is-wide" : ""}${tall ? " is-tall" : ""}`}
			aria-labelledby={headingId}
			onCancel={(event) => {
				event.preventDefault();
				if (dismissible) onCloseRef.current();
			}}
			onKeyDown={(event) => {
				if (event.key !== "Tab") return;
				// Keep keyboard focus inside the modal instead of escaping to browser chrome.
				const focusable = Array.from(
					event.currentTarget.querySelectorAll<HTMLElement>(
						'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
					),
				).filter((element) => element.offsetParent !== null);
				if (focusable.length === 0) return;
				const first = focusable[0];
				const last = focusable[focusable.length - 1];
				if (event.shiftKey && document.activeElement === first) {
					event.preventDefault();
					last.focus();
				} else if (!event.shiftKey && document.activeElement === last) {
					event.preventDefault();
					first.focus();
				}
			}}
		>
			<header className="dialog-header">
				<h2 id={headingId}>{title}</h2>
				{headerExtra}
				<button
					type="button"
					className="icon-btn"
					aria-label="Close"
					title="Close"
					onClick={onClose}
					disabled={!dismissible}
				>
					<X aria-hidden="true" />
				</button>
			</header>
			<div
				className="dialog-body"
				// biome-ignore lint/a11y/noNoninteractiveTabindex: long dialog content must scroll by keyboard
				tabIndex={0}
			>
				{children}
			</div>
			{footer ? <footer className="dialog-footer">{footer}</footer> : null}
		</dialog>
	);
}

export function ConfirmDialog({
	title,
	children,
	confirmLabel,
	busy = false,
	error,
	danger = true,
	onConfirm,
	onCancel,
}: {
	title: string;
	children: ReactNode;
	confirmLabel: string;
	busy?: boolean;
	error?: string | null;
	danger?: boolean;
	onConfirm: () => void;
	onCancel: () => void;
}) {
	return (
		<Dialog
			title={title}
			onClose={onCancel}
			dismissible={!busy}
			footer={
				<>
					<button
						type="button"
						className="btn"
						onClick={onCancel}
						disabled={busy}
					>
						Cancel
					</button>
					<button
						type="button"
						className={danger ? "btn btn-danger" : "btn btn-primary"}
						onClick={onConfirm}
						disabled={busy}
					>
						{confirmLabel}
					</button>
				</>
			}
		>
			<div className="confirm-copy">{children}</div>
			{error ? <Notice tone="error">{error}</Notice> : null}
		</Dialog>
	);
}

/* ---------- Tabs ---------- */

export function Tabs<T extends string>({
	label,
	tabs,
	value,
	onChange,
}: {
	label: string;
	tabs: { id: T; label: string; count?: number }[];
	value: T;
	onChange: (value: T) => void;
}) {
	function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
		if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
		event.preventDefault();
		const index = tabs.findIndex((tab) => tab.id === value);
		const next =
			tabs[
				(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
					tabs.length
			];
		onChange(next.id);
		requestAnimationFrame(() =>
			(
				event.currentTarget.querySelector(
					`[data-tab="${next.id}"]`,
				) as HTMLButtonElement | null
			)?.focus(),
		);
	}
	return (
		<div
			className="tabs"
			role="tablist"
			aria-label={label}
			onKeyDown={onKeyDown}
		>
			{tabs.map((tab) => (
				<button
					key={tab.id}
					type="button"
					role="tab"
					data-tab={tab.id}
					aria-selected={tab.id === value}
					tabIndex={tab.id === value ? 0 : -1}
					onClick={() => onChange(tab.id)}
				>
					{tab.label}
					{tab.count !== undefined ? (
						<span className="count">{tab.count}</span>
					) : null}
				</button>
			))}
		</div>
	);
}

/* ---------- Downloads ---------- */

export async function downloadResponse(response: Response, filename: string) {
	const objectUrl = URL.createObjectURL(await response.blob());
	const anchor = document.createElement("a");
	anchor.href = objectUrl;
	anchor.download = filename;
	document.body.append(anchor);
	anchor.click();
	setTimeout(() => {
		anchor.remove();
		URL.revokeObjectURL(objectUrl);
	}, 0);
}
