---
title: Design Resource ingestion and connector boundaries
parent: ../course-harness-product-and-architecture.md
label: wayfinder:research
status: closed
assignee: codex
blocked_by: []
---

# Design Resource ingestion and connector boundaries

## Question

What provider-neutral contracts and lifecycle implement Candidate, Resource, Snapshot, Representation, Source, lazy processing, refresh, permissions, and deduplication across workspace files, uploads, URLs, papers, GitHub, Hugging Face, academic sites, MCP, and future Google Drive?

## Resolution

Use the provider-neutral lifecycle Candidate → Resource → Snapshot → Derived Representation →
Evidence → Citation, with Source as Course-scoped admission. Registration is metadata-first,
content-addressed Snapshots are immutable and globally reusable, processing output is disposable,
and remote refresh never silently changes a Course's pinned Source Version. Local files remain
read-only inputs. Additional connectors can implement the same discovery, fetch, and identity seam.
