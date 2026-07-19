import { type FormEvent, useState } from "react";

import { responseError } from "./api";
import type { ProviderStatus } from "./AgentPanel";

export function ProviderSetup({
  onConfigured,
}: {
  onConfigured: (status: ProviderStatus) => void;
}) {
  const [kind, setKind] = useState<"openrouter" | "openai-compatible" | "anthropic">(
    "openrouter",
  );
  const [model, setModel] = useState("openai/gpt-oss-20b:free");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/provider", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind,
          model: model.trim(),
          api_key: apiKey,
          base_url: kind === "openai-compatible" ? baseUrl.trim() : null,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      onConfigured((await response.json()) as ProviderStatus);
      setApiKey("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The provider could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="provider-form" onSubmit={submit} aria-busy={busy}>
      <div className="agent-panel-heading">
        <p className="section-kicker">Private connection</p>
        <h2 id="agent-heading">Connect the Course Agent</h2>
        <p>Your credential stays in Course Harness settings, outside this Course folder.</p>
      </div>

      <div className="field provider-control">
        <label htmlFor="provider-kind">Provider</label>
        <select
          id="provider-kind"
          value={kind}
          onChange={(event) => setKind(event.target.value as typeof kind)}
        >
          <option value="openrouter">OpenRouter</option>
          <option value="anthropic">Anthropic</option>
          <option value="openai-compatible">OpenAI-compatible</option>
        </select>
      </div>

      <div className="field provider-control">
        <label htmlFor="provider-model">Model</label>
        <input
          id="provider-model"
          value={model}
          onChange={(event) => setModel(event.target.value)}
          required
          autoComplete="off"
        />
      </div>

      {kind === "openai-compatible" ? (
        <div className="field provider-control">
          <label htmlFor="provider-url">API base URL</label>
          <input
            id="provider-url"
            type="url"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://provider.example/v1"
            required
            aria-describedby="provider-url-help"
          />
          <small id="provider-url-help">HTTPS, or HTTP on this computer.</small>
        </div>
      ) : null}

      <div className="field provider-control">
        <label htmlFor="provider-key">API key</label>
        <input
          id="provider-key"
          type="password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          required
          autoComplete="off"
        />
      </div>

      <p className="provider-control" id="capability-help">
        Saving verifies authentication, tools, typed output, streaming, context, and image input
        support reported by the provider.
      </p>

      {error ? (
        <p className="notice error-notice provider-control" role="alert">
          {error}
        </p>
      ) : null}

      <button className="primary-action provider-control" type="submit" disabled={busy}>
        {busy ? "Verifying…" : "Save connection"}
      </button>
    </form>
  );
}
