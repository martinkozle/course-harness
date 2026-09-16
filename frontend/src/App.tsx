import {
	type FormEvent,
	useCallback,
	useEffect,
	useLayoutEffect,
	useState,
} from "react";

import { AgentPanel, type ChatMessage, type CoursePlan } from "./AgentPanel";
import type { AgentInterrupt } from "./agentStream";
import { responseError } from "./api";
import { CurrentStateView } from "./CurrentStateView";
import { LibraryView } from "./LibraryView";
import type {
	EvidenceTarget,
	ModelCatalog,
	PresentationSummary,
	ResourceState,
	Source,
	TemplateProfileSummary,
} from "./models";
import { PresentationView } from "./PresentationView";
import { ModelsView } from "./ProviderSetup";
import { ReleasesView } from "./ReleasesView";
import { ResizableSplit } from "./ResizableSplit";
import { RuntimeDiagnosticsDialog } from "./RuntimeDiagnostics";
import { TemplatesView } from "./TemplatesView";

type Workspace = {
	name: string;
	path: string;
};

type RecentWorkspace = Workspace & {
	id: string;
};

type Lecture = {
	id: string;
	title: string;
	group: string | null;
};

type LectureDraft = Pick<Lecture, "id" | "title">;

type WorkspaceEntry = {
	path: string;
	kind: "file" | "directory";
};

type CourseRequest = {
	title: string;
	audience: string;
	goals: string[];
	outcomes: string[];
	lectures: { title: string }[];
};

type WorkspaceView =
	| "course"
	| "author"
	| "current-state"
	| "files"
	| "models"
	| "library"
	| "releases"
	| "templates";

type SectionLink = {
	id: WorkspaceView;
	label: string;
};

function splitLines(value: string): string[] {
	return value
		.split("\n")
		.map((line) => line.trim())
		.filter(Boolean);
}

function scrollToTop() {
	window.scrollTo({ top: 0, left: 0 });
}

function Brand() {
	return (
		<div className="wordmark">
			<span className="wordmark-glyph" aria-hidden="true">
				CH
			</span>
			<span>Course Harness</span>
		</div>
	);
}

type LauncherProps = {
	recent: RecentWorkspace[];
	busy: boolean;
	error: string | null;
	onNew: () => Promise<void>;
	onOpen: () => Promise<void>;
	onOpenRecent: (identity: string) => Promise<void>;
};

function Launcher({
	recent,
	busy,
	error,
	onNew,
	onOpen,
	onOpenRecent,
}: LauncherProps) {
	return (
		<main className="launcher-main">
			<section
				className="launcher"
				aria-labelledby="launcher-title"
				aria-busy={busy}
			>
				<div className="launcher-heading">
					<div>
						<p className="eyebrow">Courses</p>
						<h1 id="launcher-title">Your courses</h1>
						<p className="lede">
							Start something new or return to a Course already on your
							computer.
						</p>
					</div>

					<div className="launcher-actions">
						<button
							className="primary-action"
							type="button"
							onClick={onNew}
							disabled={busy}
						>
							New course
						</button>
						<button
							className="secondary-action"
							type="button"
							onClick={onOpen}
							disabled={busy}
						>
							Open existing
						</button>
						<p>Your operating system will ask you to choose a folder.</p>
					</div>
				</div>

				{error ? (
					<p className="notice error-notice" role="alert">
						{error}
					</p>
				) : null}

				<section className="recent-section" aria-labelledby="recent-heading">
					<div className="section-heading">
						<div>
							<p className="section-kicker">On this computer</p>
							<h2 id="recent-heading">Recent courses</h2>
						</div>
						<span className="count-badge">{recent.length}</span>
					</div>
					{recent.length === 0 ? (
						<div className="empty-state">
							<p>No recent courses yet.</p>
							<span>
								Courses you create or open will stay within easy reach here.
							</span>
						</div>
					) : (
						<ul className="recent-list">
							{recent.map((item) => (
								<li key={item.id}>
									<button
										type="button"
										onClick={() => void onOpenRecent(item.id)}
										disabled={busy}
									>
										<span className="recent-mark" aria-hidden="true">
											{item.name.slice(0, 1).toUpperCase()}
										</span>
										<span className="recent-copy">
											<strong>{item.name}</strong>
											<small>{item.path}</small>
										</span>
										<span className="recent-arrow" aria-hidden="true">
											→
										</span>
									</button>
								</li>
							))}
						</ul>
					)}
				</section>
			</section>
		</main>
	);
}

