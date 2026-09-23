import {
	ArrowDown,
	ArrowUp,
	Bot,
	Check,
	ChevronDown,
	FilePlus2,
	ImagePlus,
	Info,
	LibraryBig,
	Loader2,
	Menu as MenuIcon,
	MoreHorizontal,
	PanelRight,
	Pencil,
	Plus,
	Search,
	Settings2,
	Shrink,
	Square,
	Trash2,
	X,
} from "lucide-react";
import {
	type RefObject,
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
} from "react";

import type { CanvasTarget } from "../App";
import { responseError } from "../api";
import type {
	CoursePlan,
	ModelCatalog,
	PresentationSummary,
	Source,
} from "../models";
import { Menu, Notice } from "../ui";
import {
	ATTACHABLE_TYPES,
	type AgentContext,
	type CourseAgent,
	messageParts,
	type RunResult,
} from "../useCourseAgent";
import { type Library, MATERIAL_ACCEPT } from "../useLibrary";
import { ApprovalCard } from "./ApprovalCard";
import type { ConversationDialog } from "./ConversationDialogs";
import { Markdown } from "./Markdown";
import { StartSurface } from "./StartSurface";

type Props = {
	agent: CourseAgent;
	catalog: ModelCatalog;
	course: CoursePlan | null;
	sources: Source[];
	library: Library;
	presentations: PresentationSummary[];
	lectureNumber: Map<string, number>;
	canvasOpen: boolean;
	focusContext: AgentContext | null;
	composerRef: RefObject<HTMLTextAreaElement | null>;
	onCatalogChange: (catalog: ModelCatalog) => void;
	onAddModel: () => void;
	onOpenNav: () => void;
	onShowCanvas: () => void;
	onOpen: (target: CanvasTarget) => void;
	onConversationDialog: (dialog: ConversationDialog) => void;
};

const statusAnnouncement: Record<CourseAgent["status"], string> = {
	idle: "",
	running: "Course Agent is working.",
	stopping: "Stopping the Course Agent.",
	stopped: "Course Agent stopped.",
	failed: "The Course Agent run failed.",
	"awaiting-approval": "The Course Agent is waiting for your approval.",
};

