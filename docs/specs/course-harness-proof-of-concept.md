---
title: Course Harness proof of concept
label: ready-for-agent
status: open
---

# Course Harness proof of concept

## Problem Statement

A Course Author needs to turn local documents, online research, code, and institutional teaching material into a coherent multi-lecture Course without surrendering control to an opaque batch generator. Existing presentation agents tend to produce isolated decks, lose provenance, impose one visual system, or require hosted infrastructure and fixed model providers. The reference `course-embroider` project demonstrates broad autonomous course generation, but its rigid multi-agent pipeline and infrastructure-heavy deployment make conversational steering, local ownership, arbitrary PowerPoint templates, and incremental revision difficult.

The Course Author needs working local software, not a separate academic report: an agent harness that can research, plan, author, revise, cite, preview, version, and export a real Course through a UI while using model credentials and files controlled by the Course Author.

## Solution

Course Harness is a local-first, chat-first application that starts in a restricted Workspace Launcher and binds to at most one explicitly chosen Course Workspace. A persistent Course Agent collaborates with the Course Author, may delegate bounded research and drafting to Worker Agents, and changes typed Course state only through validated tools. The Course Plan contains an ordered flat list of Lectures; each Lecture may progressively acquire a Presentation, notes, and later other Artifacts.

The application maintains a reusable global Library while keeping each Course portable and inspectable through human-readable files and Git-backed history. Local files, uploads, URLs, papers, GitHub material, and future connectors enter a provider-neutral Resource lifecycle, with immutable Source Versions, Evidence, and human-verifiable Citations. Research can remain exploratory until a Resource is admitted as a Course Source.

PowerPoint remains the flagship deliverable. Course Harness onboards arbitrary structurally usable templates into versioned Template Profiles, combines deterministic inspection with optional LLM and vision assistance, streams responsive template-backed Slide Previews in React, and exports native editable `.pptx` files. Guided authoring is the default; one Autonomous Mode toggle lets the agent proceed until completion while preserving steering, cancellation, Current State inspection, validation, revisions, and explicit publication.

## User Stories

