---
title: Design local distribution and BYO-provider configuration
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: closed
assignee: codex
blocked_by:
  - WF-001-prove-stack-compatibility-on-python-3-14.md
  - WF-002-prototype-the-portable-course-workspace.md
---

# Design local distribution and BYO-provider configuration

## Question

What installation, frontend bundling, secret storage, model capability negotiation, configuration, update, and optional Docker strategy delivers a credible one-command local app while supporting Anthropic, OpenAI, OpenRouter, compatible endpoints, and local providers?

## Resolution

Course Harness is a local, single-user Python application that serves its committed production
React bundle. Publish it to PyPI for `uvx course-harness` and `uv tool install course-harness`;
direct Git installation remains a development convenience because the bundle is already package
data. Provider accounts and credentials live outside Workspaces, model presets expose capability
requirements, and no hosted variant is in scope. A local Docker image was not retained for the
first demo; it remains an optional later packaging path.
