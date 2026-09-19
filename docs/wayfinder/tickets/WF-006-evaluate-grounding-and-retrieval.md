---
title: Evaluate grounding, retrieval, and citation fidelity
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: closed
assignee: codex
blocked_by:
  - WF-003-sharpen-the-course-authoring-model.md
  - WF-005-design-resource-ingestion-and-connectors.md
---

# Evaluate grounding, retrieval, and citation fidelity

## Question

Which combination of source listing, bounded reading, SQLite FTS5, optional semantic retrieval, Evidence coordinates, and provider citation features gives trustworthy course grounding at personal-corpus scale, and what small evaluation corpus can decide when embeddings are justified?

## Resolution

Use source listing, bounded reading, and SQLite FTS5 with resolvable provider-neutral Evidence
coordinates for the proof of concept. Representative deterministic tests assert expected retrieval
and coordinates. The deliverable is a working product demonstration, not a quantitative retrieval
evaluation study, so embeddings remain out until a demonstrated product need justifies them.
