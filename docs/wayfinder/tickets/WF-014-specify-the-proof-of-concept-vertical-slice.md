---
title: Specify the proof-of-concept vertical slice
parent: ../course-harness-product-and-architecture.md
label: wayfinder:grilling
status: closed
assignee: codex
blocked_by:
  - WF-004-design-the-course-agent-runtime.md
  - WF-006-evaluate-grounding-and-retrieval.md
  - WF-008-decide-slide-preview-fidelity.md
  - WF-009-prototype-the-end-to-end-professor-experience.md
  - WF-010-design-local-distribution-and-provider-configuration.md
  - WF-011-threat-model-local-and-self-hosted-modes.md
  - WF-012-identify-the-data-science-demonstration.md
  - WF-013-collect-professor-validation-assets.md
---

# Specify the proof-of-concept vertical slice

## Question

What exact functional scope, acceptance scenarios, architecture seams, test strategy, demo script, exclusions, and staged implementation plan define the smallest working Course Harness that is impressive, usable by the Course Author, and technically defensible for both subjects?

## Resolution

The durable contract is `docs/specs/course-harness-proof-of-concept.md`, with the staged execution
queue in `.scratch/course-harness/issues/`. Tickets 12, 13, and 15 and the local part of ticket 16
implement and verify Current State and Course Revisions, partial Releases, journey hardening, and
PyPI-oriented packaging. Public PyPI and repository publication are deferred pending Course Author
authorization. Worker Agents and an optional Docker image are not part of the first demo.