type WorkspaceShellProps = {
	workspace: Workspace;
	busy: boolean;
	sections: SectionLink[];
	activeView: WorkspaceView;
	onNavigate: (view: WorkspaceView) => void;
	onAllCourses: () => Promise<void>;
	children: React.ReactNode;
};

function WorkspaceShell({
	workspace,
	busy,
	sections,
	activeView,
	onNavigate,
	onAllCourses,
	children,
}: WorkspaceShellProps) {
	return (
		<div className={`workspace-layout view-${activeView}`}>
			<aside className="workspace-rail" aria-label="Course navigation">
				<button
					className="all-courses"
					type="button"
					disabled={busy}
					onClick={() => void onAllCourses()}
				>
					<span aria-hidden="true">←</span>
					All courses
				</button>

				<div className="workspace-summary">
					<p>Current folder</p>
					<strong>{workspace.name}</strong>
					<small title={workspace.path}>{workspace.path}</small>
				</div>

				{sections.length > 0 ? (
					<nav className="section-nav" aria-label="Workspace views">
						{sections.map((section) => (
							<button
								key={section.id}
								type="button"
								aria-current={activeView === section.id ? "page" : undefined}
								onClick={() => onNavigate(section.id)}
							>
								<span aria-hidden="true">
									{section.id === "course"
										? "CP"
										: section.id === "author"
											? "AG"
											: section.id === "current-state"
												? "CS"
												: section.id === "files"
													? "FL"
													: section.id === "library"
														? "LB"
														: section.id === "releases"
															? "RL"
															: "MD"}
								</span>
								{section.label}
							</button>
						))}
					</nav>
				) : null}
			</aside>
			<div className={`workspace-stage view-${activeView}`}>{children}</div>
		</div>
	);
}

type CourseSetupProps = {
	workspace: Workspace;
	busy: boolean;
	error: string | null;
	onCreate: (request: CourseRequest) => Promise<void>;
};

