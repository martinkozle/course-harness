import { type FormEvent, useCallback, useEffect, useState } from "react";

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

type CoursePlan = {
  schema_version: 1;
  id: string;
  title: string;
  audience: string;
  goals: string[];
  outcomes: string[];
  lectures: Lecture[];
};

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

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? "Course Harness could not complete that action.";
  } catch {
    return "Course Harness could not complete that action.";
  }
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function Brand() {
  return (
    <a className="wordmark" href="/" aria-label="Course Harness home">
      <span className="wordmark-glyph" aria-hidden="true">
        CH
      </span>
      <span>Course Harness</span>
    </a>
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

function Launcher({ recent, busy, error, onNew, onOpen, onOpenRecent }: LauncherProps) {
  return (
    <main className="launcher-main">
      <section className="launcher" aria-labelledby="launcher-title" aria-busy={busy}>
        <p className="eyebrow">Workspace Launcher</p>
        <h1 id="launcher-title">Choose where your Course lives.</h1>
        <p className="lede">
          Course Harness opens one folder for this session. Course state stays readable there, under
          your control.
        </p>

        <div className="launcher-actions">
          <button className="primary-action" type="button" onClick={onNew} disabled={busy}>
            New Course
          </button>
          <button className="secondary-action" type="button" onClick={onOpen} disabled={busy}>
            Open Folder
          </button>
        </div>
        <p className="action-note">Your operating system will ask you to choose a folder.</p>

        {error ? (
          <p className="notice error-notice" role="alert">
            {error}
          </p>
        ) : null}

        <section className="recent-section" aria-labelledby="recent-heading">
          <div className="section-rule">
            <h2 id="recent-heading">Recent Courses</h2>
            <span>{recent.length}</span>
          </div>
          {recent.length === 0 ? (
            <p className="empty-note">Workspaces you open will appear here.</p>
          ) : (
            <ul className="recent-list">
              {recent.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => void onOpenRecent(item.id)}
                    disabled={busy}
                  >
                    <span>{item.name}</span>
                    <small>{item.path}</small>
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
      lectures: splitLines(lectures).map((lectureTitle) => ({ title: lectureTitle })),
    });
  }

  return (
    <main className="setup-main">
      <section className="setup-intro" aria-labelledby="setup-title">
        <p className="eyebrow">New Course · {workspace.name}</p>
        <h1 id="setup-title">Plan the shape before the slides.</h1>
        <p className="lede">
          Start with the shared intent and an ordered Lecture spine. You can refine both later.
        </p>
        <p className="workspace-location">{workspace.path}</p>
      </section>

      <form className="course-form" onSubmit={submit} aria-busy={busy}>
        <div className="field wide-field">
          <label htmlFor="course-title">Course title</label>
          <input
            id="course-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            required
            maxLength={200}
          />
        </div>
        <div className="field wide-field">
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
        <div className="field">
          <label htmlFor="course-goals">Course goals (optional)</label>
          <textarea
            id="course-goals"
            value={goals}
            onChange={(event) => setGoals(event.target.value)}
            aria-describedby="goals-help"
            rows={5}
          />
          <small id="goals-help">Broad teaching intentions, one per line</small>
        </div>
        <div className="field">
          <label htmlFor="course-outcomes">Learning outcomes (optional)</label>
          <textarea
            id="course-outcomes"
            value={outcomes}
            onChange={(event) => setOutcomes(event.target.value)}
            aria-describedby="outcomes-help"
            rows={5}
          />
          <small id="outcomes-help">What learners should be able to do, one per line</small>
        </div>
        <div className="field wide-field lecture-field">
          <label htmlFor="course-lectures">Lectures in teaching order</label>
          <textarea
            id="course-lectures"
            value={lectures}
            onChange={(event) => setLectures(event.target.value)}
            aria-describedby="lectures-help"
            required
            rows={7}
          />
          <small id="lectures-help">One Lecture title per line. Each receives a stable identity.</small>
        </div>
        {error ? (
          <p className="notice error-notice wide-field" role="alert">
            {error}
          </p>
        ) : null}
        <div className="form-action wide-field">
          <button className="primary-action" type="submit" disabled={busy}>
            Create Course Plan
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

function CourseReadError({ workspace, error, busy, onRetry }: CourseReadErrorProps) {
  return (
    <main className="state-error-main">
      <section aria-labelledby="course-error-title">
        <p className="eyebrow">Course state · {workspace.name}</p>
        <h1 id="course-error-title">Course state needs attention.</h1>
        <p className="lede">
          Correct <code>course.yaml</code> in the Workspace, then ask Course Harness to read it
          again.
        </p>
        <p className="workspace-location">{workspace.path}</p>
        <p className="notice error-notice" role="alert">
          {error}
        </p>
        <button
          className="primary-action"
          type="button"
          disabled={busy}
          onClick={() => void onRetry()}
        >
          Retry reading Course
        </button>
      </section>
    </main>
  );
}

type LectureItemProps = {
  lecture: Lecture;
  index: number;
  count: number;
  busy: boolean;
  onRename: (identity: string, title: string) => Promise<void>;
  onMove: (index: number, direction: -1 | 1) => Promise<void>;
};

function LectureItem({ lecture, index, count, busy, onRename, onMove }: LectureItemProps) {
  const [title, setTitle] = useState(lecture.title);

  return (
    <li className="lecture-item">
      <div className="lecture-index" aria-hidden="true">
        {String(index + 1).padStart(2, "0")}
      </div>
      <div className="lecture-content">
        <label htmlFor={`lecture-${lecture.id}`}>Lecture {index + 1} title</label>
        <div className="rename-row">
          <input
            id={`lecture-${lecture.id}`}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={200}
          />
          <button
            type="button"
            onClick={() => void onRename(lecture.id, title)}
            disabled={busy || !title.trim() || title.trim() === lecture.title}
          >
            Save title
          </button>
        </div>
        <code>{lecture.id}</code>
      </div>
      <fieldset className="order-actions">
        <legend>Reorder {lecture.title}</legend>
        <button
          type="button"
          onClick={() => void onMove(index, -1)}
          disabled={busy || index === 0}
          aria-label={`Move ${lecture.title} earlier`}
        >
          ↑
        </button>
        <button
          type="button"
          onClick={() => void onMove(index, 1)}
          disabled={busy || index === count - 1}
          aria-label={`Move ${lecture.title} later`}
        >
          ↓
        </button>
      </fieldset>
    </li>
  );
}

type CourseViewProps = {
  workspace: Workspace;
  course: CoursePlan;
  files: WorkspaceEntry[];
  busy: boolean;
  error: string | null;
  onRename: (identity: string, title: string) => Promise<void>;
  onMove: (index: number, direction: -1 | 1) => Promise<void>;
};

function CourseView({
  workspace,
  course,
  files,
  busy,
  error,
  onRename,
  onMove,
}: CourseViewProps) {
  return (
    <main className="course-main">
      <header className="course-heading">
        <div>
          <p className="eyebrow">Syllabus · {workspace.name}</p>
          <h1>{course.title}</h1>
        </div>
        <p className="audience-copy">
          <span>For</span> {course.audience}
        </p>
      </header>

      {error ? (
        <p className="notice error-notice" role="alert">
          {error}
        </p>
      ) : null}

      <div className="course-grid">
        <section className="syllabus-panel" aria-labelledby="lectures-heading" aria-busy={busy}>
          <div className="section-rule">
            <h2 id="lectures-heading">Lecture spine</h2>
            <span>{course.lectures.length}</span>
          </div>
          <ol className="lecture-list">
            {course.lectures.map((lecture, index) => (
              <LectureItem
                key={lecture.id}
                lecture={lecture}
                index={index}
                count={course.lectures.length}
                busy={busy}
                onRename={onRename}
                onMove={onMove}
              />
            ))}
          </ol>
        </section>

        <aside className="course-sidebar">
          <section aria-labelledby="intent-heading">
            <div className="section-rule">
              <h2 id="intent-heading">Course intent</h2>
            </div>
            <h3>Goals</h3>
            <ul className="intent-list">
              {course.goals.map((goal) => (
                <li key={goal}>{goal}</li>
              ))}
            </ul>
            <h3>Outcomes</h3>
            <ul className="intent-list">
              {course.outcomes.map((outcome) => (
                <li key={outcome}>{outcome}</li>
              ))}
            </ul>
          </section>

          <section className="explorer" aria-labelledby="explorer-heading">
            <div className="section-rule">
              <h2 id="explorer-heading">Workspace</h2>
              <span>{files.length}</span>
            </div>
            <p className="workspace-location">{workspace.path}</p>
            {files.length === 0 ? (
              <p className="empty-note">This Workspace has no visible files yet.</p>
            ) : (
              <ul className="file-list">
                {files.map((entry) => (
                  <li key={entry.path}>
                    <span aria-hidden="true">{entry.kind === "directory" ? "▸" : "—"}</span>
                    {entry.path}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}

export function App() {
  const [workspace, setWorkspace] = useState<Workspace | null | undefined>(undefined);
  const [recent, setRecent] = useState<RecentWorkspace[]>([]);
  const [course, setCourse] = useState<CoursePlan | null | undefined>(undefined);
  const [files, setFiles] = useState<WorkspaceEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadWorkspace = useCallback(async (active: Workspace, signal?: AbortSignal) => {
    setWorkspace(active);
    setCourse(undefined);
    const [courseResponse, filesResponse] = await Promise.all([
      fetch("/api/course", { signal }),
      fetch("/api/workspace/files", { signal }),
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
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function initialize() {
      try {
        const workspaceResponse = await fetch("/api/workspace", { signal: controller.signal });
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
        await loadWorkspace((await workspaceResponse.json()) as Workspace, controller.signal);
      } catch (caught) {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "Course Harness could not start.");
        }
      }
    }

    void initialize();
    return () => controller.abort();
  }, [loadWorkspace]);

  async function runMutation(action: () => Promise<void>, fallbackMessage: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : fallbackMessage);
    } finally {
      setBusy(false);
    }
  }

  async function selectWorkspace(endpoint: "new-course" | "open-folder") {
    await runMutation(async () => {
      const response = await fetch(`/api/launcher/${endpoint}`, { method: "POST" });
      if (response.status === 204) {
        return;
      }
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      await loadWorkspace((await response.json()) as Workspace);
    }, "The Workspace could not be opened.");
  }

  async function openRecent(identity: string) {
    await runMutation(async () => {
      const response = await fetch(`/api/launcher/recent/${encodeURIComponent(identity)}/open`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      await loadWorkspace((await response.json()) as Workspace);
    }, "The Workspace could not be reopened.");
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
      setCourse((await response.json()) as CoursePlan);
      const filesResponse = await fetch("/api/workspace/files");
      if (filesResponse.ok) {
        setFiles((await filesResponse.json()) as WorkspaceEntry[]);
      }
    }, "The Course could not be created.");
  }

  async function renameLecture(identity: string, title: string) {
    await runMutation(async () => {
      const response = await fetch(`/api/course/lectures/${encodeURIComponent(identity)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      setCourse((await response.json()) as CoursePlan);
    }, "The Lecture could not be renamed.");
  }

  async function moveLecture(index: number, direction: -1 | 1) {
    if (!course) {
      return;
    }
    const identities = course.lectures.map((lecture) => lecture.id);
    const target = index + direction;
    [identities[index], identities[target]] = [identities[target], identities[index]];
    await runMutation(async () => {
      const response = await fetch("/api/course/lectures/order", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lecture_ids: identities }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      setCourse((await response.json()) as CoursePlan);
    }, "The Lectures could not be reordered.");
  }

  async function retryCourse() {
    if (!workspace) {
      return;
    }
    await runMutation(() => loadWorkspace(workspace), "The Course could not be read.");
  }

  async function returnToLauncher() {
    await runMutation(async () => {
      const closeResponse = await fetch("/api/workspace/close", { method: "POST" });
      if (!closeResponse.ok) {
        throw new Error(await responseError(closeResponse));
      }
      setWorkspace(null);
      setCourse(null);
      setFiles([]);

      const recentResponse = await fetch("/api/launcher/recent");
      if (!recentResponse.ok) {
        throw new Error(await responseError(recentResponse));
      }
      setRecent((await recentResponse.json()) as RecentWorkspace[]);
    }, "The Workspace Launcher could not be opened.");
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-actions">
          <p className="local-status">
            <span aria-hidden="true" /> {workspace ? "One Workspace active" : "Local session"}
          </p>
          {workspace ? (
            <button
              className="launcher-return"
              type="button"
              disabled={busy}
              onClick={() => void returnToLauncher()}
            >
              Back to Workspace Launcher
            </button>
          ) : null}
        </div>
      </header>

      {workspace === undefined ? (
        <main className="loading-main" aria-busy="true">
          <p>Starting Course Harness…</p>
        </main>
      ) : workspace === null ? (
        <Launcher
          recent={recent}
          busy={busy}
          error={error}
          onNew={() => selectWorkspace("new-course")}
          onOpen={() => selectWorkspace("open-folder")}
          onOpenRecent={openRecent}
        />
      ) : course === undefined && error ? (
        <CourseReadError workspace={workspace} error={error} busy={busy} onRetry={retryCourse} />
      ) : course === undefined ? (
        <main className="loading-main" aria-busy="true">
          <p>Reading your Course…</p>
        </main>
      ) : course === null ? (
        <CourseSetup
          workspace={workspace}
          busy={busy}
          error={error}
          onCreate={createCourse}
        />
      ) : (
        <CourseView
          workspace={workspace}
          course={course}
          files={files}
          busy={busy}
          error={error}
          onRename={renameLecture}
          onMove={moveLecture}
        />
      )}

      <footer>
        <p>Local-first course authoring</p>
        <p>{workspace ? workspace.name : "No Workspace selected"}</p>
      </footer>
    </div>
  );
}
