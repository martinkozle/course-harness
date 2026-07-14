# IDEA: Course Studio — an agentic, chat-driven course & deck creator

## 1. Origin and goal

Two inputs shape this project:

1. **A working prototype**: a chat-plus-canvas web
   app where a user describes a presentation, an agent researches a document
   collection, proposes an outline, writes slides one by one onto a live
   canvas with citations, and exports a native editable `.pptx`. It validated
   a specific architecture (see §3) end to end with live runs.
2. **The professor's project ("course-embroider")**: an agentic course
   generator — LangGraph FSM, one YAML-defined agent per pipeline node, that
   turns source material into a full course (syllabus, per-chapter slides,
   notes, Colab notebooks, H5P/Moodle package). Its research stage fans out to
   web, YouTube, GitHub, Coursera, and university OpenCourseWare sources.

The assignment: build a **course creator** in the spirit of course-embroider,
but interactive and chat-first like the prototype — an agent a professor can
converse with, that is "bloated" with connectors (upload anything, fetch
GitHub/Hugging Face/OCW), and that produces course presentations grounded in
real, cited sources.

The professor wants to actually use it, so it must run on his machine with his
own model access — hence standalone, open source, bring-your-own-provider.

## 2. Product concept

A local-first web app (one process, browser UI) with three pillars:

- **Library** (persistent, cross-course): every document ever uploaded or
  fetched — PDFs, slides, notebooks, GitHub repos/files, Hugging Face pages,
  OCW lectures, arbitrary URLs — is parsed once and stored in one common
  collection. Any course can draw on any of it. This is the NotebookLM-like
  half: a growing personal corpus, not per-project silos.