export function ConversationPane({
	agent,
	catalog,
	course,
	sources,
	library,
	presentations,
	lectureNumber,
	canvasOpen,
	focusContext,
	composerRef,
	onCatalogChange,
	onAddModel,
	onOpenNav,
	onShowCanvas,
	onOpen,
	onConversationDialog,
}: Props) {
	const scrollRef = useRef<HTMLDivElement>(null);
	const stickRef = useRef(true);
	const [showJump, setShowJump] = useState(false);
	const [lastAnnounced, setLastAnnounced] = useState("");
	const conversation = agent.activeConversation;
	const title = conversation?.title ?? "New conversation";

	const lastContent = agent.messages.at(-1)?.content;
	const contentSignal = `${agent.messages.length}:${lastContent?.length ?? 0}:${agent.activities.length}:${agent.approval?.id ?? ""}:${agent.status}`;

	// biome-ignore lint/correctness/useExhaustiveDependencies: contentSignal represents transcript growth
	useLayoutEffect(() => {
		const element = scrollRef.current;
		if (!element) return;
		if (stickRef.current) {
			element.scrollTop = element.scrollHeight;
			setShowJump(false);
		} else {
			setShowJump(true);
		}
	}, [contentSignal]);

	useEffect(() => {
		const conversationId = agent.conversations?.active_id;
		void conversationId;
		stickRef.current = true;
		const element = scrollRef.current;
		if (element) element.scrollTop = element.scrollHeight;
	}, [agent.conversations?.active_id]);

	useEffect(() => {
		const next = statusAnnouncement[agent.status];
		if (next) setLastAnnounced(next);
		else if (
			agent.status === "idle" &&
			lastAnnounced === statusAnnouncement.running
		)
			setLastAnnounced("Course Agent finished.");
	}, [agent.status, lastAnnounced]);

	function onScroll() {
		const element = scrollRef.current;
		if (!element) return;
		const nearBottom =
			element.scrollHeight - element.scrollTop - element.clientHeight < 48;
		stickRef.current = nearBottom;
		if (nearBottom) setShowJump(false);
	}

	function jumpToLatest() {
		const element = scrollRef.current;
		if (!element) return;
		stickRef.current = true;
		element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
		setShowJump(false);
	}

	const lastAssistantId = agent.messages.findLast(
		(message) => message.role === "assistant",
	)?.id;

	return (
		<section className="conversation" aria-label="Conversation">
			<header className="conversation-bar">
				<button
					type="button"
					className="icon-btn nav-toggle"
					aria-label="Open navigation"
					onClick={onOpenNav}
				>
					<MenuIcon aria-hidden="true" />
				</button>
				<h1 className="conversation-title" title={title}>
					{title}
				</h1>
				{agent.running ? (
					<span className="state is-working">
						<Loader2 className="spin" aria-hidden="true" />
						{agent.status === "stopping" ? "Stopping…" : "Working…"}
					</span>
				) : null}
				<div className="conversation-bar-actions">
					{conversation ? (
						<Menu
							label="Conversation actions"
							trigger={<MoreHorizontal aria-hidden="true" />}
							items={[
								{
									label: "Rename",
									icon: <Pencil aria-hidden="true" />,
									disabled: agent.running,
									onSelect: () =>
										onConversationDialog({ kind: "rename", conversation }),
								},
								{
									label: "Compact conversation",
									detail: "Replace earlier messages with a summary",
									icon: <Shrink aria-hidden="true" />,
									disabled:
										Boolean(agent.changeBlocker) || agent.messages.length === 0,
									title: agent.changeBlocker ?? undefined,
									onSelect: () => onConversationDialog({ kind: "compact" }),
								},
								"separator",
								{
									label: "Delete",
									icon: <Trash2 aria-hidden="true" />,
									danger: true,
									disabled:
										Boolean(agent.changeBlocker) ||
										conversation.has_pending_approval,
									title: agent.changeBlocker ?? undefined,
									onSelect: () =>
										onConversationDialog({ kind: "delete", conversation }),
								},
							]}
						/>
					) : null}
					{canvasOpen ? (
						<button
							type="button"
							className="icon-btn narrow-only"
							aria-label="Show canvas"
							title="Show canvas"
							onClick={onShowCanvas}
						>
							<PanelRight aria-hidden="true" />
						</button>
					) : null}
				</div>
			</header>

			<p
				className="visually-hidden"
				role="status"
				aria-live="polite"
				id="agent-run-status"
			>
				{lastAnnounced}
			</p>

			<section
				className="transcript"
				ref={scrollRef}
				onScroll={onScroll}
				// biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable transcript must be keyboard reachable
				tabIndex={0}
				aria-label="Course Agent conversation"
			>
				<div className="transcript-inner">
					{agent.messages.length === 0 && agent.loaded ? (
						course ? (
							<EmptyConversation
								course={course}
								presentations={presentations}
								sources={sources}
								onPick={(text) => {
									agent.setPrompt(text);
									composerRef.current?.focus();
								}}
							/>
						) : (
							<StartSurface
								sources={sources}
								library={library}
								onOpen={onOpen}
							/>
						)
					) : (
						<ol className="messages">
							{agent.messages.map((message) => {
								const parts = messageParts(message.content);
								const result = agent.results[message.id];
								const isStreaming =
									agent.running &&
									message.id === lastAssistantId &&
									message === agent.messages.at(-1);
								return (
									<li key={message.id} className={`message is-${message.role}`}>
										{message.role === "user" ? (
											<>
												{parts.attachments.length ? (
													<ul className="message-attachments">
														{parts.attachments.map((attachment) => (
															<li key={attachment.resourceId}>
																<img
																	src={`/api/resources/${encodeURIComponent(attachment.resourceId)}/content`}
																	alt={attachment.name}
																	title={attachment.name}
																	loading="lazy"
																/>
															</li>
														))}
													</ul>
												) : null}
												{parts.context ? (
													<span
														className="message-context"
														title={parts.context}
													>
														{shortContext(parts.context)}
													</span>
												) : null}
												{parts.body ? (
													<div className="message-bubble">{parts.body}</div>
												) : null}
											</>
										) : (
											<>
												<span className="message-author">
													<Bot aria-hidden="true" />
													Course Agent
												</span>
												<Markdown text={parts.body} />
												{result && !isStreaming ? (
													<RunResults
														result={result}
														course={course}
														presentations={presentations}
														lectureNumber={lectureNumber}
														onOpen={onOpen}
													/>
												) : null}
											</>
										)}
									</li>
								);
							})}
							{agent.running || agent.activities.length > 0 ? (
								<li className="message is-activity">
									<ActivityTrail agent={agent} />
								</li>
							) : null}
							{agent.status === "stopped" ? (
								<li className="message is-note">
									Stopped. Changes the agent saved before you stopped it are
									kept — review them in{" "}
									<button
										type="button"
										className="link-btn"
										onClick={() => onOpen({ kind: "history", tab: "changes" })}
									>
										History
									</button>
									.
								</li>
							) : null}
						</ol>
					)}
				</div>
			</section>

			<div className="conversation-foot">
				{showJump ? (
					<button type="button" className="jump-latest" onClick={jumpToLatest}>
						<ArrowDown aria-hidden="true" />
						Jump to latest
					</button>
				) : null}
				{agent.approval ? (
					<ApprovalCard
						approval={agent.approval}
						course={course}
						disabled={agent.running}
						onResolve={agent.resolveApproval}
					/>
				) : null}
				{agent.error ? (
					<Notice
						tone="error"
						actions={
							<button
								type="button"
								className="icon-btn is-small"
								aria-label="Dismiss"
								onClick={agent.clearError}
							>
								<X aria-hidden="true" />
							</button>
						}
					>
						{agent.error}
					</Notice>
				) : null}
				<Composer
					agent={agent}
					catalog={catalog}
					course={course}
					library={library}
					focusContext={focusContext}
					composerRef={composerRef}
					onCatalogChange={onCatalogChange}
					onAddModel={onAddModel}
					onOpen={onOpen}
				/>
			</div>
		</section>
	);
}

