# Domain Documentation

This repository uses a **single-context** layout.

## Structure

- **`CONTEXT.md`** at the repo root — domain language, key concepts, glossary
- **`docs/adr/`** — architectural decision records (ADRs)

## Consumer rules

When a skill needs domain context:

1. Read `CONTEXT.md` first to understand the domain language
2. Check `docs/adr/` for relevant architectural decisions
3. If `CONTEXT.md` doesn't exist yet, the skill may offer to create it

## Creating ADRs

Use a standard ADR template (title, status, context, decision, consequences). Number them sequentially: `0001-initial-architecture.md`, `0002-database-choice.md`, etc.
