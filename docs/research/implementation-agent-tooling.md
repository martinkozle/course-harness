# Implementation-agent tooling for Course Harness

Research date: 2026-07-14

## Recommendation

Do not equip the development agent with a large MCP collection. The minimal useful addition is **browser automation**, and even there the durable deliverable is ordinary Playwright tests checked into the repository. Keep local filesystem, Git, SQLite, package management, and PowerPoint work on the CLI or in project scripts. Add GitHub's official MCP only when issue/PR/Actions work becomes part of the workflow. Treat Context7 as optional convenience, not an authority.

This distinction matters:

- **Development-agent tools** help build and validate Course Harness and should generally not ship with it.
- **Product connectors** are capabilities Course Harness deliberately exposes to its Course Agent and users, with its own permissions, provenance, snapshots, and audit trail.

## Minimal development baseline

### 1. Playwright tests plus an optional Playwright agent interface

Browser automation materially helps with the React/AG-UI application: verifying streaming chat, run locking and steering, workspace state, slide-selection overlays, source ingestion states, template onboarding, cancellation, and release/export flows.

Microsoft's official `playwright-mcp` drives pages from structured accessibility snapshots rather than relying only on screenshots. Its own README now says coding agents may benefit more from **Playwright CLI plus skills**, because that is more token-efficient than loading MCP schemas and accessibility trees. Therefore:

1. Check in normal `@playwright/test` end-to-end tests and run them from Bun/package scripts in CI.
2. Give the agent a small repository skill describing how to start the stack, seed a temporary workspace, run focused tests, collect traces, and inspect screenshots.
3. Install Playwright MCP only when interactive UI exploration is valuable in the selected agent client; it is not required infrastructure.

The MCP server is explicitly **not a security boundary**. Its current catalog labels `browser_run_code_unsafe` as equivalent to remote code execution, so omit/disable that capability unless a narrowly scoped debugging task truly needs it. Use a fresh browser profile, test accounts, localhost-only services, and no unrelated authenticated tabs. Pin a reviewed version rather than using `@latest` in reproducible development configuration. [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp)

### 2. Chrome DevTools MCP only for diagnostics

Google's official Chrome DevTools MCP is complementary, not another default browser driver. It is particularly useful for performance traces and insights, network inspection, console/source-map debugging, and inspecting a live Chrome session. Its browser automation is Puppeteer-based, so enabling it alongside Playwright by default creates overlapping tools and more model choice ambiguity.

Add it temporarily when diagnosing AG-UI streaming, rendering/performance, browser memory, request waterfalls, or hard console/network failures. Its documentation warns that the MCP client can inspect, debug, and modify all data in the connected browser. It also collects usage statistics by default; `--no-usage-statistics` disables that, and `--no-performance-crux` disables CrUX URL lookups during traces. Use an isolated profile and headless/slim mode when sufficient; review telemetry and update-check settings before use. [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)

**Choice:** start with checked-in Playwright tests and optionally Playwright MCP/CLI. Do not configure both browser MCPs globally. Reach for Chrome DevTools MCP during a specific performance/debugging investigation.

### 3. Ordinary shell, `rg`, Git CLI, and project scripts

The coding agent already has sandboxed filesystem and shell access. A Filesystem MCP or Git MCP would mostly duplicate narrower, faster tools while expanding the tool schema and attack surface. The reference Filesystem MCP includes overwrite, edit, move, and directory operations; it can restrict access through MCP Roots and annotates destructive operations, but those are hints to the client rather than a replacement for the existing workspace sandbox and approval policy. [MCP Filesystem server](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md)

Use `rg`, patch-based edits, and Git CLI with repository-specific guardrails. Do not grant a development agent unrestricted Git commands merely because Git is part of Course Harness's product model. For the product itself, implement a narrow typed revision service (`diff`, `checkpoint`, `restore` with validation) rather than exposing shell Git to the Course Agent.

### 4. First-party documentation first; Context7 optional

Current official documentation and source repositories remain authoritative for FastAPI, Pydantic/Pydantic AI, AG-UI, React, Bun, uv, Ruff, ty, python-pptx, Docling, and Playwright. A repository research skill that routes questions to those primary sources is more reliable and auditable than silently depending on a documentation aggregator.

Context7 by Upstash can resolve libraries and retrieve version-oriented documentation, so it may reduce browsing friction for common libraries. Its CLI/skill mode may be leaner than permanently loading an MCP tool schema. However, queries are sent to Context7's external service; do not include private code, credentials, unpublished names, or sensitive course material. Its indexed snippets can be incomplete or stale, and its existence does not remove the need to verify consequential API and compatibility claims against first-party docs/source. Install it only if the agent repeatedly loses time finding current APIs. [Context7 documentation](https://context7.com/docs/resources/all-clients), [Context7 API guide](https://context7.com/docs/api-guide), [Context7 data privacy](https://context7.com/docs/security/data-privacy)

## Optional development tools

### GitHub MCP when remote collaboration starts

