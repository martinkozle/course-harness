---
title: Design the Course Agent runtime
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: open
assignee:
blocked_by:
  - WF-001-prove-stack-compatibility-on-python-3-14.md
  - WF-003-sharpen-the-course-authoring-model.md
---

# Design the Course Agent runtime

## Question

How should Pydantic AI implement the persistent Course Agent, bounded Worker Agents, sequential typed state mutations, optional Approval Checkpoints, cancellation/resume, provider capability differences, and AG-UI event streaming without recreating a rigid FSM?
