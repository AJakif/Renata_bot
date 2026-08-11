# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**No application code exists yet.** The repo contains only planning documents and source PDFs.
There is no `pyproject.toml`/`requirements.txt`, no test suite, and no build or lint config — do
not assume a command works; check first, and add tooling deliberately rather than inferring it.

Available locally: Python 3.13.7, `pdftotext` 4.00 (poppler). Note `uv` is **not** installed,
though `.claude/CLAUDE.md` prefers it — install it or fall back to pip when setting the project up.

- `project_docs/PRD_Leaflet_Assistant.md` — the specification (problem, user stories, implementation
  and testing decisions, out-of-scope list). **The binding document.**
- `project_docs/PLAN_AND_DECISIONS.md` — decision log D1–D12 with the *why* and the rejected
  alternatives, plus EDA measurements. Working doc, not a deliverable.
- `project_docs/DESIGN.md` — the ~1-page submission deliverable. Still a template: `«...»` markers
  are placeholders that must be replaced with **measured** numbers, and the fill-in instruction
  block at the top must be deleted before submitting.
- `docs/*.pdf` — the 5 medicine leaflets (doxicap, fenadin, maxpro, rolac, rolip).

Note: the planning docs' measurements (24 chunks, 1,477 words, the cosine tables) were taken over
**4** leaflets — `fenadin` was missing at design time and is now present. Any number quoted from
those docs is stale until re-measured over all 5.

## What is being built

A grounded RAG service over the leaflets: FastAPI `POST /ask` → answer + citations
(`source`, `section`, `score`), or an explicit refusal. Single static page UI. Take-home
assignment, ~5h budget, and shortlisted candidates modify it live — so **restraint and
explainability outrank sophistication**. The PRD's "Out of Scope" list is a set of decisions
already made (no reranker, no LangGraph, no Docker, no multi-turn, no query rewriting); do not
re-open them without being asked.

## Architecture and its load-bearing decisions

Linear pipeline: parse → clean → section → embed + BM25 index → retrieve → gate → generate → gate.

The corpus's defining property, and the reason for most of the design: **the leaflets are
structurally identical and 19 of 24 chunk bodies never name their own drug** (the brand appears
only in the heading). Same-section cross-drug cosine reaches 0.82 for "How to store". The risk is
cross-document confusion, not chunk sizing. Consequences:

- **One chunk per numbered section, no splitting/overlap.** Sections are 21–96 words; the citation
  contract requires an *exact* section name, not an inferred one.
- **Synthetic §0 "Product overview"** for the header block above section 1 (brand, ingredient,
  form, manufacturer, pack, price) — otherwise unretrievable.
- **Contextual headers** (`Brand (Ingredient) — Section`) prepended *before embedding*. Without
  this, retrieval on the drug axis is near a coin flip.
- **Hybrid BM25 + dense with RRF fusion.** Queries have two orthogonal axes: BM25 resolves *which
  drug* (brand names are rare, high-IDF), dense resolves *which section*. Neither does both.
- **Dual embedding.** Contextual headers inflate similarity for *any* query naming a brand,
  including out-of-scope ones — compressing the very gap the threshold depends on. So: retrieve on
  the header+body vector, gate τ on a **body-only** vector held in memory. This has an explicit
  **kill criterion** — if the single-vector baseline separates as cleanly on the eval set, delete
  the dual embedding and document the removal.
- **Two scores, two jobs.** RRF scores are rank-derived, never leave the retriever. The body-only
  cosine is both the reported citation `score` and the value τ gates on — the number shown is the
  number trusted. Sort citations by cosine descending (ranking by RRF while reporting cosine emits
  non-monotonic scores that read as a bug).

### Grounding — three layers, all required

1. Similarity gate: best body-only cosine < τ → refuse without calling the LLM.
2. Prompt constraint: numbered context, answer-only-from-context, must emit ids of chunks used,
   temperature 0.
3. Cite-or-refuse: validate returned ids against the retrieved set, drop unknown ids, refuse if
   none survive.

