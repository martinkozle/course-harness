# Course Harness

Course Harness is a local-first environment in which a Course Author collaborates with an agent to create and maintain source-grounded, multi-lecture course material.

## Language

**Course**:
A teaching plan with shared goals, audience, source material, and an ordered sequence of Lectures.
_Avoid_: Deck, presentation

**Course Author**:
The person who directs, reviews, revises, and publishes a Course through Course Harness.
_Avoid_: Professor, admin, end user

**Course Plan**:
The authoritative structured description of a Course's goals, audience, outcomes, grouping labels, and ordered Lectures.
_Avoid_: Syllabus, deck outline

**Syllabus**:
A Course Author-facing view or Artifact projected from the Course Plan; an uploaded pre-existing syllabus begins as a Resource rather than authoritative Course state.
_Avoid_: Course Plan, Source

**Lecture**:
One planned teachable unit within a Course, defined independently of whether any Presentation or other Artifact has been authored for it.
_Avoid_: Chapter, module, deck

**Course Harness**:
The interactive environment that gives an agent tools, Sources, persistent state, and Artifact production capabilities for building Courses with a Course Author.
_Avoid_: Course generator, pipeline

**Presentation**:
An optional authored deliverable for a Lecture, consisting of an ordered collection of Slides and exportable as an editable PowerPoint file.
_Avoid_: Course, lecture

**Slide**:
A stable, progressively authored unit within a Presentation that may begin as an outline skeleton and accumulate content, Evidence, Citations, and speaker notes without passing through a mandatory workflow.
_Avoid_: Slide Plan, PowerPoint page, agent stage

**Library**:
The persistent collection through which Resources and their processing state can be discovered and reused across Courses.
_Avoid_: Vector store, uploads

**Candidate**:
A transient discovery result that may be inspected or registered but has not yet been made durable.
_Avoid_: Source, search hit

**Attachment**:
A Library Resource the Course Author attached to one Conversation message, which the Course Agent may view or admit as a Source but which grounds nothing until admitted.
_Avoid_: Upload, Source, Candidate

**Resource**:
An addressable item the harness knows how to access through a connector, independently of whether any Course considers it relevant.
_Avoid_: Document, Source, Reference

**Source**:
A Course-scoped admission of a Resource into the corpus allowed to ground that Course's authored material.
_Avoid_: Resource, document

**Source Focus**:
An optional hint that a Source is especially relevant to a Lecture, without preventing that Lecture from using any other Course Source.
_Avoid_: Assignment, access rule, source ownership

**Snapshot**:
An immutable capture of a Resource's content at a particular version or retrieval time.
_Avoid_: Resource, cache entry

**Source Version**:
The immutable Snapshot of a Source pinned by a Course so its Evidence and Citations continue to refer to the content actually used.
_Avoid_: Latest version, Resource URL

**Course Workspace**:
The directory opened by the app that contains one Course's human-readable inputs, authored state, and Artifacts, plus hidden derived runtime data.
_Avoid_: Project, repository, database

**Workspace Launcher**:
The restricted Course Harness state shown before a Course Workspace is active, from which a Course Author may create, open, or reopen one explicitly.
_Avoid_: Default Workspace, home Workspace, dashboard

**Course Revision**:
An atomic, recoverable version of the human-readable Course Workspace created after a meaningful Course Author or agent action.
_Avoid_: Tool call, autosave, database transaction

**Current State**:
The latest valid Course state, including uncommitted Course Author or agent changes that may be inspected, continued, reverted selectively, or captured as a Course Revision.
_Avoid_: Draft, autosave, release

**Course Release**:
A named immutable publication of a Course Revision that pins the Source Versions, Template Profile, validation result, and Artifact identities used for delivery.
_Avoid_: Latest export, branch, autosave

**Workspace Drift**:
Uncommitted external changes to canonical Course Workspace files that have not yet been validated and accepted as a Course Revision.
_Avoid_: Corruption, revision, merge conflict

