import { type FormEvent, useEffect, useState } from "react";

export type CoursePlan = {
  schema_version: 1;
  id: string;
  title: string;
  audience: string;
  goals: string[];
  outcomes: string[];
  lectures: { id: string; title: string; group: string | null }[];
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type ProviderCapabilities = {
  tool_calling: boolean;
  structured_output: boolean;
  streaming: boolean;
  context_window: number;
  vision: boolean;
};

export type ProviderStatus =
  | { configured: false }
  | {
      configured: true;
      kind: "openrouter" | "openai-compatible" | "anthropic";
      model: string;
      base_url: string;
      capabilities: ProviderCapabilities;
      diagnostics: string[];
    };

type Activity = {
  id: string;
  title: string;
  detail: string;
};

type AgentPanelProps = {
  course: CoursePlan | null;
  initialMessages: ChatMessage[];
  initialProvider: ProviderStatus;
  onCourseChange: (course: CoursePlan) => Promise<void>;
};

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? "Course Harness could not complete that action.";
  } catch {
    return "Course Harness could not complete that action.";
  }
}

function updateAssistantMessage(
  messages: ChatMessage[],
  identity: string,
  delta: string,
): ChatMessage[] {
  const index = messages.findIndex((message) => message.id === identity);
  if (index === -1) {
    return [...messages, { id: identity, role: "assistant", content: delta }];
  }
  return messages.map((message, messageIndex) =>
    messageIndex === index ? { ...message, content: message.content + delta } : message,
  );
}

function ProviderSetup({ onConfigured }: { onConfigured: (status: ProviderStatus) => void }) {
  const [kind, setKind] = useState<"openrouter" | "openai-compatible" | "anthropic">(
    "openrouter",
  );
  const [model, setModel] = useState("openai/gpt-oss-20b:free");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [contextWindow, setContextWindow] = useState(131072);
  const [toolCalling, setToolCalling] = useState(true);
  const [structuredOutput, setStructuredOutput] = useState(true);
  const [streaming, setStreaming] = useState(true);
  const [vision, setVision] = useState(false);
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
          capabilities: {
            tool_calling: toolCalling,
            structured_output: structuredOutput,
            streaming,
            context_window: contextWindow,
            vision,
          },
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

      <fieldset className="capability-fields provider-control">
        <legend>Confirmed model capabilities</legend>
        <p id="capability-help">
          Course planning requires tools, typed output, streaming, and at least 16,384 context
          tokens.
        </p>
        <label htmlFor="context-window">
          Context window
          <input
            id="context-window"
            type="number"
            min={16384}
            step={1024}
            value={contextWindow}
            onChange={(event) => setContextWindow(event.target.valueAsNumber)}
            required
            aria-describedby="capability-help"
          />
        </label>
        <label className="check-field" htmlFor="provider-vision">
          <input
            id="provider-vision"
            type="checkbox"
            checked={vision}
            onChange={(event) => setVision(event.target.checked)}
          />
          This model accepts images
        </label>
        <label className="check-field" htmlFor="provider-tools">
          <input
            id="provider-tools"
            type="checkbox"
            checked={toolCalling}
            onChange={(event) => setToolCalling(event.target.checked)}
          />
          Tool calling
        </label>
        <label className="check-field" htmlFor="provider-structured-output">
          <input
            id="provider-structured-output"
            type="checkbox"
            checked={structuredOutput}
            onChange={(event) => setStructuredOutput(event.target.checked)}
          />
          Structured output
        </label>
        <label className="check-field" htmlFor="provider-streaming">
          <input
            id="provider-streaming"
            type="checkbox"
            checked={streaming}
            onChange={(event) => setStreaming(event.target.checked)}
          />
          Streaming responses
        </label>
      </fieldset>

      {error ? (
        <p className="notice error-notice provider-control" role="alert">
          {error}
        </p>
      ) : null}

      <button className="primary-action provider-control" type="submit" disabled={busy}>
        Save connection
      </button>
    </form>
  );
}