Layer 1 alone is insufficient — "can I drink alcohol with Maxpro?" retrieves §2 at decent
similarity but §2 says nothing about alcohol. Layer 3 without id validation degrades into
"hallucinate-a-citation-and-pass".

Refusals return the **same response shape** with a fixed message and empty citations, so the client
needs no branching.

### Known parsing traps

- Page breaks insert `\f` immediately before some headings (`\f4. Possible side effects`). A naive
  `^\d+\.` regex silently dropped 1 of 6 headings in 2 of 4 documents. **Strip `\f` before heading
  detection.**
- Use layout-preserving extraction (`pdftotext -layout`) to keep the Maxpro dose table readable.
- Strip the identical trailing legal disclaimer (~20 words against a 39-word median chunk).
- ChromaDB defaults to L2 space and returns *distance*, not similarity; the habitual `1 - distance`
  yields negative "similarities" that silently break the gate. Set
  `metadata={"hnsw:space": "cosine"}` explicitly.

## Testing seams

Two seams, deliberately kept apart so retrieval quality is never entangled with generation:

- **Seam 1 — `POST /ask` with the LLM provider injected.** A stub `generate()` makes the grounding
  chain deterministic. Covers response shape, both refusal paths, id discarding, citation ordering,
  repeat determinism.
- **Seam 2 — `retrieve(query)`.** Real embeddings, no LLM. Covers the eval set's expected
  source/section per question, τ calibration data, and the headers-on/off and hybrid-on/off
  comparisons.

**Ingestion is deliberately not a seam** — parsing defects surface as Seam 2 failures, plus one
startup assertion on chunk count and section coverage per document.

Assert observable behaviour (the caller's response, the chunks a query returns), never the internal
shape of fusion, prompt construction, or storage — tests must survive live modification.

The 8-row eval set (D10 in `PLAN_AND_DECISIONS.md`) is adversarial by construction: in-scope rows
target the measured collision hotspots, hard negatives use topics verified absent from every
leaflet (alcohol, missed dose, overdose, driving), and "is X safe for children?" must be answered
for Doxicap/Rolip and refused for Rolac. Rows 6–8 calibrate τ. **If there is no separation gap,
that is itself the finding** — report it honestly; it is the argument for why layer 3 exists.

## Relationship to `.claude/CLAUDE.md`

`.claude/CLAUDE.md` is a generic Python house-rules file carried over from another project. Most of
its on-demand pointers **do not exist here** — `.claude/references/python-stack.md`,
`.claude/rules/core.md`, `.claude/rules/testing.md`, `.claude/domains/`, `.claude/CHANGELOG_AI.md`,
`architecture/*`, and the `dev_test_infra_blockers.md` auto-memory are all absent. Don't chase them.

What genuinely applies: the tooling table (**uv**, **ruff** + `ruff format`, **mypy strict**,
**pytest**) — use it when adding project tooling — plus strict modern typing (`list[X]`, `X | None`,
no legacy `List`/`Optional`), no bare `except:`, and timeouts on every external call (the LLM
provider call).

What does not apply: the repository → service → API layering rule and all ORM/SQLAlchemy
non-negotiables. There is no database here — Chroma persists to disk and the body-vector map is
in memory. This project's module boundaries are the ones in the Architecture section above.

## Working conventions

- Embeddings and retrieval must stay **fully local** (MiniLM on CPU) so an evaluator with no API
  key can verify ingestion and retrieval. Only generation needs a provider: one
  `generate(prompt) -> str` behind `LLM_PROVIDER` (Groq default, Ollama offline) — a thin adapter,
  not a routing layer or fallback chain.
- `temperature=0` everywhere. The same question must give the same answer twice.
- Adding a leaflet means dropping a PDF in `docs/` and re-running ingestion — no code change. A
  document that doesn't match the 1–6 heading scheme should fail loudly, not index silently into
  unusable chunks. τ and `top_k` (=5) are configurable without a code change.
- When a design claim is asserted in `DESIGN.md`, back it with a measured number from an actual
  eval run; the open items at the end of `PLAN_AND_DECISIONS.md` list what still needs measuring.
