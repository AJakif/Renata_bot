# Python Stack — House Rules

> Non-negotiables only. Concrete patterns/examples live in `.claude/references/python-stack.md`
> (load on demand — do not preload every session). Per-domain context: `.claude/domains/*.md`
> via the Module Map in the root `CLAUDE.md`.

## Token discipline
Canonical rules: `.claude/rules/core.md`. Project-specific: if a user pastes >150 lines / >10k chars, ask them to move it to a file and continue from `@file`; redirect large command output to a file (`>`, `2>`, `| tee`), then read it.

## Before architecture-affecting work
Read on demand (NOT every session): `architecture/system-model.yaml`, `.claude/CHANGELOG_AI.md`.
Human overview: `architecture/architecture-summary.md`; quick ref: `architecture/architecture-compact.md`.
If they're stale vs. the latest changes, run `/update-summary` first. When a change affects
architecture, API contracts, DB schema, workflows, or module responsibilities, update those files in the same PR.

## Runtime & Tooling
| Component | Choice |
|-----------|--------|
| Runtime | Python 3.11+ |
| Package Manager | uv (preferred) / poetry / pip |
| Type Checker | mypy (strict) / pyright |
| Linter / Formatter | ruff / ruff format |
| Test Runner | pytest |

## Non-negotiables
- **Typing:** strict mypy; modern syntax (`list[X]`, `dict[K,V]`, `X | None`); complete annotations on public functions; no bare `Any` without a justification comment; no legacy `List`/`Dict`/`Optional`.
- **Layering:** repository → service → API. No DB access outside repositories. Models don't import services; schemas don't import repositories.
- **Async:** every external call has a `timeout`; no blocking I/O (`requests`, `time.sleep`) in `async`; no fire-and-forget `create_task`; rollback on partial failure.
- **Errors:** no bare `except:` / `except Exception: pass`; catch at the right boundary and propagate with context.
- **ORM:** relationships default `lazy="raise"` — eager-load explicitly with `selectinload()`/`joinedload()` in the repository (see `.claude/domains/evaluation.md` for the production bug this prevents).
- **Quality gate (touched files):** `ruff check`, `ruff format`, `mypy` clean.

## Examples & framework patterns
`.claude/references/python-stack.md` — type hints, Result/error pattern, Pydantic validation,
async patterns, pytest structure, FastAPI, async SQLAlchemy. Load it when you need a concrete template.

## Tests
Load-bearing only. Canonical policy: `.claude/rules/testing.md`.

## Project-specific test invocation
The dev-Docker pytest quirks (`-o asyncio_mode=auto`, `--noconftest`, live-mounted `app/` only)
are recorded in auto-memory `dev_test_infra_blockers.md` — defer to it over the generic commands.
