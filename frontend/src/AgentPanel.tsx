import { type FormEvent, useEffect, useState } from "react";

import { type AgentInterrupt, streamAgentRun } from "./agentStream";
import { ProviderSetup } from "./ProviderSetup";

export type CoursePlan = {
  schema_version: 1;
  id: string;
  title: string;
  audience: string;
  goals: string[];
  outcomes: string[];
  lectures: { id: string; title: string; group: string | null }[];
};

export type ChatMessage = { id: string; role: "user" | "assistant"; content: string };

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

type Activity = { id: string; title: string; detail: string };

type AgentPanelProps = {
  initialMessages: ChatMessage[];
  initialApproval: AgentInterrupt | null;
  initialProvider: ProviderStatus;
  onCourseChange: (course: CoursePlan) => Promise<void>;
  onRunningChange: (running: boolean) => void;
};

function updateAssistantMessage(
  messages: ChatMessage[],
  identity: string,
  delta: string,
): ChatMessage[] {
  const index = messages.findIndex((message) => message.id === identity);
  if (index === -1) return [...messages, { id: identity, role: "assistant", content: delta }];
  return messages.map((message, messageIndex) =>
    messageIndex === index ? { ...message, content: message.content + delta } : message,
  );
}

function approvalSummary(interrupt: AgentInterrupt): string {
  const match = interrupt.message?.match(/replace_course_plan\((.*)\)\?$/);
  if (!match) return "Review the proposed Course Plan before it changes this Workspace.";
  try {
    const proposal = JSON.parse(match[1]) as { command?: { title?: string; lectures?: unknown[] } };
    const command = proposal.command;
    if (!command) return "Review the proposed Course Plan before it changes this Workspace.";
    return `${command.title ?? "Course Plan"} · ${command.lectures?.length ?? 0} Lectures`;
  } catch {
    return "Review the proposed Course Plan before it changes this Workspace.";
  }
}

export function AgentPanel({
  initialMessages,
  initialApproval,
  initialProvider,
  onCourseChange,
  onRunningChange,
}: AgentPanelProps) {
  const [provider, setProvider] = useState(initialProvider);
  const [messages, setMessages] = useState(initialMessages);
  const [prompt, setPrompt] = useState("");
  const [activities, setActivities] = useState<Activity[]>([]);
  const [approval, setApproval] = useState<AgentInterrupt | null>(initialApproval);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setProvider(initialProvider), [initialProvider]);
  useEffect(() => setMessages(initialMessages), [initialMessages]);
  useEffect(() => setApproval(initialApproval), [initialApproval]);
  useEffect(() => onRunningChange(running), [onRunningChange, running]);
  useEffect(() => () => onRunningChange(false), [onRunningChange]);

  async function run(messagesForRun: ChatMessage[], resume?: object[]) {
    setActivities([]);
    setError(null);
    setRunning(true);
    let assistantId: string | null = null;
    try {
      await streamAgentRun(
        {
          threadId: "course-agent",
          runId: crypto.randomUUID(),
          state: {},
          messages: messagesForRun,
          tools: [],
          context: [],
          forwardedProps: {},
          ...(resume ? { resume } : {}),
        },
        {
          onTextStart: (identity) => {
            assistantId = identity;
          },
          onText: (delta) => {
            if (assistantId) {
              const identity = assistantId;
              setMessages((current) => updateAssistantMessage(current, identity, delta));
            }
          },
          onActivity: (id, title, detail) =>
            setActivities((current) => [...current, { id, title, detail }]),
          onState: async (snapshot) => {
            const state = snapshot as { course?: CoursePlan };
            if (state.course) await onCourseChange(state.course);
          },
          onInterrupt: setApproval,
        },
      );
      const transcriptResponse = await fetch("/api/chat");
      if (transcriptResponse.ok) {
        const transcript = (await transcriptResponse.json()) as {
          messages: ChatMessage[];
          approval: AgentInterrupt | null;
        };
        setMessages(transcript.messages);
        setApproval(transcript.approval);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The Course Agent run failed.");
    } finally {
      setRunning(false);
    }
  }

  function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = prompt.trim();
    if (!content || running) return;
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
    };
    setMessages((current) => [...current, userMessage]);
    setPrompt("");
    setApproval(null);
    void run([userMessage]);
  }

  function resolveApproval(approved: boolean) {
    if (!approval || running) return;
    const pending = approval;
    setApproval(null);
    void run([], [
      {
        interruptId: pending.id,
        status: "resolved",
        payload: { approved },
      },
    ]);
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

      {approval ? (
        <section className="approval-card" aria-labelledby="approval-heading">
          <p className="section-kicker">Approval checkpoint</p>
          <h3 id="approval-heading">Apply this Course Plan?</h3>
          <p>{approvalSummary(approval)}</p>
          <div>
            <button type="button" className="secondary-action" onClick={() => resolveApproval(false)}>
              Keep current plan
            </button>
            <button type="button" className="primary-action" onClick={() => resolveApproval(true)}>
              Approve plan
            </button>
          </div>
        </section>
      ) : null}

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
          disabled={running || approval !== null}
          aria-describedby="course-agent-help"
        />
        <div>
          <small id="course-agent-help">Course changes wait for your approval.</small>
          <button
            className="primary-action compact-action"
            type="submit"
            disabled={running || approval !== null || !prompt.trim()}
          >
            Send message
          </button>
        </div>
      </form>
    </aside>
  );
}
