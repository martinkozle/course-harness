---
title: Prove the chosen stack on Python 3.14
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: closed
assignee: codex
blocked_by: []
---

# Prove the chosen stack on Python 3.14

## Question

Do current releases of uv, FastAPI, Pydantic AI, its AG-UI adapter, Docling candidates, python-pptx, Ruff, and ty install and interoperate on Python 3.14, and what minimal compatible dependency/packaging baseline should the proof of concept pin?

## Resolution

The pinned Nix and uv environment runs the selected Python 3.14, FastAPI, Pydantic AI/AG-UI,
python-pptx, Ruff, and ty stack. Application dependencies are pinned in `uv.lock`, frontend
dependencies in `bun.lock`, and the repository's full compatibility checks pass on Python 3.14.
Complex document processors remain adapter choices rather than baseline requirements.
