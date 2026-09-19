---
title: Decide slide preview fidelity and native-renderer integration
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: closed
assignee: codex
blocked_by:
  - WF-007-prototype-template-profile-onboarding.md
---

# Decide slide preview fidelity and native-renderer integration

## Question

Should the proof of concept rely on a semantic React canvas, generated PowerPoint thumbnails through an optional installed renderer, LibreOffice conversion, or a hybrid, given portability, latency, licensing, and fidelity requirements?

## Resolution

Use a hybrid preview: a rendered empty-layout background plus responsive selectable browser
overlays gives immediate template-backed feedback, while a debounced optional LibreOffice or
PowerPoint adapter replaces it with an authoritative thumbnail. A semantic fallback keeps the app
usable when no native renderer is available. This decision is recorded in ADR 0010 and implemented.