**Reconciliation**:
A Course Author-reviewed interpretation and consistent patch that turns Workspace Drift into valid Course state.
_Avoid_: Automatic repair, overwrite, rollback

**Derived Representation**:
A disposable, reproducible form of a Snapshot—such as extracted Markdown, structured blocks, or a search index—stored without modifying the Resource itself.
_Avoid_: Source, converted original

**Attached Source**:
A Source made available to a Course Workspace without granting the agent permission to alter its original content.
_Avoid_: Managed file, upload

**Artifact**:
A publishable output projected from authored course state, such as an editable PowerPoint presentation.
_Avoid_: Source, cache, generated file

**Evidence**:
A precise passage or block from a Derived Representation that maps back to human-verifiable coordinates in its Snapshot.
_Avoid_: Search result, Source, Citation

**Citation**:
A claim-level link to Evidence, rendered with the Resource's canonical identity and a resolvable human location.
_Avoid_: URL, Reference, chunk identifier

**Validation Finding**:
A structural error or conservative quality warning produced while checking Course state or a proposed Course Release.
_Avoid_: Agent criticism, claim count, automatic rejection

**Waiver**:
A recorded Course Author decision to publish a Course Release despite a specific quality warning.
_Avoid_: Error suppression, global ignore

**Template Profile**:
A named, versioned, reusable mapping between semantic Presentation layouts and the concrete layouts and placeholders of a PowerPoint template. Its stable identity is independent of its unique, editable display name.
_Avoid_: Theme, template file, renderer configuration

**Slide Preview**:
A selectable visual projection of Slide state that may begin as a template-backed browser approximation and be replaced by an authoritative rendered thumbnail when a native renderer is available.
_Avoid_: Slide, Artifact, browser reimplementation of PowerPoint

**Course Agent**:
The persistent conversational agent accountable to the Course Author for planning and changing a Course.
_Avoid_: Pipeline, orchestrator, chatbot

**Conversation**:
A named, reopenable exchange between the Course Author and Course Agent within one Course Workspace. Its transcript lives in private application state, outside canonical Course files.
_Avoid_: Course Revision, Course state

**Context Compaction**:
A Course Author-reviewed summary of a Conversation's earlier turns that replaces those turns in future model context while retaining the complete visible transcript.
_Avoid_: Transcript deletion, Course Revision

**Provider Account**:
A reusable connection to a model provider, consisting of an endpoint and one credential: a private stored key, or, for Amazon Bedrock, a reference to AWS credentials that the AWS SDK resolves on every use and Course Harness never stores.
_Avoid_: Model, connection, provider configuration

**Detected Credential**:
A model provider credential that Course Harness can see in the environment it started from or in the shared AWS configuration, offered to the Course Author to add as a Provider Account; it is never used until added.
_Avoid_: Default provider, automatic Provider Account

**Model Preset**:
A named model choice with verified capabilities that uses one Provider Account; multiple Model Presets may share the same Provider Account.
_Avoid_: Provider, connection, model configuration

**Connector**:
A per-installation, Course Author-configured external tool server, such as a remote MCP web search, whose tools the Course Agent may use for research; what it returns is Candidate material until admitted.
_Avoid_: Plugin, integration, Provider Account

**Connector Credential**:
A private secret, such as an API key header, that authenticates one Connector and lives in installation credential storage, never in a Course Workspace.
_Avoid_: Provider Account, API key setting

**Paper Search Key**:
An optional private API key for one native paper index, such as Semantic Scholar, that raises its rate limit; it lives in installation credential storage, never in a Course Workspace, and is not a Connector Credential because paper search is not a Connector.
_Avoid_: Connector Credential, research key

**Worker Agent**:
A temporary model-powered delegate that receives a narrow assignment and returns research, Evidence, critique, or a draft proposal without directly changing authoritative Course state. It is distinct from a generic asynchronous or background application job.
_Avoid_: Course Agent, pipeline node, background task