1. As a Course Author, I want to create a Course in an empty directory, so that its files and history have a clear home.
2. As a Course Author, I want to pass a directory such as the current directory from the CLI, so that Course Harness fits a coding-tool-style local workflow.
3. As a Course Author, I want to create, open, or reopen a Course Workspace from a launcher UI, so that routine use does not require terminal knowledge or an implicit filesystem default.
4. As a Course Author, I want one Course identity per Workspace, so that Sources, history, Artifacts, and restore operations remain unambiguous.
5. As a Course Author, I want a Workspace explorer, so that I can inspect Course files, assets, Artifacts, and attached locations without using a general-purpose file manager.
6. As a Course Author, I want Course state stored in readable YAML and Markdown, so that it can be backed up, diffed, and understood outside the application.
7. As a Course Author, I want provider keys excluded from the Workspace, so that sharing a Course cannot leak credentials.
8. As a Course Author, I want to configure my own Anthropic, OpenAI, OpenRouter, compatible, or local model provider, so that the application does not impose one vendor.
9. As a Course Author, I want the application to validate provider and model capabilities, so that unsupported tool calling, vision, or context requirements fail clearly.
10. As a Course Author, I want to upload files from chat or the Library UI, so that new material is immediately available to the Course Agent.
11. As a Course Author, I want to attach local files or folders read-only, so that the agent can use existing material without modifying originals.
12. As a Course Author, I want to register URLs, papers, DOI references, GitHub content, Hugging Face pages, and academic course pages, so that online evidence enters one consistent system.
13. As a Course Author, I want transient search results to remain Candidates, so that exploratory research does not pollute the Course corpus.
14. As a Course Author, I want a Resource processed only when full content is needed, so that registration remains fast and heavy parsing is lazy.
15. As a Course Author, I want processing status and failures visible, so that I understand why content is not yet searchable or readable.
16. As a Course Author, I want identical content deduplicated by hash globally, so that files are not repeatedly downloaded or processed across Courses.
17. As a Course Author, I want admitted Source snapshots retained immutably, so that remote updates or deletion cannot invalidate published Course material.
18. As a Course Author, I want update checks to create new Source Versions without silently adopting them, so that existing Citations remain reproducible.
19. As a Course Author, I want to compare and explicitly adopt a newer Source Version, so that affected content can be reviewed deliberately.
20. As a Course Author, I want every Course Source available to every Lecture, so that agentic research is not constrained by rigid assignments.
21. As a Course Author, I want to mark a Source Focus for a Lecture, so that retrieval can prioritize likely-relevant material without becoming an access rule.
22. As a Course Author, I want to inspect the Evidence behind a Citation, so that I can verify claims in their original page, slide, block, or code context.
23. As a Course Author, I want to describe a Course conversationally, so that the Course Agent can propose goals, outcomes, and a multi-lecture Course Plan.
24. As a Course Author, I want Lectures to exist before Presentations, so that I can approve the complete teaching plan while authoring only selected deliverables.
25. As a Course Author, I want lightweight grouping labels over a flat Lecture order, so that I can express modules without managing a deep hierarchy.
26. As a Course Author, I want a Syllabus rendered from the Course Plan, so that the visible syllabus cannot drift from authoritative Lecture structure.
27. As a Course Author, I want uploaded syllabi treated as Resources, so that the agent can propose changes without silently replacing Course state.
28. As a Course Author, I want the agent to create progressive Slides with stable identities, so that outline skeletons can become authored Slides without a mandatory pipeline.
29. As a Course Author, I want completed Slides to survive reordering and replanning, so that small structural changes do not destroy authored work.
30. As a Course Author, I want to watch the outline and Slides appear on a live canvas, so that the agent's staged work is visible and steerable.
31. As a Course Author, I want to see compact research, delegation, retrieval, and mutation activity, so that agent behavior is understandable without exposing raw internals.
32. As a Course Author, I want to select a Course, Lecture, Slide, content block, or Citation and send a contextual instruction, so that most editing remains conversational and precise.
33. As a Course Author, I want simple direct controls for rename, reorder, approve, archive, restore, and small corrections, so that trivial actions do not require elaborate prompting.
34. As a Course Author, I want one persistent Course Agent accountable for changes, so that the experience remains coherent across many Lectures.
35. As a Course Author, I want the Course Agent to delegate bounded work to Worker Agents, so that research and drafting can scale without concurrent authoritative writes.
36. As a Course Author, I want Guided authoring by default, so that I can review the Course Plan and important outlines before substantial generation.
37. As a Course Author, I want one Autonomous Mode toggle or a natural-language “continue until done” instruction, so that trusted runs can proceed without repeated approval.
38. As a Course Author, I want to steer an active run at safe boundaries, so that I can redirect work without racing state mutations.
39. As a Course Author, I want to cancel a run while preserving valid partial Current State, so that useful work is not discarded.
40. As a Course Author, I want mutating UI actions locked during an active run, so that state changes cannot race the Course Agent.
41. As a Course Author, I want a Current State panel with changes and selective revert, so that recovery feels approachable without requiring Git knowledge.
42. As a Course Author, I want meaningful Course Revisions created by me or the agent, so that history describes Course evolution rather than every tool call.
43. As a Course Author, I want hidden crash-recovery snapshots around runs, so that failure does not depend on whether a visible Revision was created.
44. As a Course Author, I want external file edits detected as Workspace Drift, so that out-of-app work is not ignored or silently overwritten.
45. As a Course Author, I want the Course Agent to explain and reconcile inconsistent external changes, so that intent can become valid Course state through a reviewed patch.
46. As a Course Author, I want to import an arbitrary PowerPoint template, so that exported Lectures follow institutional branding.
47. As a Course Author, I want Template Profile onboarding to infer layouts and slots automatically, so that common templates require little setup.
48. As a Course Author, I want low-confidence mappings explained and visually correctable, so that unusual templates remain usable.
49. As a Course Author, I want a calibration deck, so that I can validate how semantic content maps into the template before generating a Course.
50. As a Course Author, I want Template Profiles saved and versioned globally, so that one corrected institutional template can be reused safely across Courses.
51. As a Course Author, I want each Course to pin a Template Profile version, so that later mapping changes do not silently alter old Course output.
52. As a Course Author, I want immediate template-backed Slide Previews, so that backgrounds, logos, and placeholder geometry appear while the agent writes.
53. As a Course Author, I want authoritative rendered thumbnails when PowerPoint or LibreOffice is available, so that I can inspect high-fidelity output before export.
54. As a Course Author, I want a semantic preview fallback, so that the application remains usable without PowerPoint or LibreOffice installed.
55. As a Course Author, I want native editable `.pptx` output with notes and hyperlinks, so that I can finish or teach from the deck in standard software.
56. As a Course Author, I want structural export failures reported before download, so that missing layouts or unusable placeholders never produce deceptively broken decks.
57. As a Course Author, I want conservative citation linting, so that missing grounding is visible without noisy false-positive claim counting.
58. As a Course Author, I want malformed Citation and Evidence references treated as errors, so that exported links cannot silently point nowhere.
59. As a Course Author, I want to ask the Course Agent to address Validation Findings, so that quality review remains conversational.
60. As a Course Author, I want to publish with a recorded Waiver when I accept a warning, so that advisory linting does not become a rigid gate.
61. As a Course Author, I want to create partial versioned Course Releases, so that a `0.1` milestone may contain only the Lectures currently ready.
62. As a Course Author, I want Course Releases represented by named Git tags and manifests, so that published milestones pin their Revision, Sources, template, validation, and Artifact checksums.
63. As a Course Author, I want to inspect or regenerate an older Course Release, so that later edits do not erase prior deliverables.
64. As a Course Author, I want to make a Course portable, so that required global Source snapshots and Template Profile data can be transferred intentionally.
65. As a Course Author, I want keyboard-accessible chat, navigation, canvas selection, dialogs, and streaming announcements, so that the application is usable without pointer-only interaction.
66. As a Course Author, I want the UI to address me neutrally as “you” rather than “professor,” so that Course Harness works for instructors, trainers, and other authors.
67. As a Course Author, I want local operation without mandatory Docker or external databases, so that initial use is straightforward.
68. As a Course Author, I want an optional Docker deployment path, so that the same application can later run on a trusted self-hosted machine.
69. As a Course Author, I want clear disclosure before content is sent to an external model, parser, vision provider, or connector, so that local-first operation does not hide data movement.
70. As a Course Author, I want convenience defaults and advanced escape hatches, so that common workflows are easy without preventing expert control.