function shortContext(context: string): string {
	return context.length > 72 ? `${context.slice(0, 70)}…` : context;
}

function ActivityTrail({ agent }: { agent: CourseAgent }) {
	const [expanded, setExpanded] = useState(false);
	const steps = agent.activities;
	const latest = steps.at(-1);
	if (!agent.running && steps.length === 0) return null;
	return (
		<div className="activity">
			<button
				type="button"
				className="activity-summary"
				aria-expanded={expanded}
				onClick={() => setExpanded((value) => !value)}
				disabled={steps.length === 0}
			>
				{agent.running ? (
					<Loader2 className="spin" aria-hidden="true" />
				) : (
					<Check aria-hidden="true" />
				)}
				<span>
					{agent.running
						? (latest?.title ?? "Reading your request…")
						: `${steps.length} step${steps.length === 1 ? "" : "s"}`}
				</span>
				{steps.length > 0 ? <ChevronDown aria-hidden="true" /> : null}
			</button>
			{expanded ? (
				<ol className="activity-steps">
					{steps.map((step, index) => (
						// biome-ignore lint/suspicious/noArrayIndexKey: an activity can repeat within a run
						<li key={`${step.id}-${index}`}>
							<strong>{step.title}</strong>
							<span>{step.detail}</span>
						</li>
					))}
				</ol>
			) : null}
		</div>
	);
}

