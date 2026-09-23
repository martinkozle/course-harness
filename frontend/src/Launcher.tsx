import { ArrowRight, FolderOpen, Plus, Stethoscope } from "lucide-react";
import { useState } from "react";

import { RuntimeDiagnosticsPanel } from "./RuntimeDiagnostics";
import { Dialog, Notice } from "./ui";

export type RecentWorkspace = {
	id: string;
	name: string;
	path: string;
};

export function Launcher({
	recent,
	busy,
	error,
	onNew,
	onOpen,
	onOpenRecent,
}: {
	recent: RecentWorkspace[];
	busy: boolean;
	error: string | null;
	onNew: () => Promise<void>;
	onOpen: () => Promise<void>;
	onOpenRecent: (identity: string) => Promise<void>;
}) {
	const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
	return (
		<main className="launcher" aria-busy={busy}>
			<div className="launcher-inner">
				<p className="launcher-wordmark">Course Harness</p>
				<h1 className="display-title launcher-title">Your courses</h1>

				<div className="launcher-actions">
					<button
						type="button"
						className="launcher-action is-primary"
						onClick={() => void onNew()}
						disabled={busy}
					>
						<Plus aria-hidden="true" />
						<span>
							<strong>New course</strong>
							<small>Choose an empty folder for it</small>
						</span>
					</button>
					<button
						type="button"
						className="launcher-action"
						onClick={() => void onOpen()}
						disabled={busy}
					>
						<FolderOpen aria-hidden="true" />
						<span>
							<strong>Open existing</strong>
							<small>Pick a Course folder on this computer</small>
						</span>
					</button>
				</div>

				{error ? <Notice tone="error">{error}</Notice> : null}

				<section className="launcher-recent" aria-labelledby="recent-heading">
					<h2 id="recent-heading" className="section-label">
						Recent courses
					</h2>
					{recent.length === 0 ? (
						<p className="meta">Courses you create or open appear here.</p>
					) : (
						<ul>
							{recent.map((item) => (
								<li key={item.id}>
									<button
										type="button"
										onClick={() => void onOpenRecent(item.id)}
										disabled={busy}
									>
										<span className="recent-copy">
											<strong>{item.name}</strong>
											<small>{item.path}</small>
										</span>
										<ArrowRight aria-hidden="true" />
									</button>
								</li>
							))}
						</ul>
					)}
				</section>

				<button
					type="button"
					className="btn btn-quiet launcher-diagnostics"
					onClick={() => setDiagnosticsOpen(true)}
				>
					<Stethoscope aria-hidden="true" />
					Runtime diagnostics
				</button>
			</div>
			{diagnosticsOpen ? (
				<Dialog
					title="Runtime diagnostics"
					size="wide"
					onClose={() => setDiagnosticsOpen(false)}
				>
					<RuntimeDiagnosticsPanel />
				</Dialog>
			) : null}
		</main>
	);
}