## Implementation Decisions

- Use Python 3.14 managed by uv, FastAPI, Pydantic AI, AG-UI, React, Bun, Ruff, and ty. Dependency compatibility must be proven and pinned before feature implementation proceeds deeply.
- Package one Python application that serves the Workspace Launcher, API, AG-UI endpoint, export and ingestion routes, and bundled prebuilt frontend. With no path, the local CLI opens the restricted launcher; an explicit path bypasses it. Docker remains optional.
- Use neutral UI language: “you” where possible and `Course Author` where a role name is required. “Professor” may appear only in historical assignment context, not product copy.
- Treat one directory as one Course Workspace. Do not silently use the current directory or an application-managed home directory. The Workspace Launcher creates, opens, and reopens Workspaces explicitly, while an explicit CLI path remains a power-user shortcut; after selection, the process has at most one active Workspace.
- Keep portable intent in canonical YAML/Markdown: Course Plan, instructions, Sources, ordered Lecture state, Presentations, notes, and Course-owned assets. Generated Artifacts are reproducible outputs rather than authority.
- Keep chat transcripts, execution events, provider secrets, global Library objects, parser output, thumbnails, and indexes outside canonical Course files.
- Use platform-native data, cache, configuration, and credential locations. Durable admitted snapshots and Template Profiles belong to user data; Derived Representations and previews belong to disposable cache; credentials use the OS keyring where viable.
- Automatically initialize Git for each Course Workspace. Expose typed diff, selective revert, semantic Revision, restore, and Release operations rather than unrestricted Git shell access to the Course Agent.
- Preserve Current State independently of visible Revisions. Meaningful Course Revisions are created by the Course Agent or Course Author; hidden recovery snapshots protect run boundaries. Cancelled valid work remains in Current State.
- Detect dirty canonical files as Workspace Drift. Validate the complete affected state and let the Course Agent explain, ask intent, or propose a reviewed Reconciliation.
- Model the authoritative Course Plan as Course metadata and a flat ordered list of stable-ID Lectures with optional lightweight grouping labels. The Syllabus is projected from this state.
- A Lecture exists independently of any Artifact. A Presentation is optional, and future notebooks or other deliverables may attach without changing Lecture identity.
- Use one progressive stable-ID Slide entity. It may begin as a working title, purpose, and intended layout, then accumulate typed content, Evidence, Citations, and notes. The harness recommends but does not enforce research-outline-author workflows.
- Preserve stable Course, Lecture, Presentation, and Slide IDs across rename and reorder operations; never reuse removed IDs.
- Keep Course Sources permissively available across all Lectures. Source Focus is a retrieval hint only, while Evidence and Citations record actual use.
- Implement the lifecycle `Candidate → Resource → Snapshot → Derived Representation → Evidence → Citation`; Source is the Course-scoped admission of a Resource, and Source Version pins an immutable Snapshot.
- Registration is metadata-first. Full inspection lazily materializes remote content and selects a processor by media type. Docling or LlamaParse are candidates for complex PDF, PPTX, DOCX, and scanned content, not universal definitions of reading.
- Deduplicate Snapshots globally by content hash. Refresh creates a new immutable Snapshot, existing Courses remain pinned, and adoption of a new Source Version marks affected content for review.
- Start retrieval with source listing, bounded reading, and SQLite FTS5 over Derived Representations. Preserve a provider-neutral Evidence coordinate model. Add semantic retrieval only after evaluation demonstrates material benefit.
- Use one persistent Course Agent for conversation and authoritative typed mutations. Worker Agents may perform bounded research or drafting and return proposals/Evidence but cannot mutate Course state.
- Allow only one mutating run per Workspace. Stream read-only progress through AG-UI, lock competing mutations, accept steering at safe boundaries, and preserve valid partial state on cancellation.
- Make Guided authoring the default with Approval Checkpoints around the Course Plan and significant Lecture outlines. One Autonomous Mode toggle or explicit natural-language instruction bypasses routine checkpoints; Course Release publication is always explicit.
- Route both chat intent and limited direct UI actions through the same validated application commands. Emphasize contextual chat instructions over building a general PowerPoint editor.
- Use AG-UI shared state/events for chat, run lifecycle, activity, and live Course projection. Persist sessions server-side; do not make browser framework context authoritative.
- Import structurally usable `.pptx` or `.potx` files into versioned Template Profiles. Inspect masters, layouts, placeholders, names, types, geometry, inheritance, themes, and example slides deterministically.
- Infer Template Profile mappings using structural heuristics first, text-LLM classification for ambiguity, and rendered calibration images plus vision only when structural confidence remains low. Show the configured provider and obtain onboarding consent for image transmission.
- Automatically accept high-confidence mappings as unverified, generate a calibration deck, allow visual correction, and block export only for genuinely unusable required mappings or placeholders.
- Let each Template Profile expose concrete layouts mapped to core semantic archetypes and typed slots, plus custom layouts. The Course Agent chooses only layouts declared by the pinned Profile.
- Build immediate Slide Previews from a rendered empty-layout background plus selectable HTML/SVG overlays positioned from placeholder geometry. Debounce native rendering and replace the approximation with authoritative thumbnails when available.
- Implement optional renderer adapters for installed Microsoft PowerPoint and LibreOffice. Keep high-fidelity rendering optional, detect capabilities, and retain a semantic fallback.
- Generate native editable PowerPoint deterministically from Course state and a pinned Template Profile. Include speaker notes, Evidence-backed citation labels/links, and fail clearly on invalid mapping.
- Treat structural validation failures as blocking and citation coverage as conservative Release linting. Do not map bullet counts to claim counts. Allow explicit per-finding Waivers.
- Publish partial or complete Course Releases as annotated Git tags with manifests pinning the Course Revision, included deliverables, Source Versions, Template Profile version, validation result, Waivers, and Artifact checksums.
- Keep development-agent MCPs separate from product connectors. Context7 and optional Playwright MCP assist development only; Course Harness receives its own bounded connectors, credentials, provenance, and permissions.
- Prefer ordinary Playwright tests and project scripts over an expanding MCP inventory. Add GitHub MCP only when remote issue/PR/CI work begins; do not add generic filesystem, Git, SQLite, PowerPoint, or LibreOffice MCPs.
- Create project-level agent instructions documenting architecture invariants, commands, canonical-versus-derived state, security boundaries, and neutral UI terminology once the scaffold exists.

