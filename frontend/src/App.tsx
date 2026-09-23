import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { responseError } from "./api";
import { CoursePlanCanvas } from "./canvas/CoursePlanCanvas";
import { HistoryCanvas, type HistoryTab } from "./canvas/HistoryCanvas";
import { LectureCanvas } from "./canvas/LectureCanvas";
import { ReleaseCanvas } from "./canvas/ReleaseCanvas";
import { SourceReader } from "./canvas/SourceReader";
import { SourcesCanvas, type SourcesTab } from "./canvas/SourcesCanvas";
import { ConversationPane } from "./chat/ConversationPane";
import {
	ConversationDialogs,
	type ConversationDialog,
} from "./chat/ConversationDialogs";
import { ModelDownloadDialog } from "./dialogs/ModelDownloadDialog";
import { ModelSetupDialog } from "./dialogs/ModelSetupDialog";
import { SettingsDialog, type SettingsSection } from "./dialogs/SettingsDialog";
import { Launcher, type RecentWorkspace } from "./Launcher";
import type {
	CoursePlan,
	CurrentState,
	ModelCatalog,
	PresentationSummary,
	ReaderTarget,
	ResourceState,
	Source,
	TemplateProfileSummary,
	Workspace,
} from "./models";
import { Navigator } from "./shell/Navigator";
import { CanvasFrame, type LayoutMode } from "./shell/CanvasFrame";
import { DriftBanner } from "./shell/DriftBanner";
import { errorMessage, isAbort } from "./ui";
import {
	type AgentContext,
	type RunResult,
	useCourseAgent,
} from "./useCourseAgent";
import { useLibrary } from "./useLibrary";

export type CanvasTarget =
	| { kind: "plan" }
	| { kind: "sources"; tab?: SourcesTab }
	| { kind: "lecture"; lectureId: string }
	| { kind: "reader"; target: ReaderTarget; returnTo: CanvasTarget | null }
	| { kind: "history"; tab?: HistoryTab }
	| { kind: "release" };

const emptyCatalog: ModelCatalog = {
	provider_accounts: [],
	model_presets: [],
	selected_model_id: null,
};

const LAYOUT_KEY = "course-harness:layout";

function storedLayout(): LayoutMode {
	try {
		const value = window.localStorage.getItem(LAYOUT_KEY);
		return value === "canvas" ? "canvas" : "split";
	} catch {
		return "split";
	}
}

export function App() {
	const [workspace, setWorkspace] = useState<Workspace | null | undefined>(
		undefined,
	);
	const [recent, setRecent] = useState<RecentWorkspace[]>([]);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			try {
				const response = await fetch("/api/workspace", {
					signal: controller.signal,
				});
				if (response.status === 409) {
					setWorkspace(null);
					const recentResponse = await fetch("/api/launcher/recent", {
						signal: controller.signal,
					});
					if (recentResponse.ok)
						setRecent((await recentResponse.json()) as RecentWorkspace[]);
					return;
				}
				if (!response.ok) throw new Error(await responseError(response));
				setWorkspace((await response.json()) as Workspace);
			} catch (caught) {
				if (!isAbort(caught))
					setError(errorMessage(caught, "Course Harness could not start."));
			}
		})();
		return () => controller.abort();
	}, []);

	async function choose(request: () => Promise<Response>, fallback: string) {
		setBusy(true);
		setError(null);
		try {
			const response = await request();
			if (response.status === 204) return;
			if (!response.ok) throw new Error(await responseError(response));
			setWorkspace((await response.json()) as Workspace);
		} catch (caught) {
			setError(errorMessage(caught, fallback));
		} finally {
			setBusy(false);
		}
	}

	async function returnToCourses() {
		const closeResponse = await fetch("/api/workspace/close", {
			method: "POST",
		});
		if (!closeResponse.ok) throw new Error(await responseError(closeResponse));
		setWorkspace(null);
		setError(null);
		const recentResponse = await fetch("/api/launcher/recent");
		if (recentResponse.ok)
			setRecent((await recentResponse.json()) as RecentWorkspace[]);
	}

	if (workspace === undefined) {
		return (
			<main className="boot" aria-busy="true">
				{error ? <p role="alert">{error}</p> : <p>Opening Course Harness…</p>}
			</main>
		);
	}

	if (workspace === null) {
		return (
			<Launcher
				recent={recent}
				busy={busy}
				error={error}
				onNew={() =>
					choose(
						() => fetch("/api/launcher/new-course", { method: "POST" }),
						"The Course folder could not be created.",
					)
				}
				onOpen={() =>
					choose(
						() => fetch("/api/launcher/open-folder", { method: "POST" }),
						"The Course folder could not be opened.",
					)
				}
				onOpenRecent={(identity) =>
					choose(
						() =>
							fetch(
								`/api/launcher/recent/${encodeURIComponent(identity)}/open`,
								{ method: "POST" },
							),
						"The Course could not be reopened.",
					)
				}
			/>
		);
	}

	return (
		<CourseWorkspace
			key={workspace.path}
			workspace={workspace}
			onAllCourses={returnToCourses}
		/>
	);
}

