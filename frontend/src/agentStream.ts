import { responseError } from "./api";

export type AgentInterrupt = {
  id: string;
  message?: string;
};

type AgentStreamHandlers = {
  onTextStart: (messageId: string) => void;
  onText: (delta: string) => void;
  onActivity: (messageId: string, title: string, detail: string) => void;
  onState: (snapshot: unknown) => Promise<void>;
  onInterrupt: (interrupt: AgentInterrupt) => void;
};

export async function streamAgentRun(payload: object, handlers: AgentStreamHandlers) {
  const response = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
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
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      const event = JSON.parse(dataLine.slice(6)) as Record<string, unknown>;
      if (event.type === "TEXT_MESSAGE_START") {
        handlers.onTextStart(event.messageId as string);
      } else if (event.type === "TEXT_MESSAGE_CONTENT") {
        handlers.onText(event.delta as string);
      } else if (event.type === "ACTIVITY_SNAPSHOT") {
        const content = event.content as { title: string; detail: string };
        handlers.onActivity(event.messageId as string, content.title, content.detail);
      } else if (event.type === "STATE_SNAPSHOT") {
        await handlers.onState(event.snapshot);
      } else if (event.type === "RUN_FINISHED") {
        const outcome = event.outcome as { interrupts?: AgentInterrupt[] } | undefined;
        outcome?.interrupts?.forEach(handlers.onInterrupt);
      } else if (event.type === "RUN_ERROR") {
        throw new Error((event.message as string) || "The Course Agent run failed.");
      }
    }
    if (done) break;
  }
}
