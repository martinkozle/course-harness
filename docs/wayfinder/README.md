# Local wayfinding tracker

This repository has no configured remote issue tracker, so Wayfinder issues are Markdown files.

- Maps live directly in this directory and carry `label: wayfinder:map` frontmatter.
- Child tickets live in `tickets/` and carry a `parent`, `label`, `status`, and `blocked_by` list.
- An open ticket is on the frontier when `assignee` is empty and every ticket in `blocked_by` is closed.
- Claim a ticket by setting `assignee` before working. Resolve it by adding a `## Resolution` section, setting `status: closed`, and adding one linked gist to the map's **Decisions so far**.
- Create ticket files first and add `blocked_by` edges in a second pass. Ticket filenames are stable local identities; human-facing writing should use linked ticket titles.