function RunResults({
	result,
	course,
	presentations,
	lectureNumber,
	onOpen,
}: {
	result: RunResult;
	course: CoursePlan | null;
	presentations: PresentationSummary[];
	lectureNumber: Map<string, number>;
	onOpen: (target: CanvasTarget) => void;
}) {
	const lectures = result.presentationIds
		.map((id) => presentations.find((presentation) => presentation.id === id))
		.filter((presentation): presentation is PresentationSummary =>
			Boolean(presentation),
		)
		.map((presentation) => ({
			presentation,
			lecture: course?.lectures.find(
				(lecture) => lecture.id === presentation.lecture_id,
			),
		}));
	return (
		<ul className="results" aria-label="Changes from this reply">
			{result.planChanged && course ? (
				<li>
					<button type="button" onClick={() => onOpen({ kind: "plan" })}>
						<span className="result-kind">Course Plan</span>
						<span className="result-copy">
							{course.lectures.length} Lectures · Open
						</span>
					</button>
				</li>
			) : null}
			{lectures.map(({ presentation, lecture }) =>
				lecture ? (
					<li key={presentation.id}>
						<button
							type="button"
							onClick={() => onOpen({ kind: "lecture", lectureId: lecture.id })}
						>
							<span className="result-kind">
								Lecture {lectureNumber.get(lecture.id)} · {lecture.title}
							</span>
							<span className="result-copy">
								{presentation.slide_count} Slides · Open
							</span>
						</button>
					</li>
				) : null,
			)}
			{result.deletedPresentationIds.length > 0 ? (
				<li className="is-static">
					<span className="result-kind">
						{result.deletedPresentationIds.length} Presentation
						{result.deletedPresentationIds.length === 1 ? "" : "s"} deleted
					</span>
				</li>
			) : null}
			{result.sourcesAdded > 0 ? (
				<li>
					<button type="button" onClick={() => onOpen({ kind: "sources" })}>
						<span className="result-kind">
							{result.sourcesAdded} Source{result.sourcesAdded === 1 ? "" : "s"}{" "}
							added
						</span>
						<span className="result-copy">Open</span>
					</button>
				</li>
			) : null}
		</ul>
	);
}

function EmptyConversation({
	course,
	presentations,
	sources,
	onPick,
}: {
	course: CoursePlan;
	presentations: PresentationSummary[];
	sources: Source[];
	onPick: (text: string) => void;
}) {
	const withSlides = new Set(presentations.map((item) => item.lecture_id));
	const nextLecture = course.lectures.find(
		(lecture) => !withSlides.has(lecture.id),
	);
	const suggestions = [
		nextLecture ? `Create Slides for “${nextLecture.title}”` : null,
		"Review the lecture sequence and suggest improvements",
		sources.length > 0
			? "Which parts of the plan are not covered by the Sources yet?"
			: "Suggest papers I should add as Sources",
	].filter((item): item is string => Boolean(item));
	return (
		<div className="empty-conversation">
			<h2 className="display-title">What should we work on?</h2>
			<ul className="suggestions">
				{suggestions.map((suggestion) => (
					<li key={suggestion}>
						<button type="button" onClick={() => onPick(suggestion)}>
							{suggestion}
						</button>
					</li>
				))}
			</ul>
		</div>
	);
}

