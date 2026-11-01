import { useEffect, useState } from "react";

import { ConfirmDialog, Dialog, Notice } from "../ui";
import type {
	CompactionPreview,
	Conversation,
	CourseAgent,
} from "../useCourseAgent";

export type ConversationDialog =
	| { kind: "rename"; conversation: Conversation }
	| { kind: "delete"; conversation: Conversation }
	| { kind: "compact" };

export function ConversationDialogs({
	dialog,
	agent,
	onClose,
}: {
	dialog: ConversationDialog;
	agent: CourseAgent;
	onClose: () => void;
}) {
	if (dialog.kind === "rename")
		return (
			<RenameDialog
				conversation={dialog.conversation}
				agent={agent}
				onClose={onClose}
			/>
		);
	if (dialog.kind === "delete")
		return (
			<ConfirmDialog
				title="Delete conversation?"
				confirmLabel="Delete conversation"
				busy={agent.conversationBusy}
				error={agent.error}
				onCancel={onClose}
				onConfirm={() => {
					void agent.deleteConversation(dialog.conversation.id).then((done) => {
						if (done) onClose();
					});
				}}
			>
				<p>
					“{dialog.conversation.title}” and its messages will be removed
					permanently. Course content the agent created stays in the course.
				</p>
			</ConfirmDialog>
		);
	return <CompactDialog agent={agent} onClose={onClose} />;
}

function RenameDialog({
	conversation,
	agent,
	onClose,
}: {
	conversation: Conversation;
	agent: CourseAgent;
	onClose: () => void;
}) {
	const [title, setTitle] = useState(conversation.title);
	return (
		<Dialog
			title="Rename conversation"
			onClose={onClose}
			footer={
				<>
					<button type="button" className="btn" onClick={onClose}>
						Cancel
					</button>
					<button
						type="submit"
						form="rename-conversation"
						className="btn btn-primary"
						disabled={!title.trim() || agent.conversationBusy}
					>
						Save name
					</button>
				</>
			}
		>
			<form
				id="rename-conversation"
				className="field"
				onSubmit={(event) => {
					event.preventDefault();
					void agent
						.renameConversation(conversation.id, title.trim())
						.then((done) => {
							if (done) onClose();
						});
				}}
			>
				<label htmlFor="conversation-title">Conversation name</label>
				<input
					id="conversation-title"
					value={title}
					maxLength={200}
					required
					autoFocus
					onChange={(event) => setTitle(event.target.value)}
				/>
			</form>
			{agent.error ? <Notice tone="error">{agent.error}</Notice> : null}
		</Dialog>
	);
}

function CompactDialog({
	agent,
	onClose,
}: {
	agent: CourseAgent;
	onClose: () => void;
}) {
	const [preview, setPreview] = useState<CompactionPreview | null>(null);
	const [summary, setSummary] = useState("");
	const [loading, setLoading] = useState(true);

	// biome-ignore lint/correctness/useExhaustiveDependencies: load the preview once per dialog
	useEffect(() => {
		void agent.previewCompaction().then((result) => {
			setPreview(result);
			setSummary(result?.summary ?? "");
			setLoading(false);
		});
	}, []);

	return (
		<Dialog
			title="Compact conversation"
			onClose={onClose}
			size="wide"
			footer={
				<>
					<button type="button" className="btn" onClick={onClose}>
						Cancel
					</button>
					<button
						type="button"
						className="btn btn-primary"
						disabled={!preview || !summary.trim() || agent.conversationBusy}
						onClick={() => {
							if (!preview) return;
							void agent
								.confirmCompaction(summary.trim(), preview.revision)
								.then((done) => {
									if (done) onClose();
								});
						}}
					>
						Compact conversation
					</button>
				</>
			}
		>
			<p className="meta">
				Future replies read this summary instead of the earlier messages, which
				frees up the model's context. You still see the full conversation here.
			</p>
			{loading ? <p role="status">Preparing a summary…</p> : null}
			{preview ? (
				<div className="field">
					<label htmlFor="compaction-summary">
						Summary of {preview.source_message_count} earlier messages
					</label>
					<textarea
						id="compaction-summary"
						rows={10}
						maxLength={4000}
						value={summary}
						aria-invalid={!summary.trim()}
						onChange={(event) => setSummary(event.target.value)}
					/>
				</div>
			) : null}
			{agent.error ? <Notice tone="error">{agent.error}</Notice> : null}
		</Dialog>
	);
}