## Testing Decisions

- The primary seam is the public FastAPI/AG-UI application boundary running against a temporary real Course Workspace. Tests use a deterministic fake model and fake external connectors while retaining real Pydantic validation, filesystem writes, Git history, event streaming, PowerPoint generation, and read-back verification.
- Good tests assert external behavior: streamed user-visible state, canonical Workspace files, Current State diffs, Course Revisions, Releases, validation findings, Evidence/Citation integrity, and generated `.pptx` content. They should not assert prompt wording, internal call counts, private helper structure, or incidental framework behavior.
- Build deterministic fake model, search, fetch, parser, and connector fixtures. Live provider runs may be optional smoke checks but are not the primary correctness suite.
- Test Course state commands through their highest application-service seam: Course Plan changes, Lecture reorder/archive, progressive Slide updates, Source Version adoption, cancellation, steering boundaries, validation, Reconciliation, and release creation.
- Test Resource processing with fixed local fixtures for PDF, PPTX, DOCX, Markdown, code, and notebook inputs. Assert immutable Snapshot hashing, processor/version metadata, coordinate preservation, cache deletion recovery, and global deduplication.
- Test retrieval on a small labeled corpus. Measure whether expected Evidence is found and whether coordinates resolve; use the result to decide if FTS5 is sufficient before adding embeddings.
- Test PowerPoint export with representative fixture templates. Generate decks, read them back, and assert layout selection, placeholder targeting, slide order, text, images, notes, hyperlinks, and Citation presence.
- Test Template Profile inference using structural fixture reports and expected mappings. Keep LLM/vision suggestions deterministic in tests and separately exercise user correction and version pinning.
- Maintain renderer-specific visual fixtures for PowerPoint and LibreOffice where available. Do not require pixel identity across renderers; use explicit tolerances and structural assertions.
- Check in normal Playwright browser tests for the main chat-to-export journey, run locking, steering, cancellation, Current State, contextual Slide instructions, Source processing states, template onboarding, validation, and partial Release publication.
- Add automated accessibility assertions with `@axe-core/playwright` to stable journeys, plus explicit keyboard and live-region tests for streaming chat and canvas selection.
- Keep one browser smoke journey broad; use focused API/application tests for combinatorial behavior so browser tests remain fast and diagnosable.
- Verify provider adapters with contract tests for tool calling, structured output, streaming, vision capability detection, authentication failure, and OpenAI-compatible base URLs.
- Test security boundaries with path traversal, symlinks, external-file write attempts, malicious filenames, prompt-injected Resources, unsafe URLs, oversized documents, parser failures, and secret redaction.
- Run the compatibility suite on Python 3.14 through uv and include Ruff, ty, Python tests, frontend type/lint/build checks, and Playwright in reproducible commands.
- Prior art comes from the previous prototype's pure state-transition and PowerPoint read-back tests, `course-embroider` artifact/core tests, AG-UI shared-state behavior, and Playwright's trace/screenshot tooling.