function CourseSetup({ workspace, busy, error, onCreate }: CourseSetupProps) {
	const [title, setTitle] = useState("");
	const [audience, setAudience] = useState("");
	const [goals, setGoals] = useState("");
	const [outcomes, setOutcomes] = useState("");
	const [lectures, setLectures] = useState("");

	function submit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		void onCreate({
			title: title.trim(),
			audience: audience.trim(),
			goals: splitLines(goals),
			outcomes: splitLines(outcomes),
			lectures: splitLines(lectures).map((lectureTitle) => ({
				title: lectureTitle,
			})),
		});
	}

	return (
		<main className="page-main setup-main" id="course-setup">
			<header className="page-heading setup-heading">
				<p className="eyebrow">New course</p>
				<h1>Give the course a clear shape.</h1>
				<p className="lede">
					Capture the essentials now. Goals and outcomes can wait until they are
					useful.
				</p>
			</header>

			<form className="course-form" onSubmit={submit} aria-busy={busy}>
				<section className="form-section" aria-labelledby="essentials-heading">
					<div className="form-section-heading">
						<span aria-hidden="true">01</span>
						<div>
							<h2 id="essentials-heading">Course essentials</h2>
							<p>Name the Course and the people it is for.</p>
						</div>
					</div>

					<div className="field">
						<label htmlFor="course-title">Course title</label>
						<input
							id="course-title"
							value={title}
							onChange={(event) => setTitle(event.target.value)}
							required
							maxLength={200}
							autoComplete="off"
						/>
					</div>
					<div className="field">
						<label htmlFor="course-audience">Audience</label>
						<textarea
							id="course-audience"
							value={audience}
							onChange={(event) => setAudience(event.target.value)}
							required
							rows={3}
							maxLength={1000}
						/>
					</div>
				</section>

				<section
					className="form-section"
					aria-labelledby="lectures-setup-heading"
				>
					<div className="form-section-heading">
						<span aria-hidden="true">02</span>
						<div>
							<h2 id="lectures-setup-heading">Lecture spine</h2>
							<p>Put the Lectures in the order you expect to teach them.</p>
						</div>
					</div>

					<div className="field">
						<label htmlFor="course-lectures">Lectures in teaching order</label>
						<textarea
							id="course-lectures"
							value={lectures}
							onChange={(event) => setLectures(event.target.value)}
							aria-describedby="lectures-help"
							required
							rows={6}
						/>
						<small id="lectures-help">One Lecture title per line.</small>
					</div>
				</section>

				<details className="intent-disclosure">
					<summary>
						<span className="disclosure-icon" aria-hidden="true">
							+
						</span>
						<span>
							<strong>Add course intent</strong>
							<small>Goals and learning outcomes · optional</small>
						</span>
					</summary>
					<div className="intent-fields">
						<div className="field">
							<label htmlFor="course-goals">Course goals</label>
							<textarea
								id="course-goals"
								value={goals}
								onChange={(event) => setGoals(event.target.value)}
								aria-describedby="goals-help"
								rows={4}
							/>
							<small id="goals-help">
								Broad directions for what you intend to teach.
							</small>
						</div>
						<div className="field">
							<label htmlFor="course-outcomes">Learning outcomes</label>
							<textarea
								id="course-outcomes"
								value={outcomes}
								onChange={(event) => setOutcomes(event.target.value)}
								aria-describedby="outcomes-help"
								rows={4}
							/>
							<small id="outcomes-help">
								What learners should be able to do afterward.
							</small>
						</div>
					</div>
				</details>

				{error ? (
					<p className="notice error-notice" role="alert">
						{error}
					</p>
				) : null}

				<div className="form-action">
					<p>Saved as readable files in {workspace.name}.</p>
					<button className="primary-action" type="submit" disabled={busy}>
						Create course
					</button>
				</div>
			</form>
		</main>
	);
}

type CourseReadErrorProps = {
	workspace: Workspace;
	error: string;
	busy: boolean;
	onRetry: () => Promise<void>;
};

function CourseReadError({
	workspace,
	error,
	busy,
	onRetry,
}: CourseReadErrorProps) {
	return (
		<main className="page-main error-page">
			<section aria-labelledby="course-error-title">
				<p className="eyebrow">Course state · {workspace.name}</p>
				<h1 id="course-error-title">Course state needs attention.</h1>
				<p className="lede">
					Correct <code>course.yaml</code> in the Course folder, then ask Course
					Harness to read it again.
				</p>
				<p className="notice error-notice" role="alert">
					{error}
				</p>
				<button
					className="primary-action"
					type="button"
					disabled={busy}
					onClick={() => void onRetry()}
				>
					Retry reading course
				</button>
			</section>
		</main>
	);
}

type CourseViewProps = {
	course: CoursePlan;
	busy: boolean;
	error: string | null;
	onSaveLectures: (lectures: LectureDraft[]) => Promise<boolean>;
};

