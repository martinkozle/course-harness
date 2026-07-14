# Domain Docs

This is a single-context repository.

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- Relevant ADRs under `docs/adr/`.

If either location is absent, proceed silently. Domain documentation is created lazily when terms or decisions are actually resolved.

## Use the glossary’s vocabulary

Use the canonical terms in `CONTEXT.md` in issue titles, implementation plans, tests, UI discussions, and code-facing domain names. Avoid synonyms explicitly listed under `_Avoid_`.

If a required concept is missing, reconsider whether a new term is necessary or capture the gap through the domain-modeling workflow.

## Flag ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict explicitly rather than silently overriding the decision. A superseding decision should explain why the earlier trade-off no longer applies.