function CourseWorkspace({
	workspace,
	onAllCourses,
}: {
	workspace: Workspace;
	onAllCourses: () => Promise<void>;
}) {
	const [course, setCourse] = useState<CoursePlan | null | undefined>(
		undefined,
	);
	const [courseError, setCourseError] = useState<string | null>(null);
	const [catalog, setCatalog] = useState<ModelCatalog>(emptyCatalog);
	const [resources, setResources] = useState<ResourceState[]>([]);
	const [sources, setSources] = useState<Source[]>([]);
	const [presentations, setPresentations] = useState<PresentationSummary[]>([]);
	const [presentationVersion, setPresentationVersion] = useState(0);
	const [templates, setTemplates] = useState<TemplateProfileSummary[]>([]);
	const [currentState, setCurrentState] = useState<CurrentState | null>(null);
	const [canvas, setCanvas] = useState<CanvasTarget | null>(null);
	const [layout, setLayoutState] = useState<LayoutMode>(storedLayout);
	const [narrowPane, setNarrowPane] = useState<"conversation" | "canvas">(
		"conversation",
	);
	const [navOpen, setNavOpen] = useState(false);
	const [focusContext, setFocusContext] = useState<AgentContext | null>(null);
	const [settings, setSettings] = useState<SettingsSection | null>(null);
	const [modelSetupOpen, setModelSetupOpen] = useState(false);
	const [conversationDialog, setConversationDialog] =
		useState<ConversationDialog | null>(null);
	const [shellError, setShellError] = useState<string | null>(null);
	const composerRef = useRef<HTMLTextAreaElement | null>(null);

	function setLayout(next: LayoutMode) {
		setLayoutState(next);
		try {
			window.localStorage.setItem(LAYOUT_KEY, next);
		} catch {
			// Layout preference is a convenience only.
		}
	}

	const loadCourse = useCallback(async (signal?: AbortSignal) => {
		const response = await fetch("/api/course", { signal });
		if (response.status === 404) {
			setCourse(null);
			return;
		}
		if (!response.ok) throw new Error(await responseError(response));
		setCourse((await response.json()) as CoursePlan);
		setCourseError(null);
	}, []);

	const refreshCurrentState = useCallback(async () => {
		try {
			const response = await fetch("/api/workspace/current-state");
			if (response.status === 404) {
				setCurrentState(null);
				return;
			}
			if (response.ok) setCurrentState((await response.json()) as CurrentState);
		} catch {
			// History shows its own errors; the badge simply stays as it was.
		}
	}, []);

	const refreshPresentations = useCallback(async () => {
		const response = await fetch("/api/presentations");
		if (response.ok) {
			setPresentations((await response.json()) as PresentationSummary[]);
			setPresentationVersion((version) => version + 1);
		}
	}, []);

	const refreshSources = useCallback(async () => {
		const response = await fetch("/api/sources");
		if (response.ok) setSources((await response.json()) as Source[]);
	}, []);

	useEffect(() => {
		const controller = new AbortController();
		const { signal } = controller;
		void (async () => {
			try {
				const [
					models,
					resourceList,
					sourceList,
					presentationList,
					templateList,
				] = await Promise.all([
					fetch("/api/models", { signal }),
					fetch("/api/resources", { signal }),
					fetch("/api/sources", { signal }),
					fetch("/api/presentations", { signal }),
					fetch("/api/templates", { signal }),
					loadCourse(signal).catch((caught: unknown) => {
						if (!isAbort(caught))
							setCourseError(
								errorMessage(caught, "The Course Plan could not be read."),
							);
					}),
					refreshCurrentState(),
				]);
				if (models.ok) setCatalog((await models.json()) as ModelCatalog);
				if (resourceList.ok)
					setResources((await resourceList.json()) as ResourceState[]);
				if (sourceList.ok) setSources((await sourceList.json()) as Source[]);
				if (presentationList.ok)
					setPresentations(
						(await presentationList.json()) as PresentationSummary[],
					);
				if (templateList.ok)
					setTemplates((await templateList.json()) as TemplateProfileSummary[]);
			} catch (caught) {
				if (!isAbort(caught))
					setShellError(
						errorMessage(caught, "The Course could not be opened."),
					);
			}
		})();
		return () => controller.abort();
	}, [loadCourse, refreshCurrentState]);

	const library = useLibrary({
		resources,
		sources,
		onResourcesChange: setResources,
		onSourcesChange: setSources,
	});

	const agent = useCourseAgent({
		catalog,
		onCourseChange: (updated) => {
			setCourse(updated);
		},
		onPresentationsChange: refreshPresentations,
		onSourcesChange: refreshSources,
		onRunSettled: (result: RunResult) => {
			void refreshCurrentState();
			if (result.planChanged) {
				setCanvas((current) => current ?? { kind: "plan" });
			}
		},
	});

	// Keep the composer's context aligned with what is open, unless the author is mid-draft.
	const focusKey = focusContext?.key ?? null;
	const agentContextKey = agent.context?.key ?? null;
	const promptEmpty = agent.prompt.trim() === "";
	const { setContext } = agent;
	// biome-ignore lint/correctness/useExhaustiveDependencies: only a change of focus should retarget the draft context
	useEffect(() => {
		if (promptEmpty && focusKey !== agentContextKey) setContext(focusContext);
	}, [focusKey]);

	const openCanvas = useCallback((target: CanvasTarget) => {
		setCanvas(target);
		setNarrowPane("canvas");
		setNavOpen(false);
	}, []);

	/** Choosing a conversation brings the conversation back into view. */
	function showConversation() {
		setNarrowPane("conversation");
		setNavOpen(false);
		if (layout === "canvas") setLayout("split");
	}

	function closeCanvas() {
		setCanvas(null);
		setNarrowPane("conversation");
	}

	/** Place an editable request in the composer. It is never sent automatically. */
	const askAgent = useCallback(
		(request: string, context?: AgentContext | null) => {
			agent.setPrompt(request);
			if (context !== undefined) agent.setContext(context);
			setNarrowPane("conversation");
			setLayoutState((current) => (current === "canvas" ? "split" : current));
			requestAnimationFrame(() => {
				const composer = composerRef.current;
				if (!composer) return;
				composer.focus();
				composer.setSelectionRange(
					composer.value.length,
					composer.value.length,
				);
			});
		},
		[agent],
	);

	// Canvas targets that point at removed content fall back to the Course Plan.
	useEffect(() => {
		if (canvas?.kind !== "lecture" || !course) return;
		if (!course.lectures.some((lecture) => lecture.id === canvas.lectureId)) {
			setCanvas({ kind: "plan" });
		}
	}, [canvas, course]);

	useEffect(() => {
		if (!canvas) setFocusContext(null);
		else if (canvas.kind === "plan" && course)
			setFocusContext({
				key: "plan",
				label: "Course Plan",
				instruction: "I'm looking at the Course Plan",
			});
		else if (canvas.kind !== "lecture") setFocusContext(null);
	}, [canvas, course]);

	const lectureNumber = useMemo(() => {
		const map = new Map<string, number>();
		for (const [index, lecture] of (course?.lectures ?? []).entries())
			map.set(lecture.id, index + 1);
		return map;
	}, [course]);

	const changesCount = currentState?.changes.length ?? 0;
	const drift =
		currentState && currentState.drift !== "clean" ? currentState : null;

	function renderCanvas(target: CanvasTarget) {
		switch (target.kind) {
			case "plan":
				return (
					<CoursePlanCanvas
						course={course ?? null}
						courseError={courseError}
						presentations={presentations}
						sources={sources}
						busy={agent.running}
						onCourseChange={(updated) => {
							setCourse(updated);
							void refreshCurrentState();
						}}
						onRetry={() => void loadCourse().catch(() => undefined)}
						onOpenLecture={(lectureId) =>
							openCanvas({ kind: "lecture", lectureId })
						}
						onAskAgent={askAgent}
					/>
				);
			case "sources":
				return (
					<SourcesCanvas
						key="sources"
						initialTab={target.tab}
						resources={resources}
						sources={sources}
						library={library}
						onRead={(readerTarget) =>
							openCanvas({
								kind: "reader",
								target: readerTarget,
								returnTo: target,
							})
						}
						onAskAgent={askAgent}
					/>
				);
			case "reader":
				return (
					<SourceReader
						key={`${target.target.sourceId}-${target.target.resourceId}-${target.target.lineStart}`}
						target={{
							...target.target,
							resourceId:
								target.target.resourceId ??
								sources.find((source) => source.id === target.target.sourceId)
									?.resource_id ??
								null,
						}}
						returnLabel={
							target.returnTo?.kind === "lecture"
								? "Back to Slides"
								: target.returnTo
									? "Back to Sources"
									: null
						}
						onReturn={() => setCanvas(target.returnTo ?? { kind: "sources" })}
						onAskAgent={askAgent}
					/>
				);
			case "lecture": {
				if (!course) return null;
				const lecture = course.lectures.find(
					(item) => item.id === target.lectureId,
				);
				if (!lecture) return null;
				return (
					<LectureCanvas
						key={lecture.id}
						course={course}
						lecture={lecture}
						lectureNumber={lectureNumber.get(lecture.id) ?? 1}
						presentationVersion={presentationVersion}
						templates={templates}
						sources={sources}
						busy={agent.running}
						onPresentationsChange={async () => {
							await refreshPresentations();
							void refreshCurrentState();
						}}
						onCourseChange={async () => {
							await loadCourse();
							await refreshPresentations();
							void refreshCurrentState();
						}}
						onFocusChange={setFocusContext}
						onAskAgent={askAgent}
						onOpenEvidence={(readerTarget) =>
							openCanvas({
								kind: "reader",
								target: readerTarget,
								returnTo: target,
							})
						}
						onManageTemplates={() => setSettings("templates")}
						onTemplatesChange={setTemplates}
					/>
				);
			}
			case "history":
				return (
					<HistoryCanvas
						key="history"
						initialTab={target.tab}
						course={course ?? null}
						presentations={presentations}
						onChanged={async () => {
							await Promise.all([
								refreshCurrentState(),
								loadCourse().catch(() => undefined),
								refreshPresentations(),
								refreshSources(),
							]);
						}}
						onReconcile={(driftId) => {
							agent.setDriftId(driftId);
							askAgent(
								"Course files changed outside the app. Review the changes, explain what is inconsistent, and propose a fix for me to approve.",
								{
									key: `drift-${driftId}`,
									label: "Outside changes",
									instruction:
										"Workspace Drift needs Reconciliation. Review the Current State findings, explain the inconsistency, and propose a reviewed patch. Do not treat the Drift as repaired until the Course Author approves a valid change.",
								},
							);
						}}
						onPublish={() => openCanvas({ kind: "release" })}
					/>
				);
			case "release":
				if (!course) return null;
				return (
					<ReleaseCanvas
						course={course}
						presentations={presentations}
						sources={sources}
						currentState={currentState}
						onOpenHistory={(tab) => openCanvas({ kind: "history", tab })}
						onOpenEvidence={(readerTarget) =>
							openCanvas({
								kind: "reader",
								target: readerTarget,
								returnTo: target,
							})
						}
						onAskAgent={askAgent}
						onPublished={() => void refreshCurrentState()}
					/>
				);
		}
	}

	const effectiveLayout: LayoutMode = canvas ? layout : "conversation";

	return (
		<div
			className="shell"
			data-layout={effectiveLayout}
			data-narrow-pane={canvas ? narrowPane : "conversation"}
			data-nav-open={navOpen}
		>
			<Navigator
				workspace={workspace}
				course={course ?? null}
				sources={sources}
				presentations={presentations}
				canvas={canvas}
				agent={agent}
				changesCount={changesCount}
				onOpen={openCanvas}
				onShowConversation={showConversation}
				onClose={() => setNavOpen(false)}
				onSettings={() => setSettings("models")}
				onWorkspaceDetails={() => setSettings("workspace")}
				onConversationDialog={setConversationDialog}
				onAllCourses={async () => {
					try {
						await onAllCourses();
					} catch (caught) {
						setShellError(errorMessage(caught, "Courses could not be opened."));
					}
				}}
			/>
			<button
				type="button"
				className="nav-scrim"
				aria-label="Close navigation"
				tabIndex={-1}
				onClick={() => setNavOpen(false)}
			/>
			<div className="work">
				{drift ? (
					<DriftBanner
						state={drift}
						onReview={() => openCanvas({ kind: "history", tab: "changes" })}
					/>
				) : null}
				{shellError ? (
					<div className="shell-error" role="alert">
						{shellError}
					</div>
				) : null}
				<div className="panes">
					<ConversationPane
						agent={agent}
						catalog={catalog}
						course={course ?? null}
						sources={sources}
						library={library}
						canvasOpen={canvas !== null}
						focusContext={focusContext}
						composerRef={composerRef}
						onCatalogChange={setCatalog}
						onAddModel={() => setModelSetupOpen(true)}
						onOpenNav={() => setNavOpen(true)}
						onShowCanvas={() => setNarrowPane("canvas")}
						onOpen={openCanvas}
						onConversationDialog={setConversationDialog}
						presentations={presentations}
						lectureNumber={lectureNumber}
					/>
					{canvas ? (
						<CanvasFrame
							layout={layout}
							onLayout={setLayout}
							onClose={closeCanvas}
							onShowConversation={() => setNarrowPane("conversation")}
							onOpenNav={() => setNavOpen(true)}
							canPublish={Boolean(course)}
							onPublish={() => openCanvas({ kind: "release" })}
							publishActive={canvas.kind === "release"}
						>
							{renderCanvas(canvas)}
						</CanvasFrame>
					) : null}
				</div>
			</div>

			{library.modelPrompt ? <ModelDownloadDialog library={library} /> : null}
			{modelSetupOpen ? (
				<ModelSetupDialog
					catalog={catalog}
					onCatalogChange={setCatalog}
					onClose={() => {
						setModelSetupOpen(false);
						requestAnimationFrame(() => composerRef.current?.focus());
					}}
				/>
			) : null}
			{settings ? (
				<SettingsDialog
					section={settings}
					onSection={setSettings}
					workspace={workspace}
					catalog={catalog}
					onCatalogChange={setCatalog}
					templates={templates}
					onTemplatesChange={setTemplates}
					library={library}
					onClose={() => setSettings(null)}
				/>
			) : null}
			{conversationDialog ? (
				<ConversationDialogs
					dialog={conversationDialog}
					agent={agent}
					onClose={() => setConversationDialog(null)}
				/>
			) : null}
		</div>
	);
}
