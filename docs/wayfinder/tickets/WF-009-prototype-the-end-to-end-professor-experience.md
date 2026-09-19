---
title: Prototype the end-to-end Course Author experience
parent: ../course-harness-product-and-architecture.md
label: wayfinder:prototype
status: closed
assignee: codex
blocked_by:
  - WF-002-prototype-the-portable-course-workspace.md
  - WF-004-design-the-course-agent-runtime.md
  - WF-006-evaluate-grounding-and-retrieval.md
  - WF-008-decide-slide-preview-fidelity.md
---

# Prototype the end-to-end Course Author experience

## Question

What UI flow best lets a Course Author open a folder, configure a provider, register and inspect Resources, converse with an autonomous Course Agent, answer targeted clarification questions, observe delegated work, edit a multi-lecture Course, validate citations, map a template, and export presentations without needing the CLI?

## Resolution

Use a restricted Workspace Launcher followed by a chat-first authoring workspace with dedicated
Syllabus, Library, Presentation, Model, and Template surfaces. Selection supplies conversational
context while small direct edits use the same validated command seam. The implemented browser
journeys cover the established authoring, template, and export flow. Releases join that flow through
tickets 13–15 rather than reopening its information architecture; Worker Agents are deferred.
