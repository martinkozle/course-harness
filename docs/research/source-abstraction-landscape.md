# Source abstraction landscape for Course Harness

Research date: 2026-07-12

## Recommendation

Use **Resource** as the broad external/readable thing and reserve **Source** for a resource that has been admitted to a Course's curated evidence set.

The smallest useful model is:

1. **Candidate** — a transient discovery result. It has a title, locator, snippet, provider and rank, but is not part of the Course and need not be downloaded or parsed.
2. **Resource** — a stable identity plus one or more locators for something the harness knows how to access: a workspace file, URL/DOI, Drive file ID, upload, MCP URI, GitHub object, or chat attachment. Registering a Resource is cheap and does not imply relevance.
3. **Source** — a Course-scoped admission of a Resource into the professor's selected/agent-selected corpus. It records purpose, selection state, provenance, rights/access notes, and refresh policy. A Source is what appears in the UI's Sources panel and what may ground authored output.
4. **Snapshot** — immutable bytes or captured text obtained from a Resource at a particular time, with content hash, retrieval metadata and origin locator. Remote and Drive Resources are materialized here; workspace files may be hashed/read in place unless portability is requested.
5. **Representation** — a versioned parser output derived from a Snapshot (Docling/LlamaParse Markdown or JSON, page map, images, chunks, embeddings). It is cacheable and disposable, and records parser/version/configuration.
6. **Evidence** — a precise span or block in a Representation, mapped back to the Snapshot's page/slide/sheet/character coordinates. This is the unit retrieved into model context.
7. **Citation** — a claim-to-Evidence link rendered for humans using the Resource's canonical bibliographic identity and a resolvable location. It must not merely point at a chunk ID.

This makes `Source` closer to NotebookLM's meaning than to “anything readable.” The agent may inspect Candidates through normal web/GitHub/MCP tools without polluting the curated corpus. If it needs full document reading, it registers a Resource and requests materialization/processing. If it decides the document should ground the course, it admits the Resource as a Source. Reading and admission can be one UI/agent action for ordinary local uploads, but should remain separate domain operations.

Suggested agent tools:

- `discover_resources(query, provider_filters)` → Candidates
- `register_resource(locator)` → Resource metadata
- `inspect_resource(resource_id, mode="metadata|preview|full")` → preview directly when safe, or lazily materialize and parse for `full`
- `add_source(resource_id, course_id, purpose?, refresh_policy?)` → Course Source
- `search_sources(query, source_ids?)` → Evidence hits
- `read_evidence(evidence_ids)` → bounded content plus original coordinates
- `cite(evidence_ids)` is usually implicit in authoring rather than a separate agent tool

Do not make Docling synonymous with reading. HTML, source code, GitHub issues, plain Markdown and MCP text resources may already have a usable representation; select a processor by media type and required fidelity. PDF/PPTX/DOCX and scanned files are the strong Docling/LlamaParse cases.

## What adjacent products teach us

### NotebookLM: discovery is separate from import; a Source is an analysis copy

