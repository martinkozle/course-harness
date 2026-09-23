import { Dialog, Notice } from "../ui";
import type { Library } from "../useLibrary";

export function ModelDownloadDialog({ library }: { library: Library }) {
	const prompt = library.modelPrompt;
	if (!prompt) return null;
	const status = library.modelStatus;
	const busy = library.downloading;
	const total = status?.total_steps ?? 3;
	const completed = status?.completed_steps ?? 0;
	const consequence =
		prompt.kind === "upload"
			? prompt.files.length === 1
				? "If you cancel, the PDF is not added. It stays in the list so you can add it later."
				: `If you cancel, these ${prompt.files.length} PDFs are not added. They stay in the list so you can add them later.`
			: prompt.kind === "process"
				? "If you cancel, the file stays as it is."
				: "After the download, reprocess any existing PDF to make it searchable.";
	const confirmLabel = busy
		? "Downloading models…"
		: prompt.kind === "upload"
			? "Download and add"
			: prompt.kind === "process"
				? "Download and process"
				: "Download models";

	return (
		<Dialog
			title="Download document processing models?"
			onClose={library.cancelModelPrompt}
			dismissible={!busy}
			footer={
				<>
					<button
						type="button"
						className="btn"
						onClick={library.cancelModelPrompt}
						disabled={busy}
					>
						Cancel
					</button>
					<button
						type="button"
						className="btn btn-primary"
						onClick={() => void library.confirmModelDownload()}
						disabled={busy}
					>
						{confirmLabel}
					</button>
				</>
			}
		>
			<p>
				Reading PDFs uses local layout, table, and text recognition models. They
				take several hundred megabytes and are downloaded once to this computer,
				outside your Course folder. Your documents stay on this device.
			</p>
			<p className="meta">{consequence}</p>
			{busy ? (
				<div className="model-download-progress" role="status">
					<p>{status?.stage ?? "Starting the download…"}</p>
					<progress
						aria-label="Model download stages completed"
						max={total}
						value={completed}
					/>
					<p className="meta">
						{completed} of {total} stages complete
					</p>
				</div>
			) : null}
			{library.modelError ? (
				<Notice tone="error">{library.modelError}</Notice>
			) : null}
		</Dialog>
	);
}
