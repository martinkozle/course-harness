import { useState } from "react";
import type { ResourceState } from "./models";
import { responseError } from "./api";

type LibraryViewProps = {
  resources: ResourceState[];
  onResourcesChange: (resources: ResourceState[]) => void;
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(1)} ${units[index]}`;
}

function statusBadge(status: string): string {
  const lookup: Record<string, string> = {
    unprocessed: "Awaiting processing",
    processing: "Processing…",
    ready: "Ready",
    failed: "Failed",
    retrying: "Retrying",
  };
  return lookup[status] ?? status;
}

export function LibraryView({ resources, onResourcesChange }: LibraryViewProps) {
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState<Set<string>>(new Set());

  const statusCounts = resources.reduce(
    (counts, item) => {
      counts[item.status] = (counts[item.status] ?? 0) + 1;
      return counts;
    },
    {} as Record<string, number>,
  );

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/resources/upload", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const updated = await fetch("/api/resources");
      if (!updated.ok) throw new Error(await responseError(updated));
      onResourcesChange((await updated.json()) as ResourceState[]);
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function handleProcess(resourceId: string) {
    setProcessing((current) => new Set(current).add(resourceId));
    try {
      const response = await fetch(`/api/resources/${encodeURIComponent(resourceId)}/process`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await responseError(response));
      const updated = await fetch("/api/resources");
      if (!updated.ok) throw new Error(await responseError(updated));
      onResourcesChange((await updated.json()) as ResourceState[]);
    } finally {
      setProcessing((current) => {
        const next = new Set(current);
        next.delete(resourceId);
        return next;
      });
    }
  }

  async function handleClearCache() {
    try {
      await fetch("/api/resources/cache", { method: "DELETE" });
    } catch {
      // Cache clearing failure is non-blocking
    }
  }

  return (
    <main className="page-main library-main" aria-labelledby="library-heading">
      <header className="page-heading library-heading">
        <p className="eyebrow">Library</p>
        <h1 id="library-heading">Resources</h1>
        <p className="lede">
          Files, uploads, and attachments available to the Course Agent for research and grounding.
        </p>
      </header>

      <section className="content-section library-section" aria-label="Resource library">
        <div className="content-section-heading">
          <div>
            <p className="section-kicker">Global library</p>
            <h2>Registered resources</h2>
          </div>
          <div className="section-actions">
            <button
              className="quiet-action"
              type="button"
              onClick={handleClearCache}
            >
              Clear cache
            </button>
            <label className="primary-action compact-action upload-label">
              Upload file
              <input
                type="file"
                hidden
                onChange={(e) => void handleUpload(e)}
                disabled={uploading}
              />
            </label>
          </div>
        </div>

        <div className="library-stats" aria-live="polite">
          <span className="count-badge">{resources.length}</span>
          <span className="stats-detail">
            {statusCounts.ready ?? 0} ready
            {statusCounts.unprocessed ? ` · ${statusCounts.unprocessed} unprocessed` : ""}
            {statusCounts.failed ? ` · ${statusCounts.failed} failed` : ""}
          </span>
        </div>

        {resources.length === 0 && !uploading ? (
          <div className="empty-state">
            <p>No resources registered yet.</p>
            <span>Upload a file or register a local path to add material to the Library.</span>
          </div>
        ) : (
          <ul className="library-list">
            {resources.map((resource) => (
              <li key={resource.resource_id}>
                <div className="library-meta">
                  <strong className={`status-badge status-${resource.status}`}>
                    {statusBadge(resource.status)}
                  </strong>
                  <span className="library-name">
                    {resource.location ?? resource.resource_id}
                  </span>
                </div>
                {resource.snapshot ? (
                  <p className="library-snapshot">
                    {formatBytes(resource.snapshot.byte_count)}
                    {" · "}
                    {resource.snapshot.content_hash.slice(0, 8)}…
                  </p>
                ) : null}
                {resource.error ? (
                  <p className="library-error" role="alert">
                    {resource.error}
                  </p>
                ) : null}
                {resource.status === "unprocessed" || resource.status === "failed" ? (
                  <button
                    className="compact-action secondary-action"
                    type="button"
                    onClick={() => void handleProcess(resource.resource_id)}
                    disabled={processing.has(resource.resource_id)}
                  >
                    {processing.has(resource.resource_id) ? "Processing…" : "Process"}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        {uploading ? (
          <p className="empty-note" aria-live="assertive">
            Uploading file…
          </p>
        ) : null}
      </section>
    </main>
  );
}
