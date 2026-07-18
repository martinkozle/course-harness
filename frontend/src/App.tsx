import { useEffect, useState } from "react";

type Workspace = {
  name: string;
  path: string;
};

type WorkspaceState =
  | { status: "loading" }
  | { status: "ready"; workspace: Workspace }
  | { status: "error" };

function WorkspaceMark() {
  return (
    <svg aria-hidden="true" className="workspace-mark" viewBox="0 0 72 72">
      <path d="M12 22h18l6 7h24v27H12z" />
      <path d="M20 43h32M20 50h22" />
    </svg>
  );
}

export function App() {
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    async function loadWorkspace() {
      try {
        const response = await fetch("/api/workspace", { signal: controller.signal });
        if (!response.ok) {
          throw new Error("Workspace request failed");
        }
        const workspace = (await response.json()) as Workspace;
        setWorkspaceState({ status: "ready", workspace });
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setWorkspaceState({ status: "error" });
        }
      }
    }

    void loadWorkspace();
    return () => controller.abort();
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="Course Harness home">
          <span className="wordmark-glyph" aria-hidden="true">
            CH
          </span>
          <span>Course Harness</span>
        </a>
        <p className="local-status">
          <span aria-hidden="true" /> Local Workspace
        </p>
      </header>

      <main>
        <section className="hero" aria-labelledby="page-title">
          <div className="hero-copy">
            <p className="eyebrow">Course Workspace</p>
            <h1 id="page-title">Your course starts here</h1>
            <p className="lede">
              Build the plan, gather trusted sources, and shape each lecture from one place you
              control.
            </p>
          </div>

          <div className="workspace-route" aria-hidden="true">
            <span className="route-origin" />
            <span className="route-line" />
            <span className="route-label">active</span>
          </div>

          <section className="workspace-card" aria-labelledby="workspace-heading" aria-live="polite">
            <div className="card-icon">
              <WorkspaceMark />
            </div>
            <div className="card-content">
              <p className="card-label" id="workspace-heading">
                Active Workspace
              </p>
              {workspaceState.status === "loading" ? (
                <p className="workspace-message">Finding your Workspace…</p>
              ) : workspaceState.status === "error" ? (
                <p className="workspace-message error-message">
                  The active Workspace could not be read. Restart Course Harness from the directory
                  you want to use.
                </p>
              ) : (
                <>
                  <p className="workspace-name">{workspaceState.workspace.name}</p>
                  <p className="workspace-path">{workspaceState.workspace.path}</p>
                </>
              )}
            </div>
            {workspaceState.status === "ready" ? <span className="ready-badge">Ready</span> : null}
          </section>

          <p className="boundary-note">
            Course Harness is connected only to the Workspace you selected for this session.
          </p>
        </section>
      </main>

      <footer>
        <p>Local-first course authoring</p>
        <p>Workspace connection established</p>
      </footer>
    </div>
  );
}
