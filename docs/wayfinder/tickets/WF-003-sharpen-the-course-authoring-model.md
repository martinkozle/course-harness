---
title: Sharpen the multi-lecture authoring model
parent: ../course-harness-product-and-architecture.md
label: wayfinder:grilling
status: closed
assignee: codex
blocked_by: []
---

# Sharpen the multi-lecture authoring model

## Question

Which concepts, invariants, lifecycle states, and approval boundaries must the typed Course, Syllabus, Lecture, Presentation, Slide, notes, and Citation model express for the Course Author's real multi-lecture workflow?

## Resolution

Use one flat ordered Course Plan containing stable-ID Lectures; grouping labels remain lightweight rather than first-class Modules. A Lecture exists independently of optional Presentations and other Artifacts. A Slide is one progressively authored entity that may begin as an outline skeleton, not a separate Slide Plan or enforced workflow stage. Course Sources are available to every Lecture, with optional Source Focus hints and actual use recorded through Evidence and Citations.

Keep the UI chat-first: selection-based contextual instructions feed the Course Agent, while low-ambiguity direct controls remain available. One mutating run owns the Workspace at a time; the Course Author may steer at safe boundaries or cancel, leaving valid partial work in Current State. Guided Approval Checkpoints are the default, and one Autonomous Mode toggle or natural-language instruction lets the agent continue until done; publishing remains explicit.

The Course Plan is authoritative and the Syllabus is its rendered view. Canonical YAML/Markdown Workspace files support Git-backed semantic Course Revisions, visible Current State, external-edit Reconciliation, partial Course Releases, and immutable tagged Release manifests. Working Slides may be incomplete or uncited; structural reference failures block, while citation coverage is conservative Release linting with explicit Waivers.
