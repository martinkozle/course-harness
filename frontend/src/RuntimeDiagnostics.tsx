import { useEffect, useState } from "react";

import { responseError } from "./api";
import { Notice } from "./ui";

type RuntimeDiagnostics = {
	paths: Record<string, string>;
	provider: {
		configured: boolean;
		selected_model_id: string | null;
		remediation?: string;
		credential_storage: {
			mode: "os-keyring" | "private-json-file" | "unavailable" | "unconfigured";
			location: string;
		};
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
	connector_configuration: "Connector settings",
	connector_credentials: "Connector credentials without a keyring",
};

const credentialStorageLabels = {
	"os-keyring": "Operating-system keyring",
	"private-json-file": "Private application file",
	unavailable: "Unavailable — inspect the reported metadata path",
	unconfigured: "Not configured",
} as const;

/** Read-only local checks. Provider services are never contacted. */
export function RuntimeDiagnosticsPanel() {
	const [diagnostics, setDiagnostics] = useState<RuntimeDiagnostics | null>(
		null,
	);
	const [error, setError] = useState<string | null>(null);

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
		return () => controller.abort();
	}, []);

	return (
		<div className="settings-diagnostics">
			<p className="meta">
				These checks run on this computer only. Provider services are not
				contacted and credentials are never shown.
			</p>
			{error ? <Notice tone="error">{error}</Notice> : null}
			{diagnostics === null && error === null ? (
				<p role="status" aria-live="polite" aria-busy="true">
					Checking this computer…
				</p>
			) : null}
			{diagnostics ? (
				<>
					<section
						className="settings-block"
						aria-labelledby="runtime-provider-heading"
					>
						<h3 id="runtime-provider-heading">Provider</h3>
						<p>
							{diagnostics.provider.configured
								? `Configured: ${diagnostics.provider.provider.kind} · ${diagnostics.provider.provider.model}`
								: "No configured provider"}
						</p>
						<p className="meta">
							{diagnostics.provider.selected_model_id
								? `Selected model ID: ${diagnostics.provider.selected_model_id}`
								: "No model preset is selected."}
						</p>
						<p className="meta">
							Credential storage:{" "}
							{
								credentialStorageLabels[
									diagnostics.provider.credential_storage.mode
								]
							}
						</p>
						{diagnostics.provider.provider.diagnostics?.map((finding) => (
							<p className="meta" key={finding}>
								{finding}
							</p>
						))}
						{diagnostics.provider.remediation ? (
							<p className="settings-remediation">
								{diagnostics.provider.remediation}
							</p>
						) : null}
					</section>
					<section
						className="settings-block"
						aria-labelledby="runtime-parser-heading"
					>
						<h3 id="runtime-parser-heading">Resource parser</h3>
						{Object.entries(diagnostics.parser.processors).map(
							([processor, mediaTypes]) => (
								<p className="meta" key={processor}>
									{processor}: {mediaTypes.join(", ")}
								</p>
							),
						)}
						<p className="settings-remediation">
							{diagnostics.parser.remediation}
						</p>
					</section>
					<section
						className="settings-block"
						aria-labelledby="runtime-renderer-heading"
					>
						<h3 id="runtime-renderer-heading">Presentation renderer</h3>
						<p>
							{diagnostics.renderer.name}:{" "}
							{diagnostics.renderer.available ? "available" : "not found"}
						</p>
						<p className="meta">{diagnostics.renderer.detail}</p>
						<p className="settings-remediation">
							{diagnostics.renderer.remediation}
						</p>
					</section>
					<section
						className="settings-block"
						aria-labelledby="runtime-paths-heading"
					>
						<h3 id="runtime-paths-heading">Runtime paths</h3>
						<dl className="settings-paths">
							{Object.entries(diagnostics.paths).map(([key, path]) => (
								<div key={key}>
									<dt>{pathLabels[key] ?? key}</dt>
									<dd className="mono">{path}</dd>
								</div>
							))}
						</dl>
					</section>
				</>
			) : null}
		</div>
	);
}
