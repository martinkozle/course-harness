import { type FormEvent, useEffect, useRef, useState } from "react";

import { type AgentInterrupt, streamAgentRun } from "./agentStream";
import { responseError } from "./api";
import type { ModelCatalog } from "./models";

export type CoursePlan = {
	schema_version: 1;
	id: string;
	title: string;
	audience: string;
	goals: string[];
	outcomes: string[];
	template_profile_id?: string | null;
	template_profile_version?: number | null;
	lectures: {
		id: string;
		title: string;
		group: string | null;
		presentation_id?: string;
	}[];
};

export type ChatMessage = {
	id: string;
	role: "user" | "assistant";
	content: string;
};

type CoursePlanProposal = {
	title: string;
	audience: string;
	goals: string[];
	outcomes: string[];
	lectures: { id?: string; title: string; group?: string | null }[];
};

type PresentationProposal = {
	lecture_id: string;
	replace_all_slides?: boolean;
	slides: {
		layout: string;
		title?: string | null;
		purpose?: string | null;
		archived?: boolean;
	}[];
};

type ReconciliationProposal = {
	summary: string;
	entries: { path: string; content: string | null }[];
};

type Activity = { id: string; title: string; detail: string };

type AgentMode = "guided" | "autonomous";

type Conversation = {
	id: string;
	title: string;
	created_at: string;
	updated_at: string;
	archived: boolean;
	has_pending_approval: boolean;
};

type ConversationList = { active_id: string; conversations: Conversation[] };
type Transcript = { messages: ChatMessage[]; approval: AgentInterrupt | null };
type CompactionPreview = {
	summary: string;
	source_message_count: number;
	revision: string | number;
};

type AgentPanelProps = {
	embedded?: boolean;
	initialMessages: ChatMessage[];
	initialApproval: AgentInterrupt | null;
	catalog: ModelCatalog;
	onCatalogChange: (catalog: ModelCatalog) => void;
	onOpenModels: () => void;
	onCourseChange: (course: CoursePlan) => Promise<void>;
	onRunningChange: (running: boolean) => void;
	onPresentationsChange?: () => Promise<void>;
	chatContext?: string | null;
	reconciliationDriftId?: string | null;
	onChatContextCleared?: () => void;
	onReconciliationDriftCleared?: () => void;
	onConversationCleared: () => void;
	onTranscriptChange: (
		messages: ChatMessage[],
		approval: AgentInterrupt | null,
	) => void;
};

function updateAssistantMessage(
	messages: ChatMessage[],
	identity: string,
	delta: string,
): ChatMessage[] {
	const index = messages.findIndex((message) => message.id === identity);
	if (index === -1)
		return [...messages, { id: identity, role: "assistant", content: delta }];
	return messages.map((message, messageIndex) =>
		messageIndex === index
			? { ...message, content: message.content + delta }
			: message,
	);
}

function messageParts(content: string): {
	context: string | null;
	body: string;
} {
	const match = /^\[Context: (.+)\]\n\n([\s\S]*)$/.exec(content);
	return match
		? { context: match[1], body: match[2] }
		: { context: null, body: content };
}

function approvalProposal(interrupt: AgentInterrupt): {
	tool:
		| "course_plan"
		| "presentation"
		| "presentation_delete"
		| "reconciliation";
	preview: Record<string, unknown> | null;
} | null {
	const message = interrupt.message;
	if (!message) return null;
	if (message.includes("apply_reconciliation_patch(")) {
		const reconciliationMatch =
			/apply_reconciliation_patch\((\{.*\})\)\?$/s.exec(message);
		if (reconciliationMatch) {
			try {
				return {
					tool: "reconciliation",
					preview: JSON.parse(reconciliationMatch[1]) as Record<
						string,
						unknown
					>,
				};
			} catch {
				// Fall through to the restrained unavailable preview.
			}
		}
		return { tool: "reconciliation", preview: null };
	}

	if (message.includes("delete_presentation(")) {
		const deleteMatch =
			/delete_presentation\(\{"lecture_id":\s*"([^"]+)"\}\)\?$/.exec(message);
		if (deleteMatch) {
			return {
				tool: "presentation_delete",
				preview: { lecture_id: deleteMatch[1] },
			};
		}
		return { tool: "presentation_delete", preview: null };
	}

	if (message.includes("replace_presentation(")) {
		const presMatch = /replace_presentation\((\{.*\})\)\?$/s.exec(message);
		if (presMatch) {
			try {
				const payload = JSON.parse(presMatch[1]) as {
					command?: PresentationProposal;
				};
				if (payload.command) {
					return {
						tool: "presentation",
						preview: payload.command as unknown as Record<string, unknown>,
					};
				}
			} catch {
				// fall through
			}
		}
		return { tool: "presentation", preview: null };
	}

	const marker = "replace_course_plan(";
	const start = message.indexOf(marker);
	const end = message.lastIndexOf(")?");
	if (start === -1 || end <= start) return null;
	try {
		const payload = JSON.parse(message.slice(start + marker.length, end)) as {
			command?: CoursePlanProposal;
		};
		return { tool: "course_plan", preview: payload.command ?? null };
	} catch {
		return { tool: "course_plan", preview: null };
	}
}

