# DESIGN.md

> **Fill-in markers:** `«...»` = replace with a measured number or a sentence.
> Delete this block before submitting. Target length: ~1 page. If a section
> grows past 6 lines, cut it.

---

## 1. Architecture

```
docs/*.pdf
   │
   ├─ parse        pdftotext -layout  (preserves dose tables)
   ├─ clean        strip \f page-breaks, strip trailing disclaimer
   ├─ section      regex on numbered headings 1–6  +  synthetic §0
   │
   ├─ embed (a)    "Brand (Ingredient) — Section" + body   → ChromaDB
   ├─ embed (b)    body only                               → in-memory dict
   └─ index        BM25 over the same header+body text
                                │
POST /ask ──► encode query once
              ├─ vector top-k ─┐
              │                ├─ RRF fuse ──► top_k=5
              ├─ BM25 top-k ───┘
              │
              ├─ GATE 1  body-only cosine < τ  ──────────────► refuse
              ├─ generate (temperature 0, numbered context, must emit chunk ids)
              ├─ GATE 2  validate ids ∈ retrieved set; none valid ─► refuse
              └─ return { answer, citations[{source, section, score}] }
```

## 2. Chunking strategy

**One chunk per leaflet section. No splitting, no overlap.**

Measured across the provided leaflets:

- All 6 canonical sections present in every document.
- Section length: min 21w, median 39w, max 96w — «n» chunks total, «N» words corpus-wide.
- Nothing approaches an embedding-window limit, so splitting would only fragment
  semantically complete units and make the `section` citation field approximate
  instead of exact.

A synthetic **§0 "Product overview"** captures the header block (brand, active
ingredient, form, manufacturer, pack & price), which sits above section 1 and
would otherwise be unretrievable.

The trailing legal disclaimer is identical in all documents and ~20 words long —
on a 39-word chunk that is 35% noise, so it is stripped at parse time.

## 3. The core retrieval problem

The leaflets are structurally identical, so the risk is **cross-document
confusion**, not chunk sizing. Two measurements drove the design:

| Section | mean same-section cross-drug cosine (TF-IDF) |
|---|---|
| §6 How to store | **0.82** |
| §3 How to take | 0.41 |
| others | ≤ 0.15 |

And decisively: **19 of 24 chunks never name their own drug.** The brand appears
in the heading and never again in the body. Chunk text alone therefore carries
*zero* signal about which product it belongs to.

Two fixes, addressing orthogonal axes of the query:

- **Contextual headers** (prepended before embedding) — puts the brand into the vector.
- **BM25 + RRF hybrid** — brand names are rare literal tokens with high IDF, so
  keyword search resolves *which drug*; dense retrieval resolves *which section*.
  Neither does both.

«One line on measured impact: retrieval accuracy on the eval set with headers
off / on, and hybrid off / on.»

## 4. Grounding and "not in the documents"

Three layers, because no single one is sufficient:

1. **Similarity gate.** Best body-only cosine < τ → refuse without calling the LLM.
2. **Prompt constraint.** Numbered context, answer-only-from-context, must emit
   the ids of chunks used.
3. **Cite-or-refuse.** Returned ids are validated against the retrieved set;
   unknown ids dropped; if none survive, the answer is discarded and the service
   refuses. This catches questions that retrieve plausibly but aren't actually
   covered — e.g. *"can I drink alcohol with X?"* pulls §2 at decent similarity,
   but §2 says nothing about alcohol.

**Known trade-off.** Contextual headers inflate similarity for any query naming a
brand, including out-of-scope ones — compressing exactly the gap τ depends on.
Mitigated by gating on a **body-only** vector while retrieving on header+body.
Measured separation: «in-scope min cosine» vs «out-of-scope max cosine»; single-
vector baseline gave «...». τ set to «0.xx».

«If the single-vector baseline separated just as cleanly, say so and note the
dual-embed was removed.»

The reported citation `score` is the body-only cosine — the same number the gate
uses, so what the user sees is what the system trusted. RRF scores are rank-based
and stay internal to the retriever.

## 5. Model choices

| | Choice | Trade-off |
|---|---|---|
| Embeddings | `all-MiniLM-L6-v2` | CPU, no key, deterministic. Weak on domain pharma terms; acceptable since BM25 covers literal matches. |
| Generation | «Groq / model» by default, Ollama «model» via `LLM_PROVIDER` | Hosted default keeps the README's 5-minute promise; local fallback works offline. Thin adapter — one `generate()` function, not a routing layer. |
| Temperature | 0 | Reproducible across runs. |

Embeddings and retrieval are **fully local** — ingestion and retrieval can be
verified with no API key at all; only generation needs a provider.

## 6. Evaluation

«n» questions: in-scope rows deliberately targeting the §6/§3 collision
hotspots, plus hard negatives on topics absent from every leaflet (alcohol,
missed dose, overdose, driving).

| Result | |
|---|---|
| Correct doc retrieved @5 | «x/y» |
| Correct section cited | «x/y» |
| Correct refusals on hard negatives | «x/y» |
| False refusals on in-scope | «x/y» |

«One or two sentences on what failed and why — a specific honest failure is
worth more here than a clean sweep.»

## 7. Scope, and what I would do next

Deliberately **not** built, with reasons:

- **Reranking** — top_k=5 of «n» chunks is already a fifth of the corpus;
  reordering that fraction is noise, and it costs a second model download.
- **Query rewriting / HyDE** — an extra LLM call and failure mode for questions
  that are one literal sentence.
- **Graph orchestration** — the pipeline is linear. It would earn its place at
  the point where a failed τ gate triggers retrieval retry with a rewritten
  query; that is the first thing I would add.
- **Docker** — a cold build installing torch runs well past the 5-minute setup
  claim, so it would undercut a core requirement to satisfy an optional one.
- **Multi-turn memory, streaming, auth, hosted vector DB** — out of scope per brief.

With more time, in priority order: «1» cross-document synthesis for comparative
questions ("which of these are unsafe in pregnancy?"), which currently returns a
partial answer from top_k; «2» a larger eval set with per-section breakdown;
«3» «...».

## 8. Assumptions

- «n» leaflets were provided in `docs/`; the numbered 1–6 heading scheme is
  assumed stable for any leaflet added later, and ingestion falls back to
  «behaviour» if a document does not match it.
- Page-break characters (`\f`) appear immediately before some headings and are
  stripped before section detection — without this, «k» headings are silently missed.
- Single-turn Q&A; no user accounts or persistence between requests.
