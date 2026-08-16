# MCPJam Inspector: agent awareness of the frontend

Research date: 2026-08-16. Primary source inspected: MCPJam Inspector commit [`dd212de`](https://github.com/MCPJam/inspector/tree/dd212decbd6db8b50348c9a543307928c7986857).

## Finding

MCPJam does not give its agent a continuous, unrestricted picture of the browser. It combines two deliberately different channels:

1. **Small push context on every user turn.** At send time, the browser attaches the current route, resolved screen, selected server names, and a timestamp to the user's message. It is rebuilt for every turn because the user may have navigated since the previous one. MCPJam explicitly treats this as orientation, not a state dump; the timestamp lets the model distinguish old context blocks from the newest one. The context rides on the append-only user message rather than changing the system prompt. ([context builder](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/ui-context-snapshot.ts#L1-L52), [message attachment](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/hooks/use-mcpjam-agent-session.ts#L545-L552), [wire contract and validation](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/shared/ui-context.ts#L1-L124))
2. **Detailed pull context on demand.** `ui_snapshot_app` is a read-only tool that reports the active route, selected/connected servers, and snapshots from mounted surfaces. A caller may request one surface or the whole app. Reading does not navigate to or mount a screen, which is why it can honestly bypass mutation approval. ([tool definition](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/groups/core.ts#L271-L303), [whole-app handler](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/App.tsx#L3523-L3600))

This is the central pattern worth copying: **push cheap identity and selection every turn; pull rich screen state only when the agent needs it.**

## How the agent changes what the user sees

The model receives typed `ui_*` tools such as `ui_navigate`, `ui_select_server`, `ui_set_app_context`, `ui_open_playground`, and `ui_select_tool`. These are advertised to the model by the server as no-execute tools, then fulfilled in the open browser page. The browser resolves a tool only if it is registered, invokes a typed command/action handler, and sends the result back to resume the model turn. They are not DOM-click automation and are not exposed to arbitrary browser-native agents. ([browser-side registry and transport boundary](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/ui-tools-registry.ts#L1-L28), [navigation and context tools](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/groups/core.ts#L141-L269))

There are two scopes. A small global catalog remains available everywhere and may navigate to its target. Screen-specific tools, handlers, and snapshot providers exist only while that surface is mounted; stable wrappers read the latest committed React state and registration is removed on unmount. This prevents the agent from acting through stale or disabled UI state. ([surface bridge](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/use-surface-agent-bridge.ts#L1-L39), [registration lifecycle](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/use-surface-agent-bridge.ts#L83-L154))

## Synchronization and safeguards

- One app-level snapshot handler aggregates per-surface providers; surfaces do not compete to answer the same command. Providers are read-only, mounted-state scoped, limited to 8 KiB, timed out after two seconds, and failure-isolated so one broken surface does not erase all orientation. ([snapshot registry](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/surface-snapshot-registry.ts#L1-L39), [budgets and isolation](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/surface-snapshot-registry.ts#L69-L199))
- Tool metadata distinguishes read-only, reversible, destructive, idempotent, and open-world operations. With strict approval enabled every mutation pauses; otherwise destructive actions still pause. For example, navigation is non-destructive but non-idempotent, while snapshot is read-only. ([tool annotations](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/lib/webmcp/groups/core.ts#L141-L176), [first-party approval behavior](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/docs/inspector/home.mdx#L75-L122))
- The server validates the browser-supplied tool catalog and resolves name collisions in favor of a real MCP server tool, so the UI layer cannot accidentally shadow a server capability. ([server validation](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/server/utils/chat-v2-orchestration.ts#L334-L433), [collision boundary](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/server/utils/chat-v2-orchestration.ts#L1172-L1198))
- Commands use application actions/state rather than synthesized DOM events, so agent and human interactions share one state-transition path. ([App command handler commentary](https://github.com/MCPJam/inspector/blob/dd212decbd6db8b50348c9a543307928c7986857/mcpjam-inspector/client/src/App.tsx#L3602-L3606))

## Application to Course Harness

Course Harness currently has the beginning of the push half: selecting a Lecture or Slide replaces a visible chat-context string, and that string is prepended to the next user message and then cleared. ([Lecture selection](../../frontend/src/PresentationView.tsx#L569-L583), [Slide selection](../../frontend/src/PresentationView.tsx#L922-L930), [message send](../../frontend/src/AgentPanel.tsx#L441-L455)) This validates the decision that Authoring selection should follow the user's browsing.

The scalable next shape should be:

1. Replace the prose-only pending context internally with a typed `AuthoringViewContext`: Workspace identity, `lecture_id`, optional `slide_id`, display labels, and an observation timestamp. Keep IDs for tool targeting and labels for model/user readability.
2. Attach that small context automatically to **every** Course Agent turn while the merged Authoring view is active. The visible removable chip can suppress the next-turn attachment, but it should not be the authoritative source of selection state.
3. Add one read-only Course Agent tool such as `inspect_authoring_view` that returns current Lecture/Slide selection plus bounded visible state (for example slide title, layout, block summaries, and validation state). Do not send full Presentation content every turn.
4. Later, add a very small typed UI action set only where it improves collaboration: `focus_lecture`, `focus_slide`, and perhaps `show_slide_list`. These should change the same React selection state as user clicks, be immediately visible, and return the committed selection. They must never accept filesystem paths or bypass the existing Course Workspace HTTP boundary.
5. Keep Course mutations in existing domain tools and approval rules. A UI focus action is reversible browser state; editing/archiving/deleting/exporting remains a separate domain operation. This preserves the repository invariant that the Python package owns the HTTP boundary and avoids turning frontend control into a second mutation API.

### Recommendation

Adopt the two-layer context model, but implement only its narrow Authoring subset now. The immediate spec should say: **Lecture/Slide selection automatically follows browsing; each message receives typed current-selection orientation; the Course Agent may inspect the current Authoring view through one bounded read-only tool.** Agent-driven navigation can be a later tracer bullet after that observation contract is stable.

This is a better fit than making the removable prose chip carry all semantics: it keeps the agent synchronized even after ordinary browsing, preserves immutable IDs for targeting, avoids large repeated prompts, and leaves authoritative Course changes on the existing backend-owned tool boundary.

## Project decision

Course Harness will use MCPJam as directional inspiration, but will not adopt its UI tool or snapshot architecture for the first proof of concept. Lecture, Slide, and content-block selections continue to produce a visible, removable string context for the next user message. Agent-driven navigation and a separate typed frontend-view protocol remain out of scope until concrete user value justifies the additional React synchronization surface.