- **Chat + live canvas** (per course session): the user describes the course
  or deck; the agent researches the library and the web, proposes an outline,
  and writes slides live onto a canvas the user watches and steers
  conversationally ("make slide 3 punchier", "add a code example from that
  repo"). Documents can be **uploaded ad hoc mid-chat** and are immediately
  usable — and they land in the Library for future courses too.
- **Artifacts** (deterministic export): the typed course/deck state renders to
  native editable `.pptx` (flagship), and later possibly markdown mini-book,
  notebooks, H5P — all as pure projections of the same state.

Scope framing: start as a **course-presentation creator** (a course = ordered
lectures = decks, each slide typed and cited). The course-embroider extras
(notebooks, H5P, reviewer loops) are roadmap, not v1.

## 3. Architecture patterns proven in the prototype

These patterns were validated end to end by live use — reuse them as designed:

### 3.1 Typed state as the single source of truth

One Pydantic model (`DeckState` in the prototype; here likely
`CourseState`/`DeckState` per lecture) holds metadata + an ordered list of
slides. **Style never lives in the state — only content and structure.**
Visual style lives exclusively in the renderers (web canvas CSS, PowerPoint
template). This one rule is what makes canvas and export incapable of
drifting apart.

- Slides are a **discriminated union** on a `layout` field with a small closed
  enum of layouts. The prototype used: `title`, `section`, `bullets`,
  `two_column`, `big_statement`, `closing`. Each variant has typed content
  fields with validation constraints baked in (e.g. bullets: 1–6 short lines,
  `min_length=1` titles) — the schema itself pushes the model toward good
  slides. For a course creator, add layouts like `code` (language + snippet +
  caption, sourced from a real repo) and maybe `image`/`quote`.
- Every slide has a **stable `slide_id`** assigned at outline time and never
  reused; targeted edits address slides by id.
- Every slide carries `speaker_notes` and a list of **citations**
  (`source_id`, human `label`, resolvable `url`). Citations are mandatory on
  content slides — enforced by prompt, rendered on canvas, in a hyperlinked
  source bar on exported slides, and repeated in speaker notes.
- The state also holds an **outline** (list of `slide_id` + layout +
  working title + content note) set before any slide is written; the canvas
  renders unwritten outline entries as skeletons, which makes the staged
  agentic process *visible* — a demo/grading feature in itself.

### 3.2 A single agent with structured mutation tools (no pipeline FSM)

One end-to-end agent (PydanticAI in the prototype) owns the whole process.
Stage discipline (survey the brief → research → outline → write slides in
order → summarize) is enforced by the **system prompt and the shape of the
tools**, not by a state machine. This is the deliberate architectural
counterpoint to course-embroider's LangGraph FSM, and it worked: it makes the
system conversational and iterable ("smallest change that fulfils the
request") rather than batch-shaped.

- The agent mutates state **exclusively through tools**:
  `set_outline(metadata, outline)`, `write_slide(slide)` (upsert by id),
  `remove_slide(slide_id)`. It never emits the deck as free text.
- Tool implementations delegate to **pure state-transition functions**
  (`apply_outline`, `upsert_slide`, `remove_slide`) that return a new state
  and raise `ValueError` on invalid transitions (duplicate ids, unknown ids).
  Tools translate those into model-retries so the agent self-corrects.
  These pure functions are the primary unit-test surface.
- Useful semantics that took iteration to get right:
  - `apply_outline` *replaces* the outline; already-written slides survive
    only if their `slide_id` is still present. Slides are always re-sorted
    into outline order; slides without an outline entry go to the end.
  - Mutation tools run **sequentially** (no parallel tool execution) — state
    mutations must not race.
- Research happens through separate read-only tools (search/retrieve, list
  sources, read source, plus connector fetch tools — see §4). Prompt rule:
  research first, never invent facts, keep researching mid-writing when a
  slide needs depth.

### 3.3 AG-UI shared state streaming + CopilotKit canvas

The frontend protocol is **AG-UI over a single SSE endpoint** (the PydanticAI
AG-UI adapter did this in the prototype; any AG-UI-speaking backend works).
The typed state is AG-UI **shared state**: every mutation tool returns its
text result *plus* a `StateSnapshotEvent` carrying the full serialized state,
so the frontend mirrors the deck without any bespoke messaging. Generation
runs within the request — no job queue, no polling.

- Frontend: React + Vite, **CopilotKit** as the AG-UI client for the chat
  pane, run lifecycle, and state sync. The only substantial custom UI is the
  canvas: one React component per slide layout, plus a catch-all
  tool-activity renderer that shows a compact chip per agent tool call
  ("Searching…", "Writing a slide…") — cheap and hugely valuable for trust
  and for demonstrating the staged behavior.
- **Session persistence lesson (hard-won):** hang chat history and state off
  the AG-UI agent instance (`initialMessages`, `initialState`, subscribe to
  `onMessagesChanged`/`onStateChanged`), *not* off frontend framework
  context. One agent instance per session, remount on session switch, session
  id = AG-UI `threadId`. The prototype persisted sessions to localStorage;
  the standalone app should persist server-side (§5) but keep the same
  ownership model.
- **Normalize partial/legacy persisted state** before the first run — the
  adapter validates incoming frontend state against the schema, and stale
  shapes otherwise break runs.
- Field note: models occasionally pass nested tool arguments as
  JSON-encoded *strings* instead of objects. A lenient
  pre-validation coercion step (parse-if-string) on complex tool params saved
  many failed runs. Also: give mutation tools a few retries with the
  validation error fed back.

### 3.4 Deterministic state → PPTX projection

A pure function renders the state onto a **PowerPoint template file** using
python-pptx: the layout enum maps to template slide-layout indexes via a
small config mapping; content goes into the template's real placeholders
(filtered to BODY/OBJECT/SUBTITLE types, sorted in reading order — footer and
slide-number placeholders must never receive body text). Citations become a
small hyperlinked source line at the slide bottom and are repeated in notes.
The output is a **native, fully editable** deck — on-brand by construction if
the user supplies their institution's template. Export is one-way
("export = publish"); no re-import.

- Ship with the python-pptx stock template as the default mapping; let users
  drop in any `.pptx` template plus a layout-index mapping in config.
- This renderer is the **flagship test target**: render fixtures, read the
  file back with python-pptx, assert slide count/layouts/text/notes/citation
  presence. Deterministic, and it protects the actual deliverable.
- Fail fast: missing layout index or missing placeholder raises immediately.

### 3.5 Testing philosophy

Strict typing (mypy strict) as the primary safety net; unit tests only at
stable boundaries (pure state transitions, the pptx renderer). No end-to-end
agent tests (non-deterministic), no frontend tests initially. This kept the
prototype honest at very low cost.

## 4. New capabilities (the professor's asks)

The agent should be deliberately connector-rich:

1. **Ad hoc upload in chat** — drop a PDF/pptx/docx/md/ipynb/py into the
   conversation; it is parsed and queryable within the same session, and
   persists into the common Library for any future course.
2. **GitHub connector** — fetch repo pages, READMEs, and individual files;
   the agent can pull **real code examples** from a repo into `code` slides
   (with the repo/file as the citation).
3. **Hugging Face connector** — fetch model/dataset cards and docs pages.
4. **Academic/OCW connector** — fetch MIT OpenCourseWare (and similar:
   Stanford, Berkeley…) course and lecture pages; usable both as grounding
   material and as "further reading" citations. course-embroider does this
   with a domain-restricted search tool — a good pattern to mirror.
5. **General web fetch** (and optionally web search) as the fallback
   connector.

Connector results should flow through the same ingestion path as uploads
(§5.2) so everything fetched becomes a first-class, citable Library source
with a stable `source_id` and resolvable URL.

## 5. Standalone app architecture (proposal to grill)

### 5.1 Shape and packaging

**One Python package, one process:** FastAPI backend that serves the AG-UI
endpoint, the export endpoint, the ingestion/library API, **and the bundled
pre-built React frontend as static files**. Installable and runnable as:

```bash
uvx course-studio        # or: pip install course-studio && course-studio
# → starts on localhost, opens the browser
```

This resolves the desktop-vs-self-hosted question without choosing: it *is* a
local app for the professor (`uvx`, zero infra, data in `~/.course-studio/`),
and the same artifact self-hosts behind a reverse proxy if wanted. No
Electron, no Docker requirement (a Dockerfile can exist as a convenience).
Frontend build ships inside the wheel (hatch/uv build hook runs `bun run
build` or the dist is committed/CI-built).

### 5.2 Storage — local-first, zero external services

- **SQLite** as the only database (courses, sessions, chat histories, deck
  states as JSON, library source registry, parse artifacts metadata). Files
  (originals + parsed markdown + exports) on disk under a data dir.
- **Retrieval: start without embeddings.** The corpus is personal-scale;
  emulate NotebookLM-style grounding: agent tools are `list_sources`
  (metadata + summaries), `read_source` (full parsed markdown, paginated),
  and a keyword/full-text search over parsed content (SQLite FTS5 — free,
  in-process). Modern long-context models make "list, search, read the whole
  source" surprisingly competitive at this scale.
- **Escape hatch if needed later:** `sqlite-vec` + a BYO embedding model for
  semantic search — still zero external services. Do not build this until
  full-text proves insufficient. (Postgres/pgvector only ever as an optional
  backend if a multi-user hosted mode materializes; not v1.)

### 5.3 Document parsing

- **Docling** as the default parser (professor's suggestion; strong on
  PDF/docx/pptx → markdown, local, no API key). Heavy dependency — consider
  making it an extra (`uvx course-studio[docling]`) with a plain-text/
  pymupdf-level fallback, or lazy-loading it.
- Optional pluggable parsers behind one interface: LlamaParse (API-key,
  higher quality on nasty PDFs), simple markdown/code passthrough for
  `.md/.py/.ipynb`. One `Parser` abstraction, chosen per file type/config.
- Parse once at ingestion, store markdown + metadata; never re-parse at query
  time.

### 5.4 Runtime BYO provider

- No bundled models, no default keys. First-run settings screen (and/or
  `config.toml` / env vars): pick provider + model + API key/base URL.
- PydanticAI already abstracts OpenAI/Anthropic/Google/Bedrock/Ollama/
  OpenAI-compatible endpoints — expose exactly that surface. Ollama/local
  OpenAI-compatible support means the app can run fully offline.
- Model settings live per-app-config, not per-course. Switching models
  mid-session should just work (state is model-agnostic).

### 5.5 Course model (the layer above decks)

A `Course` groups metadata (title, goals, audience, level, language) and an
ordered list of **lectures**, each lecture being one deck state. v1 can ship
with course = single deck and grow into multi-lecture; keep the DB schema
ready for the 1:N from day one. A `syllabus` step (agent proposes the lecture
list before any deck exists) is the natural course-embroider-inspired stage
to add after the single-deck loop works.

## 6. Stack summary

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.12+, FastAPI, PydanticAI | agent + AG-UI adapter + REST |
| Agent protocol | AG-UI over SSE, shared typed state | proven in prototype |
| Frontend | React + Vite + CopilotKit, bundled into the wheel | canvas = custom components per layout |
| State/DB | SQLite (+ FTS5), JSON columns for states | files on disk under a data dir |
| Parsing | Docling default, pluggable (LlamaParse optional) | parse-once at ingestion |
| Export | python-pptx, template + layout-index mapping | deterministic, testable |
| Packaging | uv/hatch wheel, `uvx course-studio` entrypoint | Dockerfile as convenience |
| Types/tests | mypy strict; unit tests on state ops + renderer | no e2e agent tests |

## 7. Stretch / roadmap (post-v1, in payoff order)

1. **Reviewer pass** — second agent critiques the finished deck (coverage,
   clarity, grounding) and the main agent applies fixes.
2. **Outline approval pause** — human-in-the-loop between outline and
   writing (AG-UI supports interrupts).
3. **Syllabus stage + multi-lecture courses** (§5.5).
4. Notebook (`.ipynb`) artifact per lecture; markdown mini-book export.
5. H5P/Moodle packaging (course-embroider parity — likely never needed, ask
   the professor before building).
6. Semantic search upgrade (`sqlite-vec`) if FTS proves insufficient.

## 8. Open questions (grill these before writing the PRD)

- **Naming**: "Course Studio" is a placeholder.
- Course = deck v1, or is a minimal syllabus/multi-lecture model required for
  the grade? (Ask the professor what "course creator" minimally means to him.)
- Web *search* (needs a search API key — Exa/Tavily/Brave/SearXNG?) vs web
  *fetch only* (no key, agent needs URLs given to it) for v1.
- Docling as hard dependency vs extra — how much install weight is acceptable
  for `uvx`? (Docling pulls torch-adjacent deps; measure first.)
- Does GitHub fetching need the API (rate limits, token optional) or is raw
  `raw.githubusercontent.com` + HTML fetch enough for v1?
- Single deck template config vs per-course template selection.
- Auth: none (localhost) for v1 — is a token needed the moment someone
  self-hosts it publicly? (Probably a simple shared secret, later.)
- License (MIT/Apache-2.0) and repo home.

## 9. Non-goals

- Multi-tenancy, orgs, RBAC.
- Editing exported files and syncing back (export is terminal).
- Arbitrary design-system theming of the live canvas (canvas is a readable
  approximation; the template owns the exported look).
- Automated quality-eval harnesses (LLM-judge scoring of decks) — separate
  research concern.
