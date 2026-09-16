import {
	type KeyboardEvent as ReactKeyboardEvent,
	useEffect,
	useRef,
	useState,
} from "react";

import { responseError } from "./api";

type RuntimeDiagnostics = {
	paths: Record<string, string>;
	provider: {
		configured: boolean;
		selected_model_id: string | null;
		remediation?: string;
		provider: {
			configured: boolean;
			kind?: string;
			model?: string;
			diagnostics?: string[];
		};
	};
	parser: { processors: Record<string, string[]>; remediation: string };
	renderer: {
		available: boolean;
		name: string;
		detail: string;
		remediation: string;
	};
};

const pathLabels: Record<string, string> = {
	recent_workspaces: "Recent Workspaces",
	chat_history: "Chat history",
	library_data: "Library data",
	library_cache: "Library cache",
	templates_data: "Template data",
	templates_cache: "Template cache",
	releases: "Published Releases",
	provider_configuration: "Provider Account data",
	provider_credentials: "Provider credentials",
};

export function RuntimeDiagnosticsDialog({ onClose }: { onClose: () => void }) {
	const [diagnostics, setDiagnostics] = useState<RuntimeDiagnostics | null>(
		null,
	);
	const [error, setError] = useState<string | null>(null);
	const closeButton = useRef<HTMLButtonElement>(null);
	const dialog = useRef<HTMLElement>(null);

	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch("/api/runtime-diagnostics", {
					signal: controller.signal,
				});
				if (!response.ok) throw new Error(await responseError(response));
				setDiagnostics((await response.json()) as RuntimeDiagnostics);
			} catch (caught) {
				if (caught instanceof DOMException && caught.name === "AbortError")
					return;
				setError(
					caught instanceof Error
						? caught.message
						: "Runtime diagnostics could not be loaded.",
				);
			}
		})();
		const previouslyFocused = document.activeElement;
		closeButton.current?.focus({ preventScroll: true });
		return () => {
			controller.abort();
			if (previouslyFocused instanceof HTMLElement) {
				previouslyFocused.focus({ preventScroll: true });
			}
		};
	}, []);

	useEffect(() => {
		function closeOnEscape(event: globalThis.KeyboardEvent) {
			if (event.key !== "Escape") return;
			event.preventDefault();
			onClose();
		}
		window.addEventListener("keydown", closeOnEscape);
		return () => window.removeEventListener("keydown", closeOnEscape);
	}, [onClose]);

	function keepFocusInside(event: ReactKeyboardEvent<HTMLElement>) {
		if (event.key !== "Tab") return;
		const focusable = dialog.current?.querySelectorAll<HTMLElement>(
			'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
		);
		if (!focusable || focusable.length === 0) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (event.shiftKey && document.activeElement === first) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && document.activeElement === last) {
			event.preventDefault();
			first.focus();
		}
	}

	return (
		<div className="runtime-diagnostics-backdrop">
			<section
				ref={dialog}
				className="runtime-diagnostics"
				role="dialog"
				aria-modal="true"
				aria-labelledby="runtime-diagnostics-title"
				onKeyDown={keepFocusInside}
			>
				<header>
					<div>
						<p className="eyebrow">Local checks</p>
						<h2 id="runtime-diagnostics-title">Runtime diagnostics</h2>
					</div>
					<button
						ref={closeButton}
						className="quiet-action compact-action"
						type="button"
						onClick={onClose}
					>
						Close
					</button>
				</header>
				<p className="runtime-diagnostics-intro">
					Read-only local checks. Provider services are not contacted and
					credential contents are never displayed.
				</p>
				{error ? (
					<p className="notice error-notice" role="alert">
						{error}
					</p>
				) : null}
				{diagnostics === null && error === null ? (
					<p role="status" aria-live="polite" aria-busy="true">
						Checking this computer…
					</p>
				) : null}
				{diagnostics ? (
					<div className="runtime-diagnostics-content">
						<section aria-labelledby="runtime-paths-heading">
							<h3 id="runtime-paths-heading">Runtime paths</h3>
							<dl className="runtime-path-list">
								{Object.entries(diagnostics.paths).map(([key, path]) => (
									<div key={key}>
										<dt>{pathLabels[key] ?? key}</dt>
										<dd>{path}</dd>
									</div>
								))}
							</dl>
						</section>
						<section aria-labelledby="runtime-provider-heading">
							<h3 id="runtime-provider-heading">Provider</h3>
							<p className="runtime-status">
								{diagnostics.provider.configured
									? `Configured: ${diagnostics.provider.provider.kind} · ${diagnostics.provider.provider.model}`
									: "No configured provider"}
							</p>
							<p className="runtime-detail">
								{diagnostics.provider.selected_model_id
									? `Selected model ID: ${diagnostics.provider.selected_model_id}`
									: "No model preset is selected."}
							</p>
							{diagnostics.provider.provider.diagnostics?.map((finding) => (
								<p className="runtime-detail" key={finding}>
									{finding}
								</p>
							))}
							{diagnostics.provider.remediation ? (
								<p className="runtime-remediation">
									{diagnostics.provider.remediation}
								</p>
							) : null}
						</section>
						<section aria-labelledby="runtime-parser-heading">
							<h3 id="runtime-parser-heading">Resource parser</h3>
							{Object.entries(diagnostics.parser.processors).map(
								([processor, mediaTypes]) => (
									<p className="runtime-detail" key={processor}>
										{processor}: {mediaTypes.join(", ")}
									</p>
								),
							)}
							<p className="runtime-remediation">
								{diagnostics.parser.remediation}
							</p>
						</section>
						<section aria-labelledby="runtime-renderer-heading">
							<h3 id="runtime-renderer-heading">Presentation renderer</h3>
							<p className="runtime-status">
								{diagnostics.renderer.name}:{" "}
								{diagnostics.renderer.available ? "available" : "not found"}
							</p>
							<p className="runtime-detail">{diagnostics.renderer.detail}</p>
							<p className="runtime-remediation">
								{diagnostics.renderer.remediation}
							</p>
						</section>
					</div>
				) : null}
			</section>
		</div>
	);
}
