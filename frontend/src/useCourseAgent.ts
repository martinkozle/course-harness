import { useCallback, useEffect, useRef, useState } from "react";

import { type AgentInterrupt, streamAgentRun } from "./agentStream";
import { responseError } from "./api";
import type { CoursePlan, ModelCatalog } from "./models";
import { errorMessage, isAbort } from "./ui";

export type ChatMessage = {
	id: string;
	role: "user" | "assistant";
	content: string;
};

export type Conversation = {
	id: string;
	title: string;
	created_at: string;
	updated_at: string;
	archived: boolean;
	has_pending_approval: boolean;
};

export type ConversationList = {
	active_id: string;
	conversations: Conversation[];
};

type Transcript = { messages: ChatMessage[]; approval: AgentInterrupt | null };

export type CompactionPreview = {
	summary: string;
	source_message_count: number;
	revision: string | number;
};

export type Activity = { id: string; title: string; detail: string };

/** A focus hint the Course Author attaches to a message. */
export type AgentContext = {
	key: string;
	label: string;
	instruction: string;
};

/** What a finished run changed, so the transcript can link to it. */
export type RunResult = {
	planChanged: boolean;
	presentationIds: string[];
	deletedPresentationIds: string[];
	sourcesAdded: number;
	stopped: boolean;
};

export type AgentStatus =
	| "idle"
	| "running"
	| "stopping"
	| "stopped"
	| "failed"
	| "awaiting-approval";

type AgentMode = "guided" | "autonomous";

/** An image attached to the next message, uploaded to the Library as a Resource. */
export type Attachment = {
	id: string;
	name: string;
	mediaType: string;
	previewUrl: string;
	resourceId: string | null;
	state: "uploading" | "ready" | "failed";
	error: string | null;
};

/** An attachment as recorded in a sent message. */
export type MessageAttachment = {
	resourceId: string;
	mediaType: string;
	name: string;
};

export const ATTACHABLE_TYPES = ["image/png", "image/jpeg", "image/gif"];

const CONTEXT_PATTERN = /^\[Context: (.+?)\]\n\n([\s\S]*)$/;
const ATTACHMENT_PATTERN =
	/^\[Attachment: (resource-[0-9a-f]{12}) (\S+) "(.*)"\]$/;