function CourseView({ course, busy, error, onSaveLectures }: CourseViewProps) {
	const [editing, setEditing] = useState(false);
	const [drafts, setDrafts] = useState<LectureDraft[]>(() =>
		course.lectures.map(({ id, title }) => ({ id, title })),
	);

	function startEditing() {
		setDrafts(course.lectures.map(({ id, title }) => ({ id, title })));
		setEditing(true);
	}

	function cancelEditing() {
		setDrafts(course.lectures.map(({ id, title }) => ({ id, title })));
		setEditing(false);
	}

	function updateDraft(identity: string, title: string) {
		setDrafts((current) =>
			current.map((lecture) =>
				lecture.id === identity ? { ...lecture, title } : lecture,
			),
		);
	}

	function moveDraft(index: number, direction: -1 | 1) {
		setDrafts((current) => {
			const next = [...current];
			const target = index + direction;
			[next[index], next[target]] = [next[target], next[index]];
			return next;
		});
	}

	const hasBlankTitle = drafts.some((draft) => !draft.title.trim());

	async function saveChanges() {
		const saved = await onSaveLectures(
			drafts.map((draft) => ({ ...draft, title: draft.title.trim() })),
		);
		if (saved) {
			setEditing(false);
		}
	}

	return (
		<main className="page-main course-main">
			<header className="page-heading course-heading">
				<p className="eyebrow">Syllabus</p>
				<h1>{course.title}</h1>
				<p className="audience-copy">
					<span>For</span>
					{course.audience}
				</p>
			</header>

			{error ? (
				<p className="notice error-notice" role="alert">
					{error}
				</p>
			) : null}

			<section
				className="content-section syllabus-section"
				id="syllabus"
				aria-labelledby="lectures-heading"
				aria-busy={busy}
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">Teaching order</p>
						<h2 id="lectures-heading">Lecture spine</h2>
					</div>
					<div className="section-actions">
						{editing ? (
							<>
								<button
									className="quiet-action"
									type="button"
									onClick={cancelEditing}
									disabled={busy}
								>
									Cancel
								</button>
								<button
									className="primary-action compact-action"
									type="button"
									onClick={() => void saveChanges()}
									disabled={busy || hasBlankTitle}
								>
									Save changes
								</button>
							</>
						) : (
							<button
								className="secondary-action compact-action"
								type="button"
								onClick={startEditing}
							>
								Edit syllabus
							</button>
						)}
					</div>
				</div>

				<ol className={`lecture-list${editing ? " is-editing" : ""}`}>
					{(editing ? drafts : course.lectures).map(
						(lecture, index, lectures) => (
							<li className="lecture-item" key={lecture.id}>
								<span className="lecture-index" aria-hidden="true">
									{String(index + 1).padStart(2, "0")}
								</span>
								{editing ? (
									<div className="lecture-editor">
										<label htmlFor={`lecture-${lecture.id}`}>
											Lecture {index + 1} title
										</label>
										<input
											id={`lecture-${lecture.id}`}
											value={lecture.title}
											onChange={(event) =>
												updateDraft(lecture.id, event.target.value)
											}
											maxLength={200}
											aria-invalid={!lecture.title.trim()}
											aria-describedby={
												lecture.title.trim()
													? undefined
													: `lecture-${lecture.id}-error`
											}
										/>
										{!lecture.title.trim() ? (
											<small
												className="field-error"
												id={`lecture-${lecture.id}-error`}
											>
												A Lecture title cannot be empty.
											</small>
										) : null}
									</div>
								) : (
									<div className="lecture-copy">
										<p>Lecture {index + 1}</p>
										<h3>{lecture.title}</h3>
									</div>
								)}
								{editing ? (
									<fieldset className="order-actions">
										<legend>
											Reorder {lecture.title || `Lecture ${index + 1}`}
										</legend>
										<button
											type="button"
											onClick={() => moveDraft(index, -1)}
											disabled={busy || index === 0}
											aria-label={`Move ${lecture.title || `Lecture ${index + 1}`} earlier`}
										>
											<span aria-hidden="true">↑</span>
										</button>
										<button
											type="button"
											onClick={() => moveDraft(index, 1)}
											disabled={busy || index === lectures.length - 1}
											aria-label={`Move ${lecture.title || `Lecture ${index + 1}`} later`}
										>
											<span aria-hidden="true">↓</span>
										</button>
									</fieldset>
								) : null}
							</li>
						),
					)}
				</ol>
			</section>

			<section
				className="content-section"
				id="intent"
				aria-labelledby="intent-heading"
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">Shared direction</p>
						<h2 id="intent-heading">Course intent</h2>
					</div>
				</div>
				<div className="intent-grid">
					<section aria-labelledby="goals-heading">
						<h3 id="goals-heading">Goals</h3>
						{course.goals.length === 0 ? (
							<p className="empty-note">No goals added.</p>
						) : (
							<ul className="intent-list">
								{course.goals.map((goal) => (
									<li key={goal}>{goal}</li>
								))}
							</ul>
						)}
					</section>
					<section aria-labelledby="outcomes-heading">
						<h3 id="outcomes-heading">Learning outcomes</h3>
						{course.outcomes.length === 0 ? (
							<p className="empty-note">No outcomes added.</p>
						) : (
							<ul className="intent-list">
								{course.outcomes.map((outcome) => (
									<li key={outcome}>{outcome}</li>
								))}
							</ul>
						)}
					</section>
				</div>
			</section>
		</main>
	);
}

