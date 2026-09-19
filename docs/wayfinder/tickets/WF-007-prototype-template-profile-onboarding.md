---
title: Prototype PowerPoint Template Profile onboarding
parent: ../course-harness-product-and-architecture.md
label: wayfinder:prototype
status: closed
assignee: codex
blocked_by:
  - WF-001-prove-stack-compatibility-on-python-3-14.md
  - WF-003-sharpen-the-course-authoring-model.md
---

# Prototype PowerPoint Template Profile onboarding

## Question

Can deterministic python-pptx inspection plus heuristic or LLM-assisted suggestions and user correction reliably map semantic slide layouts to representative arbitrary institutional templates, and what validation failures must block export?

## Resolution

Deterministic python-pptx inspection and structural heuristics provide confidence-scored semantic
layout and slot mappings. The UI supports calibration, correction, and consented optional LLM
assistance. Export blocks missing, colliding, or incompatible required mappings while leaving
ambiguous but structurally usable choices correctable. Versioned profiles are globally reusable
and Courses pin the selected version.
