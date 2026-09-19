---
title: Design the Course Agent runtime
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: closed
assignee: codex
blocked_by:
  - WF-001-prove-stack-compatibility-on-python-3-14.md
  - WF-003-sharpen-the-course-authoring-model.md
---

# Design the Course Agent runtime

## Question

How should Pydantic AI implement the persistent Course Agent, bounded Worker Agents, sequential typed state mutations, conversational clarification, cancellation/resume, provider capability differences, and AG-UI event streaming without recreating a rigid FSM?

## Resolution

Use one persistent Pydantic AI Course Agent with typed application tools, one Workspace mutation
lock, server-persisted conversation, AG-UI streaming, Guided and Autonomous modes, safe-boundary
steering, and cancellation that retains valid partial state. A Worker Agent means a temporary
model-powered subagent with a narrow task, bounded context and read-only tools, execution limits,
and a structured result; it is not a FastAPI background task. Worker Agents are deferred until
after the first product demo because their orchestration complexity does not strengthen its core
Course-authoring journey.
