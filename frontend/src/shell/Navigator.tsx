import {
	Archive,
	ArchiveRestore,
	ChevronsUpDown,
	FolderCog,
	History,
	LibraryBig,
	ListTree,
	LogOut,
	MessageSquarePlus,
	MoreHorizontal,
	Pencil,
	Settings,
	Trash2,
	X,
} from "lucide-react";
import { useState } from "react";

import type { CanvasTarget } from "../App";
import type { ConversationDialog } from "../chat/ConversationDialogs";
import type {
	CoursePlan,
	PresentationSummary,
	Source,
	Workspace,
} from "../models";
import { Menu } from "../ui";
import type { CourseAgent } from "../useCourseAgent";

export function Navigator({
	workspace,
	course,
	sources,
	presentations,
	canvas,
	agent,
	changesCount,
	onOpen,
	onClose,
	onSettings,
	onWorkspaceDetails,
	onConversationDialog,
	onAllCourses,
}: {
	workspace: Workspace;
	course: CoursePlan | null;
	sources: Source[];
	presentations: PresentationSummary[];
	canvas: CanvasTarget | null;
	agent: CourseAgent;
	changesCount: number;
	onOpen: (target: CanvasTarget) => void;
	onClose: () => void;
	onSettings: () => void;
	onWorkspaceDetails: () => void;
	onConversationDialog: (dialog: ConversationDialog) => void;
	onAllCourses: () => Promise<void>;
}) {
	const [archivedRequested, setShowArchived] = useState(false);
	const slideCount = new Map(
		presentations.map((presentation) => [
			presentation.lecture_id,
			presentation.slide_count,
		]),
	);
	const conversationList = agent.conversations?.conversations ?? [];
	const archivedCount = conversationList.filter((item) => item.archived).length;
	// Fall back to the normal list once nothing is archived any more.
	const showArchived = archivedRequested && archivedCount > 0;
	const visibleConversations = conversationList.filter(
		(item) => item.archived === showArchived,
	);
	const activeId = agent.localDraft ? null : agent.conversations?.active_id;
	const current = (kind: CanvasTarget["kind"], lectureId?: string) =>
		canvas?.kind === kind &&
		(lectureId === undefined ||
			(canvas.kind === "lecture" && canvas.lectureId === lectureId))
			? "page"
			: undefined;

	return (
		<nav className="navigator" aria-label="Course">
			<div className="nav-top">
				<Menu
					label="Course menu"
					triggerClassName="course-switcher"
					align="start"
					trigger={
						<>
							<span className="course-switcher-copy">
								<strong>{course?.title ?? workspace.name}</strong>
								<small>{course ? workspace.name : "New course"}</small>
							</span>
							<ChevronsUpDown aria-hidden="true" />
						</>
					}
					items={[
						{
							label: "Workspace details",
							icon: <FolderCog aria-hidden="true" />,
							detail: workspace.path,
							onSelect: onWorkspaceDetails,
						},
						"separator",
						{
							label: "Open another course",
							icon: <LogOut aria-hidden="true" />,
							disabled: agent.running,
							title: agent.running
								? "Wait for the Course Agent to finish"
								: undefined,
							onSelect: () => void onAllCourses(),
						},
					]}
				/>
				<button
					type="button"
					className="icon-btn nav-close"
					aria-label="Close navigation"
					onClick={onClose}
				>
					<X aria-hidden="true" />
				</button>
			</div>

			<div className="nav-scroll">
				<ul className="nav-list">
					<li>
						<button
							type="button"
							className="nav-item"
							aria-current={current("plan")}
							onClick={() => onOpen({ kind: "plan" })}
						>
							<ListTree aria-hidden="true" />
							<span className="nav-label">Course Plan</span>
						</button>
					</li>
					<li>
						<button
							type="button"
							className="nav-item"
							aria-current={current("sources") ?? current("reader")}
							onClick={() => onOpen({ kind: "sources" })}
						>
							<LibraryBig aria-hidden="true" />
							<span className="nav-label">Sources</span>
							{sources.length > 0 ? (
								<span className="nav-count">{sources.length}</span>
							) : null}
						</button>
					</li>
				</ul>

				{course ? (
					<section className="nav-section" aria-labelledby="nav-lectures">
						<h2 id="nav-lectures" className="nav-heading">
							Lectures
						</h2>
						<ol className="nav-list lecture-sequence">
							{course.lectures.map((lecture, index) => {
								const slides = slideCount.get(lecture.id);
								return (
									<li key={lecture.id}>
										<button
											type="button"
											className="nav-item lecture-item"
											aria-current={current("lecture", lecture.id)}
											onClick={() =>
												onOpen({ kind: "lecture", lectureId: lecture.id })
											}
										>
											<span className="lecture-number" aria-hidden="true">
												{index + 1}
											</span>
											<span className="nav-label">{lecture.title}</span>
											<span
												className="lecture-slides"
												title={
													slides ? `${slides} Slides` : "No Presentation yet"
												}
											>
												{slides ? (
													slides
												) : (
													<span className="visually-hidden">
														No Presentation yet
													</span>
												)}
											</span>
										</button>
									</li>
								);
							})}
						</ol>
					</section>
				) : null}

				<section className="nav-section" aria-labelledby="nav-conversations">
					<div className="nav-heading-row">
						<h2 id="nav-conversations" className="nav-heading">
							{showArchived ? "Archived conversations" : "Conversations"}
						</h2>
						<button
							type="button"
							className="icon-btn is-small"
							aria-label="New conversation"
							title={agent.changeBlocker ?? "New conversation"}
							disabled={
								Boolean(agent.changeBlocker) ||
								agent.conversationBusy ||
								(agent.messages.length === 0 && !showArchived)
							}
							onClick={() => {
								setShowArchived(false);
								agent.newConversation();
							}}
						>
							<MessageSquarePlus aria-hidden="true" />
						</button>
					</div>
					<ul className="nav-list conversation-list" aria-label="Conversations">
						{agent.localDraft && !showArchived ? (
							<li>
								<span
									className="nav-item conversation-item"
									aria-current="true"
								>
									<span className="nav-label">New conversation</span>
								</span>
							</li>
						) : null}
						{visibleConversations.map((conversation) => {
							const isActive = conversation.id === activeId;
							return (
								<li key={conversation.id} className="conversation-row">
									<button
										type="button"
										className="nav-item conversation-item"
										aria-current={isActive ? "true" : undefined}
										disabled={
											conversation.archived ||
											(Boolean(agent.changeBlocker) && !isActive)
										}
										title={
											conversation.archived
												? "Restore this conversation to open it"
												: !isActive && agent.changeBlocker
													? agent.changeBlocker
													: conversation.title
										}
										onClick={() => {
											if (!isActive) agent.openConversation(conversation.id);
											onClose();
										}}
									>
										<span className="nav-label">{conversation.title}</span>
										{conversation.has_pending_approval ? (
											<span
												className="approval-dot"
												title="Waiting for your approval"
											>
												<span className="visually-hidden">
													Waiting for your approval
												</span>
											</span>
										) : null}
									</button>
									<Menu
										label={`Actions for ${conversation.title}`}
										triggerClassName="icon-btn is-small row-menu"
										trigger={<MoreHorizontal aria-hidden="true" />}
										items={[
											{
												label: "Rename",
												icon: <Pencil aria-hidden="true" />,
												disabled: agent.running,
												onSelect: () =>
													onConversationDialog({
														kind: "rename",
														conversation,
													}),
											},
											conversation.archived
												? {
														label: "Restore",
														icon: <ArchiveRestore aria-hidden="true" />,
														disabled: agent.running,
														onSelect: () =>
															void agent.archiveConversation(
																conversation.id,
																false,
															),
													}
												: {
														label: "Archive",
														icon: <Archive aria-hidden="true" />,
														disabled:
															agent.running ||
															conversation.has_pending_approval ||
															(isActive && Boolean(agent.approval)),
														title: conversation.has_pending_approval
															? "Resolve the pending approval first"
															: undefined,
														onSelect: () =>
															void agent.archiveConversation(
																conversation.id,
																true,
															),
													},
											"separator",
											{
												label: "Delete",
												icon: <Trash2 aria-hidden="true" />,
												danger: true,
												disabled:
													agent.running ||
													conversation.has_pending_approval ||
													(isActive && Boolean(agent.approval)),
												title: conversation.has_pending_approval
													? "Resolve the pending approval first"
													: undefined,
												onSelect: () =>
													onConversationDialog({
														kind: "delete",
														conversation,
													}),
											},
										]}
									/>
								</li>
							);
						})}
					</ul>
					{archivedCount > 0 ? (
						<button
							type="button"
							className="nav-filter"
							aria-pressed={showArchived}
							onClick={() => setShowArchived((value) => !value)}
						>
							{showArchived
								? "Back to conversations"
								: `Archived (${archivedCount})`}
						</button>
					) : null}
				</section>
			</div>

			<div className="nav-bottom">
				<button
					type="button"
					className="nav-item"
					aria-current={current("history")}
					onClick={() => onOpen({ kind: "history" })}
				>
					<History aria-hidden="true" />
					<span className="nav-label">History</span>
					{changesCount > 0 ? (
						<span
							className="nav-count is-attention"
							title={`${changesCount} changes not yet saved as a Course Revision`}
						>
							{changesCount}
							<span className="visually-hidden">
								{" "}
								changes since the last revision
							</span>
						</span>
					) : null}
				</button>
				<button type="button" className="nav-item" onClick={onSettings}>
					<Settings aria-hidden="true" />
					<span className="nav-label">Settings</span>
				</button>
			</div>
		</nav>
	);
}