GitHub maintains the official GitHub MCP Server. It can expose scoped toolsets for repositories, issues, pull requests, Actions, and security capabilities. This is useful once the implementation uses GitHub issues/PRs or needs to inspect CI remotely; it adds little during purely local development. Enable only the needed toolsets and use a fine-grained token or GitHub App with the least permissions practical. Prefer read-only access until the user asks the agent to publish or mutate remote state. [Official GitHub MCP Server](https://github.com/github/github-mcp-server), [GitHub setup documentation](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server)

### SQLite inspection: CLI/script, not MCP

Course Harness may use SQLite for derived local indexes and runtime metadata, but canonical Course state lives in files. The former MCP reference SQLite server is archived and explicitly carries no ongoing security guarantees, and no credible current first-party replacement was found. A read-only diagnostic script or `sqlite3` queries against a disposable test database are clearer, deterministic, and easy to place in tests. A generic database MCP encourages ad-hoc mutations, adds broad schemas, and risks confusing derived state with authority. If a database explorer becomes desirable, point it only at a copied/read-only development database and never user data. [Archived MCP reference servers](https://github.com/modelcontextprotocol/servers-archived)

### Accessibility and visual checks

Add `@axe-core/playwright` or equivalent automated accessibility checks to selected browser tests, plus screenshot/visual-regression assertions for stable app chrome. These are test libraries rather than MCP servers and therefore run consistently in CI. Do not use screenshot pixel matching as the authority for PowerPoint fidelity across PowerPoint and LibreOffice renderers; use explicit renderer-specific golden fixtures and tolerances.

## PowerPoint and LibreOffice: scripts and adapters, not MCP

No credible first-party PowerPoint or LibreOffice MCP is needed for implementation. The required operations are deterministic and domain-specific:

- inspect Open XML and `python-pptx` objects;
- construct calibration decks and generated decks;
- invoke an installed renderer through a small platform adapter;
- compare rendered images and inspect placeholder/layout metadata;
- keep fixture templates and expected profiles in tests.

Wrap Microsoft PowerPoint automation (where installed) and LibreOffice headless conversion behind the same application interface. Detect capabilities and report them in the UI. Do not expose arbitrary Office macro execution, UNO objects, or shell commands to the Course Agent. MCP would not improve reproducibility here; a narrow typed adapter and fixture suite would.

## Capabilities that belong inside Course Harness

These are **product connectors/tools**, not development-agent MCP dependencies:

- bounded Course Source tools: discover, register, snapshot, process, search, and read Evidence;
- web/DOI paper discovery and retrieval;
- future Google Drive browsing/export with narrow OAuth scopes;
- future GitHub read tools for repositories used as course Sources;
- an MCP client connector for explicitly configured external resources/tools;
- an MCP server surface, later, for exposing Course state and Sources to trusted external agents;
- typed course mutation, validation, revision, template, and release tools.

Every external connector should preserve provider-native identity, create immutable snapshots when content enters the Course corpus, show what leaves the machine, and separate read/discovery permission from mutation permission. Do not inherit the development agent's MCP configuration or credentials into the product.

If Course Harness later implements an MCP client or server, use the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector) during protocol development. It is unnecessary before that boundary exists.

## Repository agent configuration worth adding

The implementation agent benefits more from concise project knowledge and executable workflows than from additional generic servers:

1. An `AGENTS.md` with architecture invariants, canonical/derived state boundaries, commands, forbidden shortcuts, and the rule that UI text says **user**, not “professor.”
2. A browser-testing skill/script documenting one-command startup, deterministic fake providers/connectors, seeded temporary Course Workspaces, focused Playwright runs, traces, and cleanup.
3. A PowerPoint-fixture skill/script for inspecting templates, producing calibration decks, running each available renderer, and comparing profiles/thumbnails.
4. A compatibility script that proves Python 3.14 package resolution and executes `ruff`, `ty`, Python tests, frontend checks, and Playwright tests.
5. Deterministic fake model and connector fixtures. MCP-recorded live interactions are unsuitable as the primary test seam.
6. A security note enumerating which tests may use network access, external providers, user documents, and credentials. Default tests should use none.

## Suggested adoption order

| Priority | Capability | Decision |
|---|---|---|
| Baseline | Playwright test suite + repo skill/scripts | Add early |
| Baseline | Existing shell/filesystem/Git tooling | Keep; no redundant MCP |
| Baseline | Primary-source documentation workflow | Keep |
| On demand | Playwright MCP or CLI interactive mode | Use for UI exploration |
| On demand | Chrome DevTools MCP | Use for performance/network debugging |
| When remote work starts | Official GitHub MCP | Add scoped/read-only first |
| Optional | Context7 | Convenience only; external-data caution |
| Avoid | Generic SQLite MCP | Prefer read-only scripts/tests |
| Avoid | PowerPoint/LibreOffice MCP | Build typed adapters and fixtures |
| Avoid | Product use of development MCP credentials | Separate trust boundaries |

The immediate practical investment should therefore be a deterministic integration harness and Playwright coverage, not a larger MCP inventory.