function FilesView({
	workspace,
	files,
}: {
	workspace: Workspace;
	files: WorkspaceEntry[];
}) {
	return (
		<main className="page-main files-main" aria-labelledby="files-heading">
			<header className="page-heading">
				<p className="eyebrow">Course Workspace</p>
				<h1 id="files-heading">Files</h1>
				<p className="workspace-location">{workspace.path}</p>
			</header>
			<section
				className="content-section files-section"
				aria-label="Course files"
			>
				<div className="content-section-heading">
					<div>
						<p className="section-kicker">On disk</p>
						<h2>Visible Course files</h2>
					</div>
					<span className="count-badge">{files.length}</span>
				</div>
				{files.length === 0 ? (
					<p className="empty-note">
						This Course folder has no visible files yet.
					</p>
				) : (
					<ul className="file-list">
						{files.map((entry) => (
							<li key={entry.path}>
								<span aria-hidden="true">
									{entry.kind === "directory" ? "▸" : "—"}
								</span>
								{entry.path}
							</li>
						))}
					</ul>
				)}
			</section>
		</main>
	);
}

const workspaceViews: SectionLink[] = [
	{ id: "course", label: "Course Plan" },
	{ id: "author", label: "Authoring" },
	{ id: "current-state", label: "Current State" },
	{ id: "files", label: "Files" },
	{ id: "library", label: "Library" },
	{ id: "releases", label: "Releases" },
	{ id: "templates", label: "Templates" },
	{ id: "models", label: "Models" },
];