function attachmentLine(attachment: MessageAttachment): string {
	const name = attachment.name.replace(/["\r\n\]]/g, "_");
	return `[Attachment: ${attachment.resourceId} ${attachment.mediaType} "${name}"]`;
}

export function messageParts(content: string): {
	attachments: MessageAttachment[];
	context: string | null;
	body: string;
} {
	const lines = content.split("\n");
	const attachments: MessageAttachment[] = [];
	for (const line of lines) {
		const match = ATTACHMENT_PATTERN.exec(line);
		if (!match) break;
		attachments.push({
			resourceId: match[1],
			mediaType: match[2],
			name: match[3],
		});
	}
	const rest = attachments.length
		? lines.slice(attachments.length).join("\n").replace(/^\n/, "")
		: content;
	const match = CONTEXT_PATTERN.exec(rest);
	return match
		? { attachments, context: match[1], body: match[2] }
		: { attachments, context: null, body: rest };
}

function appendAssistantDelta(
	messages: ChatMessage[],
	identity: string,
	delta: string,
): ChatMessage[] {
	const index = messages.findIndex((message) => message.id === identity);
	if (index === -1)
		return [...messages, { id: identity, role: "assistant", content: delta }];
	return messages.map((message, position) =>
		position === index
			? { ...message, content: message.content + delta }
			: message,
	);
}

async function requestJson(
	path: string,
	method = "GET",
	body?: object,
): Promise<Response> {
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

type Options = {
	catalog: ModelCatalog;
	onCourseChange: (course: CoursePlan) => Promise<void> | void;
	onPresentationsChange: () => Promise<void>;
	onSourcesChange: () => Promise<void>;
	onRunSettled: (result: RunResult) => void;
};

export function useCourseAgent({
	catalog,
	onCourseChange,
	onPresentationsChange,
	onSourcesChange,
	onRunSettled,
}: Options) {
	const [conversations, setConversations] = useState<ConversationList | null>(
		null,
	);
	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [approval, setApproval] = useState<AgentInterrupt | null>(null);
	const [activities, setActivities] = useState<Activity[]>([]);
	const [status, setStatus] = useState<AgentStatus>("idle");
	const [error, setError] = useState<string | null>(null);
	const [localDraft, setLocalDraft] = useState(false);
	const [conversationBusy, setConversationBusy] = useState(false);
	const [prompt, setPromptState] = useState("");
	const [context, setContext] = useState<AgentContext | null>(null);
	const [attachments, setAttachments] = useState<Attachment[]>([]);
	const [driftId, setDriftId] = useState<string | null>(null);
	const [results, setResults] = useState<Record<string, RunResult>>({});
	const [loaded, setLoaded] = useState(false);

	const abortRef = useRef<AbortController | null>(null);
	const cancelRequestedRef = useRef(false);
	const draftsRef = useRef(new Map<string, string>());
	const conversationsRef = useRef(conversations);
	conversationsRef.current = conversations;
	const localDraftRef = useRef(localDraft);
	localDraftRef.current = localDraft;
	const callbacks = useRef({
		onCourseChange,
		onPresentationsChange,
		onSourcesChange,
		onRunSettled,
	});
	callbacks.current = {
		onCourseChange,
		onPresentationsChange,
		onSourcesChange,
		onRunSettled,
	};

	const running = status === "running" || status === "stopping";
	const hasModel = catalog.model_presets.some(
		(preset) => preset.id === catalog.selected_model_id,
	);

	const draftKey = useCallback(
		() =>
			localDraftRef.current
				? "__draft__"
				: (conversationsRef.current?.active_id ?? "__none__"),
		[],
	);

	const setPrompt = useCallback(
		(value: string) => {
			draftsRef.current.set(draftKey(), value);
			setPromptState(value);
		},
		[draftKey],
	);

	const refreshConversations = useCallback(async () => {
		const response = await requestJson("/api/conversations");
		const list = (await response.json()) as ConversationList;
		setConversations(list);
		return list;
	}, []);

	const loadTranscript = useCallback(async () => {
		const response = await requestJson("/api/chat");
		const transcript = (await response.json()) as Transcript;
		setMessages(transcript.messages);
		setApproval(transcript.approval);
		setStatus(transcript.approval ? "awaiting-approval" : "idle");
		setActivities([]);
	}, []);

	useEffect(() => {
		let active = true;
		void (async () => {
			try {
				const [list] = await Promise.all([
					refreshConversations(),
					loadTranscript(),
				]);
				if (active) setPromptState(draftsRef.current.get(list.active_id) ?? "");
			} catch (caught) {
				if (active && !isAbort(caught))
					setError(errorMessage(caught, "Conversations could not be loaded."));
			} finally {
				if (active) setLoaded(true);
			}
		})();
		return () => {
			active = false;
			abortRef.current?.abort();
		};
	}, [loadTranscript, refreshConversations]);

	async function run(
		messagesForRun: ChatMessage[],
		options: { resume?: object[]; mode?: AgentMode; threadId?: string } = {},
	) {
		const threadId = options.threadId ?? conversationsRef.current?.active_id;
		if (!threadId) return;
		setActivities([]);
		setError(null);
		setStatus("running");
		cancelRequestedRef.current = false;
		let assistantId: string | null = null;
		const seenActivities: Activity[] = [];
		const controller = new AbortController();
		abortRef.current = controller;
		const mode: AgentMode = driftId ? "guided" : (options.mode ?? "autonomous");
		let settled: RunResult | null = null;
		try {
			const result = await streamAgentRun(
				{
					threadId,
					runId: crypto.randomUUID(),
					state: {},
					messages: messagesForRun,
					tools: [],
					context: [],
					forwardedProps: {
						mode,
						...(driftId ? { reconciliationDriftId: driftId } : {}),
					},
					...(options.resume ? { resume: options.resume } : {}),
				},
				{
					onTextStart: (identity) => {
						assistantId = identity;
					},
					onText: (delta) => {
						if (!assistantId) return;
						const identity = assistantId;
						setMessages((current) =>
							appendAssistantDelta(current, identity, delta),
						);
					},
					onActivity: (id, title, detail) => {
						seenActivities.push({ id, title, detail });
						setActivities((current) => [...current, { id, title, detail }]);
					},
					onState: async (snapshot) => {
						const state = snapshot as {
							course?: CoursePlan;
							presentations?: unknown;
							sources?: unknown;
						};
						if (state.course)
							await callbacks.current.onCourseChange(state.course);
						if (state.presentations)
							await callbacks.current.onPresentationsChange();
					},
					onInterrupt: (interrupt) => {
						setApproval(interrupt);
					},
				},
				controller.signal,
			);
			const stopped = result.cancelled || cancelRequestedRef.current;
			settled = summarize(seenActivities, stopped);
			if (stopped) {
				setStatus("stopped");
				return;
			}
			const transcriptResponse = await fetch("/api/chat");
			if (transcriptResponse.ok) {
				const transcript = (await transcriptResponse.json()) as Transcript;
				setMessages(transcript.messages);
				setApproval(transcript.approval);
				setStatus(transcript.approval ? "awaiting-approval" : "idle");
				if (driftId && transcript.approval === null) setDriftId(null);
				const lastAssistant = transcript.messages.findLast(
					(message) => message.role === "assistant",
				);
				if (lastAssistant && hasChanges(settled)) {
					const summary = settled;
					setResults((current) => ({
						...current,
						[lastAssistant.id]: summary,
					}));
				}
			} else {
				setStatus("idle");
			}
			void refreshConversations().catch(() => {
				setError("The conversation list could not be refreshed.");
			});
		} catch (caught) {
			if (isAbort(caught)) {
				setStatus("stopped");
				settled = summarize(seenActivities, true);
				return;
			}
			setStatus("failed");
			setError(errorMessage(caught, "The Course Agent run failed."));
			settled = summarize(seenActivities, false);
		} finally {
			if (abortRef.current === controller) abortRef.current = null;
			if (settled) {
				if (settled.sourcesAdded > 0) void callbacks.current.onSourcesChange();
				callbacks.current.onRunSettled(settled);
			}
		}
	}

	async function stop() {
		if (status !== "running") return;
		cancelRequestedRef.current = true;
		setStatus("stopping");
		try {
			const response = await fetch("/api/agent/cancel", { method: "POST" });
			if (!response.ok) throw new Error(await responseError(response));
			abortRef.current?.abort();
			setStatus("stopped");
		} catch (caught) {
			setStatus("running");
			setError(errorMessage(caught, "The Course Agent could not be stopped."));
		}
	}

	function updateAttachment(id: string, update: Partial<Attachment>) {
		setAttachments((current) =>
			current.map((item) => (item.id === id ? { ...item, ...update } : item)),
		);
	}

	/** Upload images to the Library so the next message can refer to them. */
	function attach(files: File[]) {
		const images = files.filter((file) => ATTACHABLE_TYPES.includes(file.type));
		if (images.length < files.length)
			setError(
				"Only PNG, JPEG, and GIF images can be attached to a message. Add other files to the Course from the + menu.",
			);
		for (const file of images) {
			const item: Attachment = {
				id: crypto.randomUUID(),
				name: file.name || "pasted-image.png",
				mediaType: file.type,
				previewUrl: URL.createObjectURL(file),
				resourceId: null,
				state: "uploading",
				error: null,
			};
			setAttachments((current) => [...current, item]);
			void (async () => {
				try {
					const formData = new FormData();
					formData.append("file", file, item.name);
					const response = await fetch("/api/resources/upload", {
						method: "POST",
						body: formData,
					});
					if (!response.ok) throw new Error(await responseError(response));
					const uploaded = (await response.json()) as {
						resource_id: string;
						status: string;
						error: string | null;
					};
					if (uploaded.status !== "ready")
						throw new Error(uploaded.error ?? "The image could not be read.");
					updateAttachment(item.id, {
						state: "ready",
						resourceId: uploaded.resource_id,
					});
				} catch (caught) {
					updateAttachment(item.id, {
						state: "failed",
						error: errorMessage(caught, "The image could not be attached."),
					});
				}
			})();
		}
	}

	function removeAttachment(id: string) {
		setAttachments((current) => {
			const removed = current.find((item) => item.id === id);
			if (removed) URL.revokeObjectURL(removed.previewUrl);
			return current.filter((item) => item.id !== id);
		});
	}

	const attachmentsPending = attachments.some(
		(item) => item.state === "uploading",
	);
	const readyAttachments = attachments.filter(
		(item) => item.state === "ready" && item.resourceId,
	);

	async function send() {
		const content = prompt.trim();
		if (
			(!content && readyAttachments.length === 0) ||
			attachmentsPending ||
			running ||
			conversationBusy ||
			approval ||
			!conversations ||
			!hasModel
		)
			return;
		const attached = readyAttachments.map((item) =>
			attachmentLine({
				resourceId: item.resourceId as string,
				mediaType: item.mediaType,
				name: item.name,
			}),
		);
		const prefix =
			(attached.length ? `${attached.join("\n")}\n\n` : "") +
			(context ? `[Context: ${context.instruction}]\n\n` : "");
		const userMessage: ChatMessage = {
			id: crypto.randomUUID(),
			role: "user",
			content: prefix + content,
		};
		for (const item of attachments) URL.revokeObjectURL(item.previewUrl);
		setAttachments([]);
		const mode: AgentMode = driftId ? "guided" : "autonomous";
		if (localDraft) {
			setConversationBusy(true);
			setError(null);
			try {
				const response = await requestJson("/api/conversations", "POST", {});
				const list = (await response.json()) as ConversationList;
				draftsRef.current.delete("__draft__");
				setConversations(list);
				setLocalDraft(false);
				setMessages([userMessage]);
				setPromptState("");
				setContext(null);
				void run([userMessage], { mode, threadId: list.active_id });
			} catch (caught) {
				setError(
					errorMessage(caught, "The conversation could not be started."),
				);
			} finally {
				setConversationBusy(false);
			}
			return;
		}
		draftsRef.current.delete(conversations.active_id);
		setMessages((current) => [...current, userMessage]);
		setPromptState("");
		setContext(null);
		setApproval(null);
		void run([userMessage], { mode });
	}

	function resolveApproval(approved: boolean) {
		if (!approval || running || !conversations) return;
		const pending = approval;
		setApproval(null);
		void run([], {
			resume: [
				{ interruptId: pending.id, status: "resolved", payload: { approved } },
			],
			mode: "guided",
		});
	}

	async function conversationAction(action: () => Promise<void>) {
		if (running || conversationBusy) return false;
		setConversationBusy(true);
		setError(null);
		try {
			await action();
			await refreshConversations();
			return true;
		} catch (caught) {
			setError(errorMessage(caught, "The conversation could not be changed."));
			return false;
		} finally {
			setConversationBusy(false);
		}
	}

	function restoreDraftFor(key: string) {
		setPromptState(draftsRef.current.get(key) ?? "");
	}

	const changeBlocker = running
		? "Wait for the Course Agent to finish"
		: approval
			? "Resolve the pending approval first"
			: null;

	function newConversation() {
		if (changeBlocker || conversationBusy) return;
		if (!localDraft && messages.length === 0) return;
		setLocalDraft(true);
		setMessages([]);
		setApproval(null);
		setActivities([]);
		setError(null);
		setStatus("idle");
		restoreDraftFor("__draft__");
	}

	function openConversation(id: string) {
		if (changeBlocker) return;
		void conversationAction(async () => {
			if (id !== conversations?.active_id) {
				await requestJson(
					`/api/conversations/${encodeURIComponent(id)}/activate`,
					"POST",
				);
			}
			setLocalDraft(false);
			await loadTranscript();
			restoreDraftFor(id);
		});
	}

	function renameConversation(id: string, title: string) {
		return conversationAction(async () => {
			await requestJson(
				`/api/conversations/${encodeURIComponent(id)}`,
				"PATCH",
				{
					title,
				},
			);
		});
	}

	function archiveConversation(id: string, archived: boolean) {
		return conversationAction(async () => {
			await requestJson(
				`/api/conversations/${encodeURIComponent(id)}`,
				"PATCH",
				{
					archived,
				},
			);
			if (id === conversations?.active_id && !localDraft) {
				await loadTranscript();
				const list = await refreshConversations();
				restoreDraftFor(list.active_id);
			}
		});
	}

	function deleteConversation(id: string) {
		return conversationAction(async () => {
			const wasActive = id === conversations?.active_id && !localDraft;
			await requestJson(
				`/api/conversations/${encodeURIComponent(id)}`,
				"DELETE",
			);
			draftsRef.current.delete(id);
			if (wasActive) {
				await loadTranscript();
				const list = await refreshConversations();
				restoreDraftFor(list.active_id);
			}
		});
	}

	async function previewCompaction(): Promise<CompactionPreview | null> {
		const id = conversations?.active_id;
		if (!id || changeBlocker) return null;
		let preview: CompactionPreview | null = null;
		await conversationAction(async () => {
			const response = await requestJson(
				`/api/conversations/${encodeURIComponent(id)}/compact/preview`,
				"POST",
			);
			preview = (await response.json()) as CompactionPreview;
		});
		return preview;
	}

	function confirmCompaction(summary: string, revision: string | number) {
		const id = conversations?.active_id;
		if (!id) return Promise.resolve(false);
		return conversationAction(async () => {
			await requestJson(
				`/api/conversations/${encodeURIComponent(id)}/compact`,
				"POST",
				{ summary, revision },
			);
			await loadTranscript();
		});
	}

	/** Reset after the Workspace changes; the owning component remounts us per Workspace. */
	const activeConversation =
		localDraft || !conversations
			? null
			: (conversations.conversations.find(
					(conversation) => conversation.id === conversations.active_id,
				) ?? null);

	return {
		loaded,
		conversations,
		activeConversation,
		localDraft,
		messages,
		approval,
		activities,
		status,
		running,
		error,
		clearError: () => setError(null),
		results,
		prompt,
		setPrompt,
		context,
		setContext,
		attachments,
		attachmentsPending,
		readyAttachments,
		attach,
		removeAttachment,
		driftId,
		setDriftId,
		hasModel,
		conversationBusy,
		changeBlocker,
		send,
		stop,
		resolveApproval,
		newConversation,
		openConversation,
		renameConversation,
		archiveConversation,
		deleteConversation,
		previewCompaction,
		confirmCompaction,
	};
}

export type CourseAgent = ReturnType<typeof useCourseAgent>;

function summarize(activities: Activity[], stopped: boolean): RunResult {
	const presentationIds = new Set<string>();
	const deletedPresentationIds = new Set<string>();
	let planChanged = false;
	let sourcesAdded = 0;
	for (const activity of activities) {
		if (activity.id.startsWith("course-plan-")) planChanged = true;
		else if (activity.id.startsWith("presentation-delete-"))
			deletedPresentationIds.add(
				activity.id.slice("presentation-delete-".length),
			);
		else if (activity.id.startsWith("presentation-"))
			presentationIds.add(activity.id.slice("presentation-".length));
		else if (activity.id.startsWith("sources-admit-")) sourcesAdded += 1;
	}
	return {
		planChanged,
		presentationIds: [...presentationIds],
		deletedPresentationIds: [...deletedPresentationIds],
		sourcesAdded,
		stopped,
	};
}

function hasChanges(result: RunResult): boolean {
	return (
		result.planChanged ||
		result.presentationIds.length > 0 ||
		result.deletedPresentationIds.length > 0 ||
		result.sourcesAdded > 0
	);
}
