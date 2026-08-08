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

type Activity = { id: string; title: string; detail: string };

type AgentMode = "guided" | "autonomous";

type AgentPanelProps = {
	initialMessages: ChatMessage[];
	initialApproval: AgentInterrupt | null;
	catalog: ModelCatalog;
	onCatalogChange: (catalog: ModelCatalog) => void;
	onOpenModels: () => void;
	modelsOpen: boolean;
	onCourseChange: (course: CoursePlan) => Promise<void>;
	onRunningChange: (running: boolean) => void;
	onPresentationsChange?: () => Promise<void>;
	chatContext?: string | null;
	onChatContextCleared?: () => void;
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

function approvalProposal(interrupt: AgentInterrupt): {
	tool: "course_plan" | "presentation" | "presentation_delete";
	preview: Record<string, unknown> | null;
} | null {
	const message = interrupt.message;
	if (!message) return null;

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
	const courseProposal =
		!isPresentation && !isPresentationDelete
			? (parsed?.preview as CoursePlanProposal | null)
			: null;
	const presentationProposal = isPresentation
		? (parsed?.preview as PresentationProposal | null)
		: null;
	const deleteLectureId = isPresentationDelete
		? (parsed?.preview as { lecture_id?: string })?.lecture_id
		: null;

	let kicker = "Course Plan proposal";
	if (isPresentation) kicker = "Presentation proposal";
	else if (isPresentationDelete) kicker = "Presentation proposal";

	let heading = "Apply this change?";
	if (isPresentation) heading = "Apply this presentation change?";
	else if (isPresentationDelete) heading = "Delete this Presentation?";

	return (
		<section className="approval-card" aria-labelledby="approval-heading">
			<p className="section-kicker">{kicker}</p>
			<h3 id="approval-heading">{heading}</h3>
			{isPresentationDelete ? (
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
			{!isPresentation && !isPresentationDelete ? (
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
					{isPresentation || isPresentationDelete
						? "Skip"
						: "Keep current plan"}
				</button>
				<button
					type="button"
					className="primary-action"
					onClick={() => onResolve(true)}
				>
					{isPresentationDelete
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
	initialMessages,
	initialApproval,
	catalog,
	onCatalogChange,
	onOpenModels,
	modelsOpen,
	onCourseChange,
	onRunningChange,
	onPresentationsChange,
	chatContext,
	onChatContextCleared,
}: AgentPanelProps) {
	const [messages, setMessages] = useState(initialMessages);
	const [prompt, setPrompt] = useState("");
	const [activities, setActivities] = useState<Activity[]>([]);
	const [approval, setApproval] = useState<AgentInterrupt | null>(
		initialApproval,
	);
	const [running, setRunning] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [mode, setMode] = useState<AgentMode>("guided");
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

	function isContinueUntilDone(text: string): boolean {
		const lower = text.toLowerCase().trim();
		return (
			lower === "continue until done" ||
			lower === "continue until finished" ||
			lower === "carry on until done"
		);
	}

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
		modeForRun: AgentMode = mode,
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
					forwardedProps: { mode: modeForRun },
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
		const modeForRun = isContinueUntilDone(content) ? "autonomous" : mode;
		if (modeForRun === "autonomous" && mode !== "autonomous") {
			setMode("autonomous");
		}
		setMessages((current) => [...current, userMessage]);
		setPrompt("");
		setApproval(null);
		if (onChatContextCleared) onChatContextCleared();
		void run([userMessage], undefined, modeForRun);
	}

	function resolveApproval(approved: boolean) {
		if (!approval || running) return;
		const pending = approval;
		setApproval(null);
		void run(
			[],
			[{ interruptId: pending.id, status: "resolved", payload: { approved } }],
		);
	}

	return (
		<aside className="agent-panel" id="agent" aria-labelledby="agent-heading">
			<div className="agent-panel-heading">
				<div className="agent-heading-line">
					<div>
						<p className="section-kicker">Course Agent</p>
						<h2 id="agent-heading">Plan in conversation</h2>
					</div>
					<span className={`agent-status${running ? " is-running" : ""}`}>
						{running ? "Working" : selected ? "Ready" : "Needs model"}
					</span>
				</div>
				{running ? (
					<button
						className="secondary-action compact-action"
						type="button"
						onClick={cancelRun}
					>
						Cancel run
					</button>
				) : null}
				<div className="agent-mode-toggle">
					<label htmlFor="agent-mode">Behaviour</label>
					<select
						id="agent-mode"
						value={mode}
						disabled={running}
						onChange={(event) => setMode(event.target.value as AgentMode)}
					>
						<option value="guided">Guided</option>
						<option value="autonomous">Autonomous</option>
					</select>
					<small>
						{mode === "guided"
							? "Course Plan changes wait for your approval."
							: "Course Plan changes apply automatically."}
					</small>
				</div>
				{selected ? (
					<div className="agent-model-picker">
						<label htmlFor="agent-model">Model Preset</label>
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
						<p>
							Add a Provider Account and Model Preset before starting a run.
						</p>
						{!modelsOpen ? (
							<button
								className="secondary-action compact-action"
								type="button"
								onClick={onOpenModels}
							>
								Open Models
							</button>
						) : null}
					</div>
				)}
			</div>

			<div className="chat-scroll-container">
				<ol className="chat-messages" aria-label="Course Agent conversation">
					{messages.length === 0 ? (
						<li className="chat-empty">
							The Course Agent currently creates and revises the Course Plan:
							its intent and Lecture spine. Lecture content comes in a later
							authoring step.
						</li>
					) : (
						messages.map((message) => (
							<li className={`chat-message ${message.role}`} key={message.id}>
								<span>{message.role === "user" ? "You" : "Course Agent"}</span>
								<p>{message.content}</p>
							</li>
						))
					)}
				</ol>
				<div ref={chatEndRef} />
			</div>

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
							onClick={() => onChatContextCleared?.()}
						>
							Clear
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
					rows={4}
					placeholder="Create a four-Lecture Course Plan for…"
					maxLength={4000}
					disabled={running || approval !== null || !selected}
					aria-describedby="course-agent-help"
				/>
				<div>
					<small id="course-agent-help">
						{mode === "guided"
							? "Course Plan changes wait for your approval. Enter to send, Shift+Enter for newline."
							: "Course Plan changes apply automatically. Enter to send, Shift+Enter for newline."}
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
		</aside>
	);
}