export function App() {
	const [workspace, setWorkspace] = useState<Workspace | null | undefined>(
		undefined,
	);
	const [recent, setRecent] = useState<RecentWorkspace[]>([]);
	const [course, setCourse] = useState<CoursePlan | null | undefined>(
		undefined,
	);
	const [files, setFiles] = useState<WorkspaceEntry[]>([]);
	const [catalog, setCatalog] = useState<ModelCatalog>({
		provider_accounts: [],
		model_presets: [],
		selected_model_id: null,
	});
	const [resources, setResources] = useState<ResourceState[]>([]);
	const [sources, setSources] = useState<Source[]>([]);
	const [presentations, setPresentations] = useState<PresentationSummary[]>([]);
	const [presentationVersion, setPresentationVersion] = useState(0);
	const [templates, setTemplates] = useState<TemplateProfileSummary[]>([]);
	const [activeView, setActiveView] = useState<WorkspaceView>("course");
	const [evidenceTarget, setEvidenceTarget] = useState<EvidenceTarget | null>(
		null,
	);
	const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
	const [chatApproval, setChatApproval] = useState<AgentInterrupt | null>(null);
	const [chatContext, setChatContext] = useState<string | null>(null);
	const [reconciliationDriftId, setReconciliationDriftId] = useState<
		string | null
	>(null);
	const [agentRunning, setAgentRunning] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);

	useLayoutEffect(() => {
		void activeView;
		scrollToTop();
	}, [activeView]);

	function openAgentWithContext(context: string, driftId?: string) {
		setChatContext(context);
		setReconciliationDriftId(driftId ?? null);
		setEvidenceTarget(null);
		if (activeView !== "author") scrollToTop();
		setActiveView("author");
	}

	function navigateTo(
		view: WorkspaceView,
		target: EvidenceTarget | null = null,
	) {
		scrollToTop();
		setEvidenceTarget(view === "library" ? target : null);
		setActiveView(view);
	}

	function openEvidence(target: EvidenceTarget) {
		navigateTo("library", target);
	}

	const loadWorkspace = useCallback(
		async (active: Workspace, signal?: AbortSignal) => {
			scrollToTop();
			setActiveView("course");
			setEvidenceTarget(null);
			setWorkspace(active);
			setCourse(undefined);
			const [
				courseResponse,
				filesResponse,
				modelsResponse,
				chatResponse,
				resourcesResponse,
				sourcesResponse,
				presentationsResponse,
				templatesResponse,
			] = await Promise.all([
				fetch("/api/course", { signal }),
				fetch("/api/workspace/files", { signal }),
				fetch("/api/models", { signal }),
				fetch("/api/chat", { signal }),
				fetch("/api/resources", { signal }),
				fetch("/api/sources", { signal }),
				fetch("/api/presentations", { signal }),
				fetch("/api/templates", { signal }),
			]);
			if (courseResponse.status === 404) {
				setCourse(null);
			} else if (courseResponse.ok) {
				setCourse((await courseResponse.json()) as CoursePlan);
			} else {
				throw new Error(await responseError(courseResponse));
			}
			if (filesResponse.ok) {
				setFiles((await filesResponse.json()) as WorkspaceEntry[]);
			} else {
				throw new Error(await responseError(filesResponse));
			}
			if (!modelsResponse.ok) {
				throw new Error(await responseError(modelsResponse));
			}
			setCatalog((await modelsResponse.json()) as ModelCatalog);
			if (!chatResponse.ok) {
				throw new Error(await responseError(chatResponse));
			}
			const transcript = (await chatResponse.json()) as {
				messages: ChatMessage[];
				approval: AgentInterrupt | null;
			};
			setChatMessages(transcript.messages);
			setChatApproval(transcript.approval);
			if (resourcesResponse.ok) {
				setResources((await resourcesResponse.json()) as ResourceState[]);
			}
			if (sourcesResponse.ok) {
				setSources((await sourcesResponse.json()) as Source[]);
			}
			if (presentationsResponse.ok) {
				setPresentations(
					(await presentationsResponse.json()) as PresentationSummary[],
				);
			}
			if (templatesResponse.ok) {
				setTemplates(
					(await templatesResponse.json()) as TemplateProfileSummary[],
				);
			}
		},
		[],
	);

	useEffect(() => {
		const controller = new AbortController();

		async function initialize() {
			try {
				const workspaceResponse = await fetch("/api/workspace", {
					signal: controller.signal,
				});
				if (workspaceResponse.status === 409) {
					setWorkspace(null);
					setCourse(null);
					const recentResponse = await fetch("/api/launcher/recent", {
						signal: controller.signal,
					});
					if (recentResponse.ok) {
						setRecent((await recentResponse.json()) as RecentWorkspace[]);
					}
					return;
				}
				if (!workspaceResponse.ok) {
					throw new Error(await responseError(workspaceResponse));
				}
				await loadWorkspace(
					(await workspaceResponse.json()) as Workspace,
					controller.signal,
				);
			} catch (caught) {
				if (!(caught instanceof DOMException && caught.name === "AbortError")) {
					setError(
						caught instanceof Error
							? caught.message
							: "Course Harness could not start.",
					);
				}
			}
		}

		void initialize();
		return () => controller.abort();
	}, [loadWorkspace]);

	async function runMutation(
		action: () => Promise<void>,
		fallbackMessage: string,
	): Promise<boolean> {
		setBusy(true);
		setError(null);
		try {
			await action();
			return true;
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : fallbackMessage);
			return false;
		} finally {
			setBusy(false);
		}
	}

	async function selectWorkspace(endpoint: "new-course" | "open-folder") {
		await runMutation(async () => {
			const response = await fetch(`/api/launcher/${endpoint}`, {
				method: "POST",
			});
			if (response.status === 204) {
				return;
			}
			if (!response.ok) {
				throw new Error(await responseError(response));
			}
			await loadWorkspace((await response.json()) as Workspace);
		}, "The Course folder could not be opened.");
	}

	async function openRecent(identity: string) {
		await runMutation(async () => {
			const response = await fetch(
				`/api/launcher/recent/${encodeURIComponent(identity)}/open`,
				{
					method: "POST",
				},
			);
			if (!response.ok) {
				throw new Error(await responseError(response));
			}
			await loadWorkspace((await response.json()) as Workspace);
		}, "The Course could not be reopened.");
	}

	async function createCourse(request: CourseRequest) {
		await runMutation(async () => {
			const response = await fetch("/api/course", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(request),
			});
			if (!response.ok) {
				throw new Error(await responseError(response));
			}
			scrollToTop();
			setCourse((await response.json()) as CoursePlan);
			const filesResponse = await fetch("/api/workspace/files");
			if (filesResponse.ok) {
				setFiles((await filesResponse.json()) as WorkspaceEntry[]);
			}
		}, "The Course could not be created.");
	}

	async function refreshPresentations() {
		const response = await fetch("/api/presentations");
		if (response.ok) {
			setPresentations((await response.json()) as PresentationSummary[]);
			setPresentationVersion((v) => v + 1);
		}
	}

	async function saveLectureChanges(drafts: LectureDraft[]): Promise<boolean> {
		if (!course) {
			return false;
		}

		return runMutation(async () => {
			let updated = course;
			const originalById = new Map(
				course.lectures.map((lecture) => [lecture.id, lecture]),
			);

			for (const draft of drafts) {
				const original = originalById.get(draft.id);
				if (!original || original.title === draft.title) {
					continue;
				}
				const response = await fetch(
					`/api/course/lectures/${encodeURIComponent(draft.id)}`,
					{
						method: "PATCH",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ title: draft.title }),
					},
				);
				if (!response.ok) {
					throw new Error(await responseError(response));
				}
				updated = (await response.json()) as CoursePlan;
			}

			const requestedOrder = drafts.map((lecture) => lecture.id);
			const currentOrder = updated.lectures.map((lecture) => lecture.id);
			if (
				requestedOrder.some(
					(identity, index) => identity !== currentOrder[index],
				)
			) {
				const response = await fetch("/api/course/lectures/order", {
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ lecture_ids: requestedOrder }),
				});
				if (!response.ok) {
					throw new Error(await responseError(response));
				}
				updated = (await response.json()) as CoursePlan;
			}

			setCourse(updated);
		}, "The syllabus changes could not be saved.");
	}

	async function retryCourse() {
		if (!workspace) {
			return;
		}
		await runMutation(
			() => loadWorkspace(workspace),
			"The Course could not be read.",
		);
	}

	async function returnToCourses() {
		await runMutation(async () => {
			const closeResponse = await fetch("/api/workspace/close", {
				method: "POST",
			});
			if (!closeResponse.ok) {
				throw new Error(await responseError(closeResponse));
			}
			scrollToTop();
			setWorkspace(null);
			setCourse(null);
			setEvidenceTarget(null);
			setFiles([]);
			setChatMessages([]);
			setChatApproval(null);
			setCatalog({
				provider_accounts: [],
				model_presets: [],
				selected_model_id: null,
			});
			setActiveView("course");

			const recentResponse = await fetch("/api/launcher/recent");
			if (!recentResponse.ok) {
				throw new Error(await responseError(recentResponse));
			}
			setRecent((await recentResponse.json()) as RecentWorkspace[]);
		}, "Courses could not be opened.");
	}

	let content: React.ReactNode;
	if (workspace === undefined) {
		content = (
			<main className="loading-main" aria-busy="true">
				<p>Starting Course Harness…</p>
			</main>
		);
	} else if (workspace === null) {
		content = (
			<Launcher
				recent={recent}
				busy={busy || agentRunning}
				error={error}
				onNew={() => selectWorkspace("new-course")}
				onOpen={() => selectWorkspace("open-folder")}
				onOpenRecent={openRecent}
			/>
		);
	} else {
		content = (
			<WorkspaceShell
				workspace={workspace}
				busy={busy || agentRunning}
				sections={workspaceViews}
				activeView={activeView}
				onNavigate={navigateTo}
				onAllCourses={returnToCourses}
			>
				{activeView === "author" ? (
					<main
						className={`authoring-workspace${course ? "" : " is-course-empty"}`}
						aria-labelledby="authoring-heading"
					>
						<header className="authoring-header">
							<div>
								<p className="eyebrow">Author</p>
								<h1 id="authoring-heading">Build the Lecture</h1>
							</div>
							<p>
								Shape the Presentation and direct the Course Agent in one place.
							</p>
						</header>
						<div className="authoring-body">
							<ResizableSplit
								storageKey="course-harness:authoring-agent-width"
								primary={
									course ? (
										<PresentationView
											course={course}
											busy={busy || agentRunning}
											presentations={presentations}
											presentationVersion={presentationVersion}
											templates={templates}
											chatContext={chatContext}
											onChange={refreshPresentations}
											onChatContext={openAgentWithContext}
										/>
									) : (
										<section className="authoring-course-empty">
											<p className="section-kicker">Presentation</p>
											<h2>Start with a Course Plan</h2>
											<p>
												Ask the Course Agent to draft one, or create it from the
												Course Plan view.
											</p>
										</section>
									)
								}
								secondary={
									<AgentPanel
										key={workspace.path}
										embedded
										initialMessages={chatMessages}
										initialApproval={chatApproval}
										catalog={catalog}
										onCatalogChange={setCatalog}
										onOpenModels={() => navigateTo("models")}
										onRunningChange={setAgentRunning}
										onCourseChange={async (updated) => {
											setCourse(updated);
											const filesResponse = await fetch("/api/workspace/files");
											if (filesResponse.ok) {
												setFiles(
													(await filesResponse.json()) as WorkspaceEntry[],
												);
											}
										}}
										onPresentationsChange={refreshPresentations}
										chatContext={chatContext}
										reconciliationDriftId={reconciliationDriftId}
										onChatContextCleared={() => setChatContext(null)}
										onReconciliationDriftCleared={() =>
											setReconciliationDriftId(null)
										}
										onConversationCleared={() => {
											setChatMessages([]);
											setChatApproval(null);
										}}
										onTranscriptChange={(messages, approval) => {
											setChatMessages(messages);
											setChatApproval(approval);
										}}
									/>
								}
							/>
						</div>
					</main>
				) : activeView === "current-state" ? (
					<CurrentStateView
						workspaceName={workspace.name}
						onReconcileWithAgent={(driftId) =>
							openAgentWithContext(
								"Workspace Drift needs Reconciliation. Review the Current State findings, explain the inconsistency, and propose a reviewed patch. Do not treat the Drift as repaired until the Course Author approves a valid change.",
								driftId,
							)
						}
					/>
				) : activeView === "models" ? (
					<ModelsView catalog={catalog} onCatalogChange={setCatalog} />
				) : activeView === "library" ? (
					<LibraryView
						resources={resources}
						sources={sources}
						onResourcesChange={setResources}
						onSourcesChange={setSources}
						evidenceTarget={evidenceTarget}
						onEvidenceTargetClose={() => setEvidenceTarget(null)}
					/>
				) : activeView === "releases" && course ? (
					<ReleasesView
						course={course}
						presentations={presentations}
						sources={sources}
						onOpenEvidence={openEvidence}
						onOpenAgent={openAgentWithContext}
					/>
				) : activeView === "templates" ? (
					<TemplatesView
						templates={templates}
						catalog={catalog}
						onTemplatesChange={setTemplates}
					/>
				) : activeView === "files" ? (
					<FilesView workspace={workspace} files={files} />
				) : course === undefined && error ? (
					<CourseReadError
						workspace={workspace}
						error={error}
						busy={busy || agentRunning}
						onRetry={retryCourse}
					/>
				) : course === undefined ? (
					<main className="loading-main" aria-busy="true">
						<p>Reading your Course…</p>
					</main>
				) : course === null ? (
					<CourseSetup
						workspace={workspace}
						busy={busy || agentRunning}
						error={error}
						onCreate={createCourse}
					/>
				) : (
					<CourseView
						course={course}
						busy={busy || agentRunning}
						error={error}
						onSaveLectures={saveLectureChanges}
					/>
				)}
			</WorkspaceShell>
		);
	}

	return (
		<div className="app-shell">
			<header className="topbar">
				<Brand />
				<div className="topbar-actions">
					<button
						className="runtime-diagnostics-trigger"
						type="button"
						onClick={() => setDiagnosticsOpen(true)}
					>
						Runtime diagnostics
					</button>
					<p className="local-status">
						<span aria-hidden="true" /> Local session
					</p>
				</div>
			</header>
			{content}
			{diagnosticsOpen ? (
				<RuntimeDiagnosticsDialog onClose={() => setDiagnosticsOpen(false)} />
			) : null}
		</div>
	);
}