function isReconciliationProposal(
	value: Record<string, unknown> | null,
): value is ReconciliationProposal {
	if (value === null || typeof value.summary !== "string") return false;
	if (!Array.isArray(value.entries) || value.entries.length === 0) return false;
	return value.entries.every(
		(entry) =>
			typeof entry === "object" &&
			entry !== null &&
			typeof (entry as Record<string, unknown>).path === "string" &&
			((entry as Record<string, unknown>).content === null ||
				typeof (entry as Record<string, unknown>).content === "string"),
	);
}

function ApprovalCard({
	approval,
	onResolve,
}: {
	approval: AgentInterrupt;
	onResolve: (approved: boolean) => void;
}) {
	const parsed = approvalProposal(approval);
	const isPresentation = parsed?.tool === "presentation";
	const isPresentationDelete = parsed?.tool === "presentation_delete";
	const isReconciliation = parsed?.tool === "reconciliation";
	const courseProposal =
		!isPresentation && !isPresentationDelete && !isReconciliation
			? (parsed?.preview as CoursePlanProposal | null)
			: null;
	const presentationProposal = isPresentation
		? (parsed?.preview as PresentationProposal | null)
		: null;
	const deleteLectureId = isPresentationDelete
		? (parsed?.preview as { lecture_id?: string })?.lecture_id
		: null;
	const reconciliationPreview = isReconciliation
		? (parsed?.preview ?? null)
		: null;
	const reconciliationProposal: ReconciliationProposal | null =
		isReconciliationProposal(reconciliationPreview)
			? reconciliationPreview
			: null;

	let kicker = "Course Plan proposal";
	if (isPresentation) kicker = "Presentation proposal";
	else if (isPresentationDelete) kicker = "Presentation proposal";
	else if (isReconciliation) kicker = "Workspace Reconciliation";

	let heading = "Apply this change?";
	if (isPresentation) heading = "Apply this presentation change?";
	else if (isPresentationDelete) heading = "Delete this Presentation?";
	else if (isReconciliation) heading = "Apply this Workspace Reconciliation?";

	return (
		<section className="approval-card" aria-labelledby="approval-heading">
			<p className="section-kicker">{kicker}</p>
			<h3 id="approval-heading">{heading}</h3>
			{isReconciliation ? (
				reconciliationProposal ? (
					<div className="proposal-sheet reconciliation-proposal">
						<div>
							<strong>{reconciliationProposal.summary}</strong>
							<span>
								{reconciliationProposal.entries.length} canonical path
								{reconciliationProposal.entries.length === 1 ? "" : "s"}
							</span>
						</div>
						<ul aria-label="Reconciliation patch">
							{reconciliationProposal.entries.map((entry) => (
								<li key={entry.path}>
									<details>
										<summary>
											<span>
												{entry.content === null ? "Remove" : "Replace"}
											</span>
											<code>{entry.path}</code>
										</summary>
										{entry.content === null ? (
											<p>This canonical file will be removed.</p>
										) : (
											<pre>{entry.content}</pre>
										)}
									</details>
								</li>
							))}
						</ul>
					</div>
				) : (
					<p>
						The agent proposed a Workspace Reconciliation, but its bounded patch
						preview could not be read.
					</p>
				)
			) : isPresentationDelete ? (
				<p>
					{deleteLectureId
						? `The agent wants to delete the Presentation for lecture ${deleteLectureId}.`
						: "The agent wants to delete a Presentation."}
				</p>
			) : isPresentation ? (
				presentationProposal ? (
					<div className="proposal-sheet">
						<div>
							<strong>
								{presentationProposal.slides.length} slide
								{presentationProposal.slides.length !== 1 ? "s" : ""}
							</strong>
							{presentationProposal.replace_all_slides ? (
								<span>Replace all slides</span>
							) : null}
						</div>
						<ol>
							{presentationProposal.slides.map((slide, index) => (
								<li key={`${slide.layout}-${slide.title ?? index}`}>
									<span>{String(index + 1).padStart(2, "0")}</span>
									<span className="slide-layout-badge">{slide.layout}</span>
									{slide.title ?? "(no title)"}
									{slide.archived ? " [archived]" : ""}
								</li>
							))}
						</ol>
					</div>
				) : (
					<p>
						The agent wants to create or update a Presentation, but its proposal
						could not be read.
					</p>
				)
			) : courseProposal ? (
				<div className="proposal-sheet">
					<div>
						<strong>{courseProposal.title}</strong>
						<span>{courseProposal.audience}</span>
					</div>
					<ol>
						{courseProposal.lectures.map((lecture, index) => (
							<li key={`${lecture.id ?? "new"}-${lecture.title}`}>
								<span>{String(index + 1).padStart(2, "0")}</span>
								{lecture.title}
							</li>
						))}
					</ol>
				</div>
			) : (
				<p>
					The agent proposed a Course Plan, but its preview could not be read.
				</p>
			)}
			{isReconciliation ? (
				<p className="approval-scope">
					Approval applies this exact canonical-file patch, verifies the
					resulting Course state, and creates the named Course Revision.
				</p>
			) : !isPresentation && !isPresentationDelete ? (
				<p className="approval-scope">
					This saves the Course title, intent, and Lecture spine. It does not
					create Lecture content yet.
				</p>
			) : null}
			<div className="approval-actions">
				<button
					type="button"
					className="secondary-action"
					onClick={() => onResolve(false)}
				>
					{isReconciliation
						? "Keep Workspace Drift"
						: isPresentation || isPresentationDelete
							? "Skip"
							: "Keep current plan"}
				</button>
				<button
					type="button"
					className="primary-action"
					disabled={isReconciliation && !reconciliationProposal}
					onClick={() => onResolve(true)}
				>
					{isReconciliation
						? "Apply Reconciliation"
						: isPresentationDelete
							? "Delete"
							: isPresentation
								? "Apply"
								: "Save Course Plan"}
				</button>
			</div>
		</section>
	);
}

