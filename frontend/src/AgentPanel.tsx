import { type FormEvent, useEffect, useRef, useState } from "react";

import { type AgentInterrupt, streamAgentRun } from "./agentStream";
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
	const [error, setError] = useState<string | null>(null);
	const [confirmingClear, setConfirmingClear] = useState(false);
	const abortRef = useRef<AbortController | null>(null);
	const chatEndRef = useRef<HTMLDivElement | null>(null);
	const selected = catalog.model_presets.find(
		(preset) => preset.id === catalog.selected_model_id,
	);
	const selectedAccount = catalog.provider_accounts.find(
		(account) => account.id === selected?.provider_account_id,
	);
	const lastMessageContent = messages.at(-1)?.content;

	useEffect(() => setMessages(initialMessages), [initialMessages]);
	useEffect(() => setApproval(initialApproval), [initialApproval]);
	useEffect(() => onRunningChange(running), [onRunningChange, running]);
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
		setActivities([]);
		setError(null);
		setRunning(true);
		let assistantId: string | null = null;
		const controller = new AbortController();
		abortRef.current = controller;
		try {
			const result = await streamAgentRun(
				{
					threadId: "course-agent",
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
			if (result.cancelled) return;
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
		} catch (caught) {
			if (caught instanceof DOMException && caught.name === "AbortError") {
				return;
			}
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

	async function clearConversation() {
		if (running || approval) return;
		setError(null);
		try {
			const response = await fetch("/api/chat", { method: "DELETE" });
			if (!response.ok) {
				throw new Error("The conversation could not be cleared.");
			}
			setMessages([]);
			setActivities([]);
			setConfirmingClear(false);
			onConversationCleared();
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "The conversation could not be cleared.",
			);
		}
	}

	function sendMessage(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const content = prompt.trim();
		if (!content || running) return;
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
		if (!approval || running) return;
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
					<span className={`agent-status${running ? " is-running" : ""}`}>
						{running ? "Working" : selected ? "Ready" : "Needs model"}
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
						) : confirmingClear ? (
							<fieldset className="clear-confirmation">
								<legend>Confirm clear conversation</legend>
								<span>This cannot be undone.</span>
								<button
									className="quiet-action compact-action"
									type="button"
									onClick={() => setConfirmingClear(false)}
								>
									Cancel
								</button>
								<button
									className="secondary-action destructive-action compact-action"
									type="button"
									onClick={() => void clearConversation()}
								>
									Clear now
								</button>
							</fieldset>
						) : (
							<button
								className="quiet-action compact-action"
								type="button"
								disabled={messages.length === 0 || approval !== null}
								onClick={() => setConfirmingClear(true)}
							>
								Clear conversation
							</button>
						)}
					</div>
				</div>
			</header>

			<section className="chat-scroll-container" aria-label="Conversation">
				<ol className="chat-messages" aria-label="Course Agent conversation">
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
										disabled={!selected}
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

			<div className="agent-activity" aria-live="polite" aria-atomic="true">
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
					disabled={running || approval !== null || !selected}
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
							running || approval !== null || !selected || !prompt.trim()
						}
					>
						Send message
					</button>
				</div>
			</form>
		</section>
	);
}
