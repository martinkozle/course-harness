---
title: Prototype the portable Course Workspace
parent: ../course-harness-product-and-architecture.md
label: wayfinder:prototype
status: closed
assignee: codex
blocked_by: []
---

# Prototype the portable Course Workspace

## Question

What smallest human-readable directory layout, hidden runtime layout, mutation transaction, and Git snapshot behavior makes a Course portable and safely editable through both the UI and controlled agent tools without treating SQLite as the sole authority?

## Resolution

Canonical Course state lives in validated human-readable Workspace files written atomically. Git
is initialized with the Course while caches, credentials, chat history, global Library data, and
recent-Workspace metadata remain outside canonical state. The launcher and API expose only one
explicitly selected Workspace, and ticket 12 owns the remaining Current State, Revision, recovery,
and Workspace Drift behavior.