export function AgentPanel({
  course,
  initialMessages,
  initialProvider,
  onCourseChange,
}: AgentPanelProps) {
  const [provider, setProvider] = useState(initialProvider);
  const [messages, setMessages] = useState(initialMessages);
  const [prompt, setPrompt] = useState("");
  const [activities, setActivities] = useState<Activity[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setProvider(initialProvider), [initialProvider]);
  useEffect(() => setMessages(initialMessages), [initialMessages]);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = prompt.trim();
    if (!content || running) {
      return;
    }
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
    };
    setMessages((current) => [...current, userMessage]);
    setPrompt("");
    setActivities([]);
    setError(null);
    setRunning(true);

    try {
      const response = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          threadId: "course-agent",
          runId: crypto.randomUUID(),
          state: { course },
          messages: [userMessage],
          tools: [],
          context: [],
          forwardedProps: {},
        }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      if (!response.body) {
        throw new Error("The Course Agent did not return a stream.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let assistantId: string | null = null;
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
          if (!dataLine) {
            continue;
          }
          const agentEvent = JSON.parse(dataLine.slice(6)) as Record<string, unknown>;
          if (agentEvent.type === "TEXT_MESSAGE_START") {
            assistantId = agentEvent.messageId as string;
          } else if (agentEvent.type === "TEXT_MESSAGE_CONTENT" && assistantId) {
            const delta = agentEvent.delta as string;
            const messageId = assistantId;
            setMessages((current) => updateAssistantMessage(current, messageId, delta));
          } else if (agentEvent.type === "ACTIVITY_SNAPSHOT") {
            const activityContent = agentEvent.content as { title: string; detail: string };
            setActivities((current) => [
              ...current,
              {
                id: agentEvent.messageId as string,
                title: activityContent.title,
                detail: activityContent.detail,
              },
            ]);
          } else if (agentEvent.type === "STATE_SNAPSHOT") {
            const snapshot = agentEvent.snapshot as { course?: CoursePlan };
            if (snapshot.course) {
              await onCourseChange(snapshot.course);
            }
          } else if (agentEvent.type === "RUN_ERROR") {
            throw new Error((agentEvent.message as string) || "The Course Agent run failed.");
          }
        }
        if (done) {
          break;
        }
      }

      const transcriptResponse = await fetch("/api/chat");
      if (transcriptResponse.ok) {
        const transcript = (await transcriptResponse.json()) as { messages: ChatMessage[] };
        setMessages(transcript.messages);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The Course Agent run failed.");
    } finally {
      setRunning(false);
    }
  }

  if (!provider.configured) {
    return (
      <aside className="agent-panel" id="agent" aria-labelledby="agent-heading">
        <ProviderSetup onConfigured={setProvider} />
      </aside>
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
            {running ? "Working" : "Ready"}
          </span>
        </div>
        <p>
          {provider.model} · {provider.kind}
        </p>
      </div>

      <ol className="chat-messages" aria-label="Course Agent conversation">
        {messages.length === 0 ? (
          <li className="chat-empty">
            Describe the audience, the change you want learners to make, and how much teaching time
            you have.
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

      <div className="agent-activity" aria-live="polite" aria-atomic="true">
        {activities.map((activity) => (
          <p key={activity.id}>
            <strong>{activity.title}</strong>
            <span>{activity.detail}</span>
          </p>
        ))}
        {running && activities.length === 0 ? <p>Reading your direction…</p> : null}
      </div>

      {error ? (
        <p className="notice error-notice" role="alert">
          {error}
        </p>
      ) : null}

      <form className="chat-composer" onSubmit={sendMessage} aria-busy={running}>
        <label htmlFor="course-agent-message">Message the Course Agent</label>
        <textarea
          id="course-agent-message"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          rows={4}
          placeholder="Create a four-Lecture Course for…"
          maxLength={4000}
          disabled={running}
          aria-describedby="course-agent-help"
        />
        <div>
          <small id="course-agent-help">
            Course changes are validated before they are saved.
          </small>
          <button
            className="primary-action compact-action"
            type="submit"
            disabled={running || !prompt.trim()}
          >
            Send message
          </button>
        </div>
      </form>
    </aside>
  );
}