NotebookLM defines a notebook as a project-specific collection of sources. Its source discovery UI returns web or Drive search results for review, and only selected results are imported. Deep Research likewise presents a report plus cited and uncited results; unimported results are discarded. This directly supports a `Candidate → Source` boundary rather than auto-registering every search result. [NotebookLM: add or discover sources](https://support.google.com/notebooklm/answer/16215270?hl=en), [NotebookLM: create a notebook](https://support.google.com/notebooklm/answer/16206563?hl=en)

NotebookLM's user-facing **Source** is “a copy or auto-synced version” of an imported document. Local audio is transcribed at import; web URLs contribute scraped text; URL PDFs are treated as PDF sources. Drive sources are read-only with respect to the original and can auto-sync, while losing Drive access makes the source inaccessible. This is best modelled internally as `Resource identity + Source membership + versioned Snapshot`, even though NotebookLM calls the aggregate a source. [NotebookLM: source types and import behavior](https://support.google.com/notebooklm/answer/16215270?hl=en)

NotebookLM grounds chat in the selected source set and its citations navigate to quoted text in source context. That argues for Evidence with stable location mapping, not opaque vector hits. [NotebookLM chat and citations](https://support.google.com/notebooklm/answer/16179559?hl=en)

### Gemini and Drive: original resource identity remains the verification target

Gemini in Drive lets users group files, folders and emails as sources, save them as a Project, and supplement them with web search. Citations open the original Drive document. This suggests keeping provider-native identifiers and permissions on Resource even after local materialization; the Snapshot is for reproducible processing, while the canonical Resource is the human verification target when still accessible. [Gemini in Drive research and citations](https://support.google.com/drive/answer/16963068?hl=en)

The Drive API represents both files and folders as `files` resources and separates metadata/identity from file content. Course Harness should mirror this connector boundary: a Drive Resource stores the stable Drive file ID, MIME type, revision/version metadata, access state and export strategy; downloaded bytes are a Snapshot, not the identity itself. [Google Drive files and folders overview](https://developers.google.com/workspace/drive/api/guides/about-files)

NotebookLM notebooks now appear in Gemini and source/instruction changes sync between them. However, grounding differs: NotebookLM answers exclusively from notebook sources, while Gemini may also use web search and other tools. This is a useful product distinction for Course Harness: “source-grounded mode” and “research mode” should be explicit policies over the same Resource/Source model. [Notebooks in Gemini Apps](https://support.google.com/notebooklm/answer/17003757?hl=en)

### Claude: distinguish a search result container from citable document content

Anthropic's API has a helpful term and schema: a `search_result` contains a required `source` URL/identifier, title and an array of text blocks. When citations are enabled, citations point to exact block ranges. Anthropic explicitly notes that block granularity controls citation granularity. Course Harness should expose processed chunks to providers in this shape where supported, but retain provider-neutral Evidence IDs and coordinates internally. [Claude search results](https://platform.claude.com/docs/en/build-with-claude/search-results)

Claude's document citations distinguish PDFs (page ranges), plain text (character ranges), and custom content (block ranges). Titles and context are metadata, not citable content. This strongly supports separating Source metadata from Evidence content and preserving original coordinate systems through parsing. [Claude citations](https://platform.claude.com/docs/en/build-with-claude/citations)

Claude Research's product promise is also phrased as searching many internal/external sources but citing only information it incorporates. That supports logging explored Candidates without promoting all of them into the Course's Sources. [Anthropic: advanced Research and integrations](https://www.anthropic.com/news/integrations)

### OpenAI File Search: uploaded file and indexed membership are different objects

OpenAI distinguishes a File from a `vector_store.file`, which represents attaching that File to a vector store; attachment has processing status, attributes and chunking strategy. This is a useful precedent for separating Resource/Snapshot identity from Source membership and Representation/index lifecycle. Course Harness should not let a provider vector store become authoritative because BYO providers, reparsing and local reproducibility require a provider-neutral corpus. [OpenAI vector store files](https://platform.openai.com/docs/api-reference/vector-stores-files/file-object)

### MCP: Resource is the right interoperability umbrella

MCP defines Resources as application-controlled contextual data, uniquely identified by URIs, with separate listing and reading operations; a Resource descriptor may include name, description, MIME type and size. Resources can be files, API/database content or application-specific information, and custom URI schemes are allowed. This makes `Resource` a good broad Course Harness term and creates a straightforward future MCP adapter. [MCP server primitives](https://modelcontextprotocol.io/specification/2025-06-18/server/index), [MCP Resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)

Course Harness should not copy MCP's control model blindly: MCP Resources are application-controlled, while model-driven retrieval is normally a Tool. Internally, expose Course Sources as resources to UI/MCP clients, but give the course agent bounded `search_sources`/`read_evidence` tools so actions remain observable and permissionable.

## Lifecycle and invariants

```text
discover → Candidate
             │ register / full-read request
             ▼
          Resource ── access/materialize ──> Snapshot ── process ──> Representation
             │                                                        │
             └── admit to Course ──> Source                            └── retrieve → Evidence
                                        │                                               │
                                        └──────────────── authored claim ───────────────┴→ Citation
```

Key invariants:

- A Candidate is expendable; a Resource has durable identity; a Source is deliberate Course membership.
- Every Snapshot is immutable and content-addressed. Refresh creates a new Snapshot and never silently rewrites historical evidence.
- Every Representation declares its Snapshot hash, processor, processor version and configuration.
- Every Evidence item maps to both Representation coordinates and original human coordinates where possible.
- Every published factual citation resolves to Evidence and Source; authored artifacts record the Snapshot version used.
- Removing a Source from a Course does not necessarily delete a shared Resource/Snapshot. Garbage collection is reference-counted and explicit.
- The agent receives content through bounded retrieval tools. Raw filesystem access can remain available for managed course authoring paths, but source documents stay read-only.

## Local files, URLs, Drive and chat attachments

The same model works without pretending they have identical behavior:

| Origin | Resource identity | Default materialization | Refresh |
|---|---|---|---|
| Workspace file | workspace-relative URI + file identity | Read/hash in place; cache Snapshot when parsing | Detect hash/mtime change, ask or version automatically by policy |
| External local file | absolute/file-bookmark locator | Managed copy for processing | Explicit |
| Upload/chat attachment | generated resource ID + original filename | Immediate managed immutable Snapshot | None unless replaced |
| Web paper | canonical URL and preferably DOI | Download immutable PDF/HTML Snapshot before full read | Explicit conditional fetch |
| Google Drive | Drive file ID + account/tenant | Download/export revision to Snapshot | Manual, watch/poll, or “latest before run” policy |
| MCP | server identity + resource URI | Read result cached as Snapshot when reproducibility is needed | MCP subscription or explicit read |
| GitHub | repository/object URL + commit SHA when possible | Normal tool browsing for discovery; Snapshot exact blob/page only when admitted/used | Pin or explicit refresh |

“Lazy processing” should therefore mean: registration is metadata-only; the first operation requiring full semantic content materializes a Snapshot if needed and creates the appropriate Representation. A cheap preview may avoid Docling. Adding a Source may optionally preprocess in the background so a professor does not wait at first chat.

## Future NotebookLM boundary

Treat NotebookLM as an optional downstream **notebook sink / grounded assistant**, not as Course Harness's storage or parsing backend.

As of the research date, consumer NotebookLM/Gemini offers product-level synchronization, but the documented programmable surface is NotebookLM Enterprise's preview API. It can create notebooks and add Drive documents, raw text, web content, YouTube URLs, or uploaded files as sources; returned source objects expose IDs, status and token/word metadata. [NotebookLM Enterprise notebook API](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks), [NotebookLM Enterprise source API](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks-sources)

Recommended adapter:

- Map one Course (or optionally one lecture) to a NotebookLM notebook.
- Export admitted Course Sources, never Candidates.
- Prefer original Drive IDs when authorized; otherwise upload the selected Snapshot or normalized text.
- Store a synchronization mapping `(course_source_id, snapshot_hash) ↔ notebooklm_source_id` and treat a new Snapshot as an explicit update/reimport.
- Do not depend on retrieving NotebookLM's internal parsed chunks: the documented API manages source objects, not a portable evidence graph.
- Import NotebookLM-generated artifacts only as new Resources with provenance, not as replacements for Course Harness authored state.
- Keep provider credentials, access scopes and data-residency decisions outside portable Course files.

NotebookLM Enterprise currently describes imported sources as static analysis copies and stores them inside the Google Cloud project, accessible only to NotebookLM Enterprise rather than other Cloud services. Its API is Preview/Pre-GA and requires Enterprise setup/licensing. That makes it a valuable future connector but a poor core dependency for the local-first proof of concept. [NotebookLM Enterprise overview](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/overview)

## Naming decision

Recommended ubiquitous language:

- **Resource**: anything addressable/readable through a connector.
- **Source**: a Resource selected into a Course's grounding corpus.
- **Evidence**: the citable fragment used to support a claim.
- **Citation**: the rendered link from claim to Evidence and original Resource.
- Keep **Document** as a media/category term, not the root abstraction; Resources also include websites, videos, repositories, folders, chats and MCP data.
- Keep **Reference** for bibliographic metadata or a lightweight mention in authored material. It is too ambiguous to name the readable object.

The concise UI can still say “Sources.” Advanced activity views can reveal “discovered,” “registered,” “downloading,” “processing,” “ready,” “stale,” and “access lost” states without teaching every internal noun to the professor.