## Out of Scope

- Multi-tenancy, organizations, role-based access control, and production SaaS operations.
- Real-time collaboration between multiple Course Authors.
- Round-trip import or synchronization of edits made to exported PowerPoint files.
- Guaranteed zero-configuration support for malformed templates or templates without meaningful layouts/placeholders.
- Reimplementing PowerPoint rendering completely in React.
- Mandatory Microsoft PowerPoint, LibreOffice, Docker, Postgres, Redis, MinIO, or external vector infrastructure.
- A separate academic report deliverable.
- Full H5P/Moodle packages, mini-books, notebooks, Colab publishing, and executable exercises in the first presentation-centered proof of concept.
- Consumer NotebookLM as authoritative storage, parsing, or retrieval infrastructure.
- Google Drive and NotebookLM integration in the first vertical slice, beyond preserving connector-compatible Resource boundaries.
- Hosted multi-user authentication beyond a later safe single-user self-hosted mode.
- Automated LLM-judge scoring as the primary definition of Course quality.
- Unrestricted shell, filesystem, Git, Office macro, or MCP access for the Course Agent.

## Further Notes

- “Course Harness” is the working product name. Naming and license remain reversible decisions.
- Ease of use and quality of life are the highest product priorities: sensible defaults, progressive disclosure, and UI control should hide infrastructure complexity without hiding provenance or state changes.
- The proof of concept should visibly demonstrate agent-harness engineering and data-science concerns through retrieval, evidence grounding, source processing, versioning, evaluation, and observable agent work.
- `course-embroider` remains valuable prior art for connector breadth, per-Lecture parallelism, review, and future Artifacts, but Course Harness intentionally replaces its fixed batch FSM with a steerable persistent Course Agent.
- The source-abstraction and implementation-agent-tooling research documents are supporting evidence for this spec.
- Open Wayfinder tickets still de-risk stack compatibility, Workspace persistence, agent runtime, connector contracts, retrieval evaluation, template onboarding, rendering, UX, packaging, security, academic demonstration, validation assets, and final vertical-slice scope. This PRD states the intended product contract; those tickets may refine implementation details without silently changing settled user-facing behavior.
