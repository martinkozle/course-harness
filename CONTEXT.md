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

**Approval Checkpoint**:
An optional pause at a meaningful authoring boundary where the Course Author can accept, revise, or redirect the agent's proposed work before it continues.
_Avoid_: Mandatory workflow stage, pipeline node

**Autonomous Mode**:
A per-run or persistent choice that lets the Course Agent pass routine authoring Approval Checkpoints until completion, while leaving steering, cancellation, and Release publication under Course Author control.
_Avoid_: Autonomy Level, permission tier

**Course Workspace**:
The directory opened by the app that contains one Course's human-readable inputs, authored state, and Artifacts, plus hidden derived runtime data.
_Avoid_: Project, repository, database

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
A versioned, reusable mapping between semantic Presentation layouts and the concrete layouts and placeholders of a PowerPoint template.
_Avoid_: Theme, template file, renderer configuration

**Slide Preview**:
A selectable visual projection of Slide state that may begin as a template-backed browser approximation and be replaced by an authoritative rendered thumbnail when a native renderer is available.
_Avoid_: Slide, Artifact, browser reimplementation of PowerPoint

**Course Agent**:
The persistent conversational agent accountable to the Course Author for planning and changing a Course.
_Avoid_: Pipeline, orchestrator, chatbot

**Provider Account**:
A reusable connection to a model provider, consisting of an endpoint and one private credential.
_Avoid_: Model, connection, provider configuration

**Model Preset**:
A named model choice with verified capabilities that uses one Provider Account; multiple Model Presets may share the same Provider Account.
_Avoid_: Provider, connection, model configuration

**Worker Agent**:
A temporary delegate that returns research, Evidence, or a draft proposal to the Course Agent without directly changing authoritative Course state.
_Avoid_: Course Agent, pipeline node