export function AgentPanel({
	embedded = false,
	initialMessages,
	initialApproval,
	catalog,
	onCatalogChange,
	onOpenModels,
	onCourseChange,
	onRunningChange,
	onPresentationsChange,
	chatContext,
	reconciliationDriftId,
	onChatContextCleared,
	onReconciliationDriftCleared,
	onConversationCleared,
	onTranscriptChange,
}: AgentPanelProps) {
	const [messages, setMessages] = useState(initialMessages);
	const [prompt, setPrompt] = useState("");
	const [activities, setActivities] = useState<Activity[]>([]);
	const [approval, setApproval] = useState<AgentInterrupt | null>(
		initialApproval,
	);
	const [running, setRunning] = useState(false);
	const [runStatus, setRunStatus] = useState(() =>
		catalog.selected_model_id
			? "Course Agent is ready."
			: "Course Agent needs a model.",
	);
	const [error, setError] = useState<string | null>(null);
	const [conversationList, setConversationList] =
		useState<ConversationList | null>(null);
	const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
	const [renaming, setRenaming] = useState<string | null>(null);
	const [draftTitle, setDraftTitle] = useState("");
	const [compaction, setCompaction] = useState<CompactionPreview | null>(null);
	const [summary, setSummary] = useState("");
	const [conversationBusy, setConversationBusy] = useState(false);
	const [conversationOpen, setConversationOpen] = useState(false);
	const abortRef = useRef<AbortController | null>(null);
	const renameInputRef = useRef<HTMLInputElement | null>(null);
	const chatEndRef = useRef<HTMLDivElement | null>(null);
	const statusModelIdRef = useRef(catalog.selected_model_id);
	const selected = catalog.model_presets.find(
		(preset) => preset.id === catalog.selected_model_id,
	);
	const selectedAccount = catalog.provider_accounts.find(
		(account) => account.id === selected?.provider_account_id,
	);
	const lastMessageContent = messages.at(-1)?.content;

	useEffect(() => setMessages(initialMessages), [initialMessages]);
	useEffect(() => setApproval(initialApproval), [initialApproval]);
	useEffect(() => { if (renaming) renameInputRef.current?.focus(); }, [renaming]);
	useEffect(() => {
		const controller = new AbortController();
		void fetch("/api/conversations", { signal: controller.signal })
			.then(async (response) => {
				if (!response.ok) throw new Error(await responseError(response));
				setConversationList((await response.json()) as ConversationList);
			})
			.catch((caught: unknown) => {
				if (!(caught instanceof DOMException && caught.name === "AbortError")) {
					setError(
						caught instanceof Error
							? caught.message
							: "Conversations could not be loaded.",
					);
				}
			});
		return () => controller.abort();
	}, []);
	useEffect(() => onRunningChange(running), [onRunningChange, running]);
	useEffect(() => {
		const selectedModelId = selected?.id ?? null;
		if (!running && statusModelIdRef.current !== selectedModelId) {
			statusModelIdRef.current = selectedModelId;
			setRunStatus(
				selected ? "Course Agent is ready." : "Course Agent needs a model.",
			);
		}
	}, [running, selected]);
	useEffect(() => {
		if (lastMessageContent !== undefined || activities.length > 0 || approval) {
			chatEndRef.current?.scrollIntoView({ behavior: "auto" });
		}
	}, [lastMessageContent, activities.length, approval]);
	useEffect(
		() => () => {
			abortRef.current?.abort();
			onRunningChange(false);
		},
		[onRunningChange],
	);

	async function selectModel(modelId: string) {
		setError(null);
		const response = await fetch("/api/models/selected", {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ model_id: modelId }),
		});
		if (!response.ok) {
			setError("The Model Preset could not be selected.");
			return;
		}
		onCatalogChange((await response.json()) as ModelCatalog);
	}

	async function run(
		messagesForRun: ChatMessage[],
		resume?: object[],
		modeForRun: AgentMode = "autonomous",
	) {
		if (!conversationList) return;
		setActivities([]);
		setError(null);
		setRunStatus("Course Agent is working.");
		setRunning(true);
		let assistantId: string | null = null;
		const controller = new AbortController();
		abortRef.current = controller;
		try {
			const result = await streamAgentRun(
				{
					threadId: conversationList.active_id,
					runId: crypto.randomUUID(),
					state: {},
					messages: messagesForRun,
					tools: [],
					context: [],
					forwardedProps: {
						mode: reconciliationDriftId ? "guided" : modeForRun,
						...(reconciliationDriftId ? { reconciliationDriftId } : {}),
					},
					...(resume ? { resume } : {}),
				},
				{
					onTextStart: (identity) => {
						assistantId = identity;
						setRunStatus("Course Agent is streaming a response.");
					},
					onText: (delta) => {
						if (assistantId) {
							const identity = assistantId;
							setMessages((current) =>
								updateAssistantMessage(current, identity, delta),
							);
						}
					},
					onActivity: (id, title, detail) =>
						setActivities((current) => [...current, { id, title, detail }]),
					onState: async (snapshot) => {
						const state = snapshot as {
							course?: CoursePlan;
						};
						if (state.course) await onCourseChange(state.course);
						if (
							onPresentationsChange &&
							(snapshot as Record<string, unknown>).presentations
						) {
							await onPresentationsChange();
						}
					},
					onInterrupt: setApproval,
				},
				controller.signal,
			);
			if (result.cancelled) {
				setRunStatus("Course Agent stopped.");
				return;
			}
			const transcriptResponse = await fetch("/api/chat");
			if (transcriptResponse.ok) {
				const transcript = (await transcriptResponse.json()) as {
					messages: ChatMessage[];
					approval: AgentInterrupt | null;
				};
				setMessages(transcript.messages);
				setApproval(transcript.approval);
				onTranscriptChange(transcript.messages, transcript.approval);
				if (reconciliationDriftId && transcript.approval === null) {
					onReconciliationDriftCleared?.();
				}
			}
			setRunStatus("Course Agent finished.");
			void refreshConversations().catch(() => {
				setError("The conversation list could not be refreshed.");
			});
		} catch (caught) {
			if (caught instanceof DOMException && caught.name === "AbortError") {
				setRunStatus("Course Agent stopped.");
				return;
			}
			setRunStatus("Course Agent encountered an error.");
			setError(
				caught instanceof Error
					? caught.message
					: "The Course Agent run failed.",
			);
		} finally {
			setRunning(false);
		}
	}

	async function cancelRun() {
		abortRef.current?.abort();
		try {
			await fetch("/api/agent/cancel", { method: "POST" });
		} catch {
			// Best-effort; the abort already stops the frontend stream
		}
	}

	async function refreshConversations() {
		const response = await fetch("/api/conversations");
		if (!response.ok) throw new Error(await responseError(response));
		setConversationList((await response.json()) as ConversationList);
	}

	async function conversationAction(action: () => Promise<void>) {
		if (running || conversationBusy) return;
		setConversationBusy(true);
		setError(null);
		try {
			await action();
			await refreshConversations();
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The conversation could not be changed.",
			);
		} finally {
			setConversationBusy(false);
		}
	}

	async function requestConversation(
		path: string,
		method: string,
		body?: object,
	) {
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
		return response;
	}

	async function loadActiveTranscript() {
		const response = await fetch("/api/chat");
		if (!response.ok) throw new Error(await responseError(response));
		const transcript = (await response.json()) as Transcript;
		setMessages(transcript.messages);
		setApproval(transcript.approval);
		setActivities([]);
		setPrompt("");
		setCompaction(null);
		setConfirmingDelete(null);
		onTranscriptChange(transcript.messages, transcript.approval);
	}

	function createConversation() {
		void conversationAction(async () => {
			await requestConversation("/api/conversations", "POST", {});
			await loadActiveTranscript();
			onConversationCleared();
		});
	}

	function activateConversation(id: string) {
		void conversationAction(async () => {
			await requestConversation(
				`/api/conversations/${encodeURIComponent(id)}/activate`,
				"POST",
			);
			await loadActiveTranscript();
		});
	}

	function archiveConversation(id: string, archived: boolean) {
		void conversationAction(async () => {
			await requestConversation(
				`/api/conversations/${encodeURIComponent(id)}`,
				"PATCH",
				{ archived },
			);
			if (id === conversationList?.active_id) await loadActiveTranscript();
		});
	}

	function renameConversation(id: string) {
		const title = draftTitle.trim();
		if (!title) return;
		void conversationAction(async () => {
			await requestConversation(
				`/api/conversations/${encodeURIComponent(id)}`,
				"PATCH",
				{ title },
			);
			setRenaming(null);
		});
	}

	function deleteConversation(id: string) {
		void conversationAction(async () => {
			await requestConversation(
				`/api/conversations/${encodeURIComponent(id)}`,
				"DELETE",
			);
			await loadActiveTranscript();
		});
	}

	function previewCompaction() {
		const id = conversationList?.active_id;
		if (!id) return;
		void conversationAction(async () => {
			const response = await requestConversation(
				`/api/conversations/${encodeURIComponent(id)}/compact/preview`,
				"POST",
			);
			const preview = (await response.json()) as CompactionPreview;
			setCompaction(preview);
			setSummary(preview.summary);
		});
	}

	function confirmCompaction() {
		const id = conversationList?.active_id;
		if (!id || !compaction || !summary.trim()) return;
		void conversationAction(async () => {
			await requestConversation(
				`/api/conversations/${encodeURIComponent(id)}/compact`,
				"POST",
				{
					summary: summary.trim(),
					revision: compaction.revision,
				},
			);
			await loadActiveTranscript();
		});
	}

	function sendMessage(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const content = prompt.trim();
		if (!content || running || !conversationList) return;
		const contextPrefix = chatContext ? `[Context: ${chatContext}]\n\n` : "";
		const userMessage: ChatMessage = {
			id: crypto.randomUUID(),
			role: "user",
			content: contextPrefix + content,
		};
		setMessages((current) => [...current, userMessage]);
		setPrompt("");
		setApproval(null);
		if (onChatContextCleared) onChatContextCleared();
		void run(
			[userMessage],
			undefined,
			reconciliationDriftId ? "guided" : "autonomous",
		);
	}

	function resolveApproval(approved: boolean) {
		if (!approval || running || !conversationList) return;
		const pending = approval;
		setApproval(null);
		void run(
			[],
			[{ interruptId: pending.id, status: "resolved", payload: { approved } }],
			"guided",
		);
	}

	return (
		<section
			className={`agent-panel${embedded ? " is-embedded" : ""}`}
			id="agent"
			aria-labelledby="agent-heading"
		>
			<header className="agent-panel-heading">
				<div className="agent-heading-line">
					<div>
						<p className="section-kicker">
							{embedded ? "Collaborate" : "Course Agent"}
						</p>
						{embedded ? (
							<h2 id="agent-heading">Course Agent</h2>
						) : (
							<>
								<h1 id="agent-heading">Work with your course</h1>
								<p>
									Ask for a change, review the result, and keep shaping it here.
								</p>
							</>
						)}
					</div>
					<span
						className={`agent-status${running ? " is-running" : ""}`}
						id="agent-run-status"
						role="status"
						aria-live="polite"
						aria-atomic="true"
					>
						{runStatus}
					</span>
				</div>

				<div className="agent-toolbar">
					{selected ? (
						<div className="agent-model-picker">
							<label htmlFor="agent-model">Model</label>
							<select
								id="agent-model"
								value={selected.id}
								disabled={running}
								onChange={(event) => void selectModel(event.target.value)}
							>
								{catalog.model_presets.map((preset) => (
									<option key={preset.id} value={preset.id}>
										{preset.name}
									</option>
								))}
							</select>
							<small>
								{selected.model} · {selectedAccount?.name}
							</small>
						</div>
					) : (
						<div className="agent-model-empty">
							<p>Add a model before starting a conversation.</p>
							<button
								className="secondary-action compact-action"
								type="button"
								onClick={onOpenModels}
							>
								Set up a model
							</button>
						</div>
					)}

					<div className="conversation-actions">
						{running ? (
							<button
								className="secondary-action compact-action"
								type="button"
								onClick={cancelRun}
							>
								Stop
							</button>
						) : (
							<button
								className="secondary-action compact-action"
								type="button"
								aria-expanded={conversationOpen}
								aria-controls="conversation-library"
								onClick={() => setConversationOpen((open) => !open)}
							>
								Conversations
							</button>
						)}
					</div>
				</div>
				{conversationOpen ? (
					<section
						className="conversation-library"
						id="conversation-library"
						aria-label="Conversations"
					>
						<div className="conversation-library-heading">
							<strong>Conversations</strong>
							<button
								type="button"
								className="quiet-action compact-action"
								onClick={createConversation}
								disabled={
									running || conversationBusy || approval !== null || messages.length === 0
								}
								title={
									approval
										? "Resolve the pending approval first"
										: messages.length === 0
											? "This conversation is already empty"
											: undefined
								}
							>
								New conversation
							</button>
						</div>
						{approval ? (
							<p className="conversation-guard">
								Resolve the pending approval before changing conversations.
							</p>
						) : null}
						{conversationList ? (
							<ul className="conversation-list">
								{conversationList.conversations.map((conversation) => (
									<li
										key={conversation.id}
										className={
											conversation.id === conversationList.active_id
												? "is-active"
												: ""
										}
									>
										<div className="conversation-entry">
											<button
												type="button"
												className="conversation-select"
												onClick={() => activateConversation(conversation.id)}
												disabled={
													running ||
													conversationBusy ||
													approval !== null ||
													conversation.archived ||
													conversation.id === conversationList.active_id
												}
												title={
													conversation.archived
														? "Restore this conversation before opening it"
														: undefined
												}
												aria-current={
													conversation.id === conversationList.active_id
														? "true"
														: undefined
												}
											>
												{conversation.title}
											</button>
											<small>
												{conversation.archived
													? "Archived"
													: conversation.id === conversationList.active_id
														? "Current"
														: "Saved"}
											</small>
										</div>
										<div className="conversation-entry-actions">
											<button
												type="button"
												className="quiet-action compact-action"
												onClick={() => {
													setRenaming(conversation.id);
													setDraftTitle(conversation.title);
												}}
												disabled={running || conversationBusy}
											>
												Rename
											</button>
											<button
												type="button"
												className="quiet-action compact-action"
												onClick={() =>
													archiveConversation(
														conversation.id,
														!conversation.archived,
													)
												}
												disabled={
													running ||
													conversationBusy ||
													approval !== null ||
													conversation.has_pending_approval ||
													(conversation.id === conversationList.active_id &&
														!conversation.archived)
												}
												title={
													conversation.has_pending_approval
														? "Resolve the pending approval first"
														: conversation.id === conversationList.active_id &&
																!conversation.archived
															? "Open another conversation before archiving this one"
															: undefined
												}
											>
												{conversation.archived ? "Restore" : "Archive"}
											</button>
											<button
												type="button"
												className="quiet-action compact-action destructive-action"
												onClick={() => setConfirmingDelete(conversation.id)}
												disabled={
													running ||
													conversationBusy ||
													approval !== null ||
													conversation.has_pending_approval
												}
												title={
													conversation.has_pending_approval
														? "Resolve the pending approval first"
														: undefined
												}
											>
												Delete
											</button>
										</div>
										{renaming === conversation.id ? (
											<form
												className="conversation-rename"
												onSubmit={(event) => {
													event.preventDefault();
													renameConversation(conversation.id);
												}}
											>
												<label
													htmlFor={`conversation-title-${conversation.id}`}
												>
													Conversation title
												</label>
												<input
													id={`conversation-title-${conversation.id}`}
													value={draftTitle}
													onChange={(event) =>
														setDraftTitle(event.target.value)
													}
													maxLength={200}
													required
													ref={renameInputRef}
												/>
												<div className="conversation-entry-actions">
													<button
														type="button"
														className="quiet-action compact-action"
														onClick={() => setRenaming(null)}
													>
														Cancel
													</button>
													<button
														type="submit"
														className="secondary-action compact-action"
														disabled={!draftTitle.trim() || conversationBusy}
													>
														Save title
													</button>
												</div>
											</form>
										) : null}
										{confirmingDelete === conversation.id ? (
											<fieldset className="conversation-confirmation">
												<legend>
													Delete {conversation.title} permanently?
												</legend>
												<button
													type="button"
													className="quiet-action compact-action"
													onClick={() => setConfirmingDelete(null)}
												>
													Cancel
												</button>
												<button
													type="button"
													className="secondary-action destructive-action compact-action"
													onClick={() => deleteConversation(conversation.id)}
												>
													Delete now
												</button>
											</fieldset>
										) : null}
									</li>
								))}
							</ul>
						) : (
							<p role="status">Loading conversations…</p>
						)}
						<button
							type="button"
							className="quiet-action compact-action"
							onClick={previewCompaction}
							disabled={
								running ||
								conversationBusy ||
								approval !== null ||
								messages.length === 0
							}
							title={
								approval ? "Resolve the pending approval first" : undefined
							}
						>
							Compact current conversation
						</button>
						{compaction ? (
							<div className="compaction-preview">
								<label htmlFor="compaction-summary">
									Review the summary that will replace model context from{" "}
									{compaction.source_message_count} messages
								</label>
								<textarea
									id="compaction-summary"
									value={summary}
									onChange={(event) => setSummary(event.target.value)}
									rows={7}
									maxLength={4000}
									aria-invalid={!summary.trim()}
								/>
								<p>The full conversation remains available in history.</p>
								<div className="conversation-entry-actions">
									<button
										type="button"
										className="quiet-action compact-action"
										onClick={() => setCompaction(null)}
									>
										Cancel
									</button>
									<button
										type="button"
										className="primary-action compact-action"
										onClick={confirmCompaction}
										disabled={
											!summary.trim() ||
											conversationBusy ||
											running ||
											approval !== null
										}
									>
										Use this summary
									</button>
								</div>
							</div>
						) : null}
					</section>
				) : null}
				{selected && selectedAccount ? (
					<p className="agent-network-disclosure" role="note">
						When you send a message, it and any Source excerpts needed for the
						response leave this device for{" "}
						<strong>{selectedAccount.name}</strong>
						at <strong>{selectedAccount.base_url}</strong>, using the selected
						Model Preset. Its credential stays in Course Harness&apos;s private
						provider store. Only the credential saved for this Provider Account
						is used.
					</p>
				) : null}
			</header>

			<section className="chat-scroll-container" aria-label="Conversation">
				<ol
					className="chat-messages"
					aria-label="Course Agent conversation"
					// biome-ignore lint/a11y/noNoninteractiveTabindex: this is the element with scrollable conversation content
					tabIndex={0}
				>
					{messages.length === 0 ? (
						<li className="chat-empty">
							<p className="section-kicker">Start here</p>
							<h2>What should we work on?</h2>
							<p>
								Describe the outcome you want. Changes are applied as the agent
								works and remain editable in the course.
							</p>
							<div className="prompt-starters">
								{[
									"Draft a practical course plan",
									"Review the lecture sequence",
									"Create slides for the next lecture",
								].map((starter) => (
									<button
										key={starter}
										type="button"
										onClick={() => setPrompt(starter)}
										disabled={!selected || !conversationList}
									>
										{starter}
									</button>
								))}
							</div>
						</li>
					) : (
						messages.map((message) => {
							const parts = messageParts(message.content);
							return (
								<li className={`chat-message ${message.role}`} key={message.id}>
									<span>
										{message.role === "user" ? "You" : "Course Agent"}
									</span>
									<div className="message-body">
										{parts.context ? <small>{parts.context}</small> : null}
										<p>{parts.body}</p>
									</div>
								</li>
							);
						})
					)}
				</ol>
				<div ref={chatEndRef} />
			</section>

			<div className="agent-activity" aria-live="polite">
				{activities.map((activity) => (
					<p key={activity.id}>
						<strong>{activity.title}</strong>
						<span>{activity.detail}</span>
					</p>
				))}
				{running && activities.length === 0 ? (
					<p>Reading your direction…</p>
				) : null}
			</div>

			{approval ? (
				<ApprovalCard approval={approval} onResolve={resolveApproval} />
			) : null}

			{error ? (
				<p className="notice error-notice" role="alert">
					{error}
				</p>
			) : null}

			<form
				className="chat-composer"
				onSubmit={sendMessage}
				aria-busy={running}
			>
				{chatContext ? (
					<div className="chat-context-indicator">
						<span className="context-label">Context</span>
						<span className="context-text">{chatContext}</span>
						<button
							type="button"
							className="quiet-action compact-action"
							onClick={() => {
								onChatContextCleared?.();
								onReconciliationDriftCleared?.();
							}}
						>
							Remove
						</button>
					</div>
				) : null}
				<label htmlFor="course-agent-message">Message the Course Agent</label>
				<textarea
					id="course-agent-message"
					value={prompt}
					onChange={(event) => setPrompt(event.target.value)}
					onKeyDown={(event) => {
						if (event.key === "Enter" && !event.shiftKey) {
							event.preventDefault();
							sendMessage(event as unknown as FormEvent<HTMLFormElement>);
						}
					}}
					rows={3}
					placeholder="Ask for a change, a review, or a new draft…"
					maxLength={4000}
					disabled={running || approval !== null || !selected || !conversationList}
					aria-describedby="course-agent-help"
				/>
				<div>
					<small id="course-agent-help">
						Changes apply as the agent works. Enter to send; Shift+Enter adds a
						line.
					</small>
					<button
						className="primary-action compact-action"
						type="submit"
						disabled={
							running || approval !== null || !selected || !conversationList || !prompt.trim()
						}
					>
						Send message
					</button>
				</div>
			</form>
		</section>
	);
}