function Composer({
	agent,
	catalog,
	course,
	library,
	focusContext,
	composerRef,
	onCatalogChange,
	onAddModel,
	onOpen,
}: {
	agent: CourseAgent;
	catalog: ModelCatalog;
	course: CoursePlan | null;
	library: Library;
	focusContext: AgentContext | null;
	composerRef: RefObject<HTMLTextAreaElement | null>;
	onCatalogChange: (catalog: ModelCatalog) => void;
	onAddModel: () => void;
	onOpen: (target: CanvasTarget) => void;
}) {
	const fileInputRef = useRef<HTMLInputElement>(null);
	const imageInputRef = useRef<HTMLInputElement>(null);
	const [dragging, setDragging] = useState(false);
	const [disclosureOpen, setDisclosureOpen] = useState(false);
	const [modelError, setModelError] = useState<string | null>(null);
	const selected = catalog.model_presets.find(
		(preset) => preset.id === catalog.selected_model_id,
	);
	const account = catalog.provider_accounts.find(
		(item) => item.id === selected?.provider_account_id,
	);

	const resize = useCallback(() => {
		const element = composerRef.current;
		if (!element) return;
		element.style.height = "auto";
		element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
	}, [composerRef]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: prompt changes drive height
	useLayoutEffect(resize, [agent.prompt, resize]);

	async function selectModel(modelId: string) {
		setModelError(null);
		const response = await fetch("/api/models/selected", {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ model_id: modelId }),
		});
		if (!response.ok) {
			setModelError(await responseError(response));
			return;
		}
		onCatalogChange((await response.json()) as ModelCatalog);
	}

	const inputDisabled = agent.conversationBusy || !agent.loaded;
	const sendBlocked =
		agent.running ||
		agent.conversationBusy ||
		agent.approval !== null ||
		!agent.hasModel ||
		agent.attachmentsPending ||
		(!agent.prompt.trim() && agent.readyAttachments.length === 0);
	const offerFocus =
		focusContext &&
		agent.prompt.trim() !== "" &&
		focusContext.key !== agent.context?.key;

	return (
		<form
			className={`composer${dragging ? " is-dropping" : ""}`}
			onSubmit={(event) => {
				event.preventDefault();
				void agent.send();
			}}
			onDragOver={(event) => {
				if (!event.dataTransfer.types.includes("Files") || inputDisabled)
					return;
				event.preventDefault();
				setDragging(true);
			}}
			onDragLeave={(event) => {
				if (!event.currentTarget.contains(event.relatedTarget as Node | null))
					setDragging(false);
			}}
			onDrop={(event) => {
				if (!event.dataTransfer.files.length || inputDisabled) return;
				event.preventDefault();
				setDragging(false);
				agent.attach(Array.from(event.dataTransfer.files));
			}}
			aria-busy={agent.running}
		>
			{agent.attachments.length ? (
				<ul className="composer-attachments" aria-label="Attached images">
					{agent.attachments.map((attachment) => (
						<li
							key={attachment.id}
							className={`attachment is-${attachment.state}`}
							title={attachment.error ?? attachment.name}
						>
							<img src={attachment.previewUrl} alt={attachment.name} />
							{attachment.state === "uploading" ? (
								<span className="attachment-status">
									<Loader2 className="spin" aria-hidden="true" />
									<span className="visually-hidden">Attaching</span>
								</span>
							) : null}
							{attachment.state === "failed" ? (
								<span className="attachment-status" role="alert">
									{attachment.error}
								</span>
							) : null}
							<button
								type="button"
								aria-label={`Remove ${attachment.name}`}
								onClick={() => agent.removeAttachment(attachment.id)}
							>
								<X aria-hidden="true" />
							</button>
						</li>
					))}
				</ul>
			) : null}
			{agent.context || offerFocus ? (
				<div className="composer-context">
					{agent.context ? (
						<span className="context-chip" title={agent.context.instruction}>
							<span>{agent.context.label}</span>
							<button
								type="button"
								aria-label={`Remove context: ${agent.context.label}`}
								onClick={() => {
									agent.setContext(null);
									if (agent.driftId) agent.setDriftId(null);
								}}
							>
								<X aria-hidden="true" />
							</button>
						</span>
					) : null}
					{offerFocus && focusContext ? (
						<button
							type="button"
							className="context-offer"
							onClick={() => agent.setContext(focusContext)}
						>
							<Plus aria-hidden="true" />
							Use {focusContext.label}
						</button>
					) : null}
				</div>
			) : null}
			<label htmlFor="course-agent-message" className="visually-hidden">
				Message the Course Agent
			</label>
			<textarea
				id="course-agent-message"
				ref={composerRef}
				rows={2}
				value={agent.prompt}
				maxLength={4000}
				disabled={inputDisabled}
				placeholder={
					course
						? agent.context
							? `Ask about ${agent.context.label}…`
							: "Ask for a change, a review, or new Slides…"
						: "What should this Course help people learn?"
				}
				onChange={(event) => agent.setPrompt(event.target.value)}
				onPaste={(event) => {
					const files = Array.from(event.clipboardData.files).filter((file) =>
						ATTACHABLE_TYPES.includes(file.type),
					);
					if (!files.length) return;
					event.preventDefault();
					agent.attach(files);
				}}
				onKeyDown={(event) => {
					if (
						event.key === "Enter" &&
						!event.shiftKey &&
						!event.nativeEvent.isComposing
					) {
						event.preventDefault();
						if (!sendBlocked) void agent.send();
					}
				}}
			/>
			<div className="composer-actions">
				<Menu
					label="Add material"
					direction="up"
					align="start"
					trigger={<Plus aria-hidden="true" />}
					items={[
						{
							label: "Attach an image to this message",
							icon: <ImagePlus aria-hidden="true" />,
							onSelect: () => imageInputRef.current?.click(),
						},
						{
							label: "Add files to this course",
							icon: <FilePlus2 aria-hidden="true" />,
							onSelect: () => fileInputRef.current?.click(),
						},
						{
							label: "Discover papers",
							icon: <Search aria-hidden="true" />,
							onSelect: () => onOpen({ kind: "sources", tab: "discover" }),
						},
						{
							label: "Choose from Library",
							icon: <LibraryBig aria-hidden="true" />,
							onSelect: () => onOpen({ kind: "sources", tab: "library" }),
						},
					]}
				/>
				<input
					ref={imageInputRef}
					type="file"
					multiple
					hidden
					accept={ATTACHABLE_TYPES.join(",")}
					aria-label="Attach an image to this message"
					onChange={(event) => {
						const files = Array.from(event.target.files ?? []);
						event.target.value = "";
						agent.attach(files);
					}}
				/>
				<input
					ref={fileInputRef}
					type="file"
					multiple
					hidden
					accept={MATERIAL_ACCEPT}
					aria-label="Add files to this course"
					onChange={(event) => {
						const files = Array.from(event.target.files ?? []);
						event.target.value = "";
						void library.addFiles(files, true);
					}}
				/>
				{selected ? (
					<Menu
						label={`Model: ${selected.name}`}
						direction="up"
						align="start"
						triggerClassName="model-chip"
						disabled={agent.running}
						trigger={
							<>
								<span>{selected.name}</span>
								<ChevronDown aria-hidden="true" />
							</>
						}
						items={[
							...catalog.model_presets.map((preset) => ({
								label: preset.name,
								detail: preset.model,
								icon:
									preset.id === selected.id ? (
										<Check aria-hidden="true" />
									) : (
										<span className="menu-icon-space" />
									),
								onSelect: () => void selectModel(preset.id),
							})),
							"separator" as const,
							{
								label: "Add model…",
								icon: <Plus aria-hidden="true" />,
								onSelect: onAddModel,
							},
						]}
					/>
				) : (
					<button
						type="button"
						className="model-chip is-missing"
						onClick={onAddModel}
					>
						<Settings2 aria-hidden="true" />
						<span>Add a model to chat</span>
					</button>
				)}
				{selected && account ? (
					<button
						type="button"
						className="icon-btn is-small"
						aria-label="Where messages are sent"
						title="Where messages are sent"
						aria-expanded={disclosureOpen}
						onClick={() => setDisclosureOpen((value) => !value)}
					>
						<Info aria-hidden="true" />
					</button>
				) : null}
				<div className="composer-spacer" />
				{agent.running ? (
					<button
						type="button"
						className="send-btn is-stop"
						aria-label="Stop response"
						title="Stop response"
						disabled={agent.status === "stopping"}
						onClick={() => void agent.stop()}
					>
						<Square aria-hidden="true" />
					</button>
				) : (
					<button
						type="submit"
						className="send-btn"
						aria-label="Send message"
						title={
							!agent.hasModel
								? "Add a model to send messages"
								: agent.approval
									? "Resolve the pending approval first"
									: "Send message"
						}
						disabled={sendBlocked}
					>
						<ArrowUp aria-hidden="true" />
					</button>
				)}
			</div>
			{disclosureOpen && selected && account ? (
				<p className="composer-disclosure" role="note">
					When you send a message, it, any Source excerpts, and any images the
					Course Agent looks at for the reply leave this device for{" "}
					<strong>{account.name}</strong> at <strong>{account.base_url}</strong>{" "}
					using {selected.model}. The API key stays in Course Harness&apos;s
					private credential store.
				</p>
			) : null}
			{modelError ? <p className="field-error">{modelError}</p> : null}
		</form>
	);
}
