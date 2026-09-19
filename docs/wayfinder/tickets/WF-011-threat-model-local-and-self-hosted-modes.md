---
title: Threat-model local and optional container modes
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: open
assignee:
blocked_by:
  - WF-002-prototype-the-portable-course-workspace.md
  - WF-005-design-resource-ingestion-and-connectors.md
  - WF-010-design-local-distribution-and-provider-configuration.md
---

# Threat-model local and optional container modes

## Question

What boundaries are required for filesystem writes, connector credentials, prompt-injected Resources, remote fetches, parser execution, generated code, localhost exposure, and an optional locally run container, and which controls belong in the proof of concept? A hosted Course Harness service is not in scope.
