# DESIGN.md

> **Fill-in markers:** `«...»` = replace with a measured number from an actual
> eval run. Delete this block before submitting. Target length: ~1 page.
> If a section grows past 6 lines, cut it.

---

## 1. Architecture

```
docs/*.pdf
   │
   ├─ parse        pypdf, extraction_mode="layout"   (pure python, no binary)
   ├─ clean        strip \f page-breaks, strip trailing disclaimer,
   │               normalize unicode dashes/quotes
   ├─ section      regex on numbered headings 1–6  +  synthetic §0
   │
   ├─ embed (a)    "Brand (Ingredient) — Section" + body  → ChromaDB (cosine)
   ├─ embed (b)    body only                              → numpy (35, 384)
   └─ index        BM25 over the same header+body text
                                │
POST /ask ──► encode query once
              ├─ vector top-k ─┐
              │                ├─ RRF fuse ──► top_k=8
              ├─ BM25 top-k ───┘
              │
              ├─ GATE 4  query names a brand? drop other products' chunks
              ├─ GATE 1  best body-only cosine < τ  ──────────────► refuse
              ├─ generate (temperature 0, numbered context, must emit chunk ids)
              ├─ GATE 2  validate ids ∈ retrieved set; none valid ─► refuse
              └─ return { answer, citations[{source, section, score}] }
```

Gate 4 lives in the grounding layer, deliberately **outside** `retrieve()` — so
retrieval quality can still be measured without it (§3).

## 2. Chunking strategy

**One chunk per leaflet section. No splitting, no overlap.**

Measured across all five leaflets:

- All 6 canonical sections present in every document.
- Section length: min 17w, median 40w, max 97w — 30 numbered sections plus 5
  synthetic §0, **35 chunks, 1,572 words** corpus-wide.
- Nothing approaches an embedding-window limit, so splitting would only fragment
  semantically complete units and make the `section` citation field approximate
  instead of exact.

A synthetic **§0 "Product overview"** captures the header block (brand, active
ingredient, form, manufacturer, pack & price), which sits above section 1 and
would otherwise be unretrievable. The `section` value is synthetic and not
literally in the document.

The trailing legal disclaimer is identical in all documents and ~20 words long —
on a 40-word chunk that is a third noise, so it is stripped at parse time.

## 3. The core retrieval problem

The leaflets are structurally identical, so the risk is **cross-document
confusion**, not chunk sizing. Two measurements drove the design:

| Section | mean same-section cross-drug cosine (TF-IDF) |
|---|---|
| §6 How to store | **«0.82»** |
| §3 How to take | «0.41» |
| others | ≤ «0.15» |

And decisively: **22 of 30 chunks never name their own drug.** The brand appears
in the heading and never again in the body. Chunk text alone therefore carries
*zero* signal about which product it belongs to.

Two fixes, addressing orthogonal axes of the query:

- **Contextual headers** (prepended before embedding) — puts the brand into the vector.
- **BM25 + RRF hybrid** — brand names are rare literal tokens with high IDF, so
  keyword search resolves *which drug*; dense retrieval resolves *which section*.

Measured on the eval set, with the product filter **off** so this isolates
retrieval itself: correct document @8 went from «x/y» to «x/y» with headers on,
and «x/y» to «x/y» with hybrid on.

## 4. Grounding and "not in the documents"

Four layers, because no single one is sufficient:

1. **Product scope.** If the query names a brand or active ingredient, chunks
   belonging to other products are dropped before anything is scored.
2. **Similarity gate.** Best body-only cosine < τ → refuse without calling the LLM.
3. **Prompt constraint.** Numbered context, answer-only-from-context, must emit
   the ids of chunks used.
4. **Cite-or-refuse.** Returned ids are validated against the retrieved set;
   unknown ids dropped; if none survive, the answer is discarded and the service
   refuses. This catches questions that retrieve plausibly but aren't covered —
   *"can I drink alcohol with X?"* pulls §2 at decent similarity, but §2 says
   nothing about alcohol.

**Why layer 1 exists — the failure the other three cannot catch.** Fenadin's §2
covers driving; no other leaflet does. Asked *"can I drive after taking Rolac?"*,
retrieval surfaces Fenadin §2 at high similarity (layer 2 passes), the model
answers from it, and the id it cites **was** in the retrieved set (layer 4
passes). The result is a confident answer about Rolac sourced from Fenadin, with
an honest citation above a wrong answer. Layers 2–4 defend the *topic* axis and
say nothing about the *product* axis. The same trap exists for "is X safe for
children?", which is answerable for three products and absent from two.

**Known trade-off.** Contextual headers inflate similarity for any query naming a
brand, including out-of-scope ones — compressing exactly the gap τ depends on.
Mitigated by gating on a **body-only** vector while retrieving on header+body.
Layer 1 makes this *more* necessary, not less: once filtered to one product,
every surviving chunk shares that brand in its header, so header+body cosine is
uniformly inflated and body-only is the only discriminative signal left.
Measured separation: «in-scope min cosine» vs «out-of-scope max cosine»; single-
vector baseline gave «...». τ set to «0.xx».

The reported citation `score` is the body-only cosine — the same number the gate
uses, so what the user sees is what the system trusted. RRF scores are rank-based
and stay internal to the retriever. Citations are sorted by cosine descending.

**Unscoped questions.** *"How should I store this?"* names no product, so layer 1
cannot fire and neither headers nor BM25 have a brand to match. Rather than
answer from a coin-flip product, the response attributes per product
("Doxicap: …; Rolip: …") and cites each. Attribution, not synthesis — comparative
reasoning across documents remains out of scope.

Refusals return the same response shape with a fixed message and empty citations,
so the client needs no branching.

## 5. Model choices

| | Choice | Trade-off |
|---|---|---|
| Embeddings | `all-MiniLM-L6-v2`, **ONNX build** via ChromaDB | CPU, no key, deterministic, already a dependency — no torch, no `transformers`. ~370 MB total stack vs ~2.5 GB. Output is L2-normalized, so cosine is a dot product. Weak on domain pharma terms; acceptable since BM25 covers literal matches. |
| Parsing | `pypdf`, `extraction_mode="layout"` | Pure Python, ~4 MB, no system binary. Verified to reproduce `pdftotext -layout` on the Maxpro dose table. |
| Generation | Provider and model both from `.env`. Default `LLM_PROVIDER=groq`; `LLM_PROVIDER=ollama` («model») is the offline path. | Thin adapter — one `generate()`, not a routing layer and not auto-detecting. |
| Temperature | 0 | Reproducible across runs. |

Embeddings and retrieval are **fully local** — ingestion and retrieval can be
verified with no API key at all; only generation needs a provider.

**Deviation from the brief, stated deliberately.** The brief recommends a local
Ollama model and lists free-tier hosted as also fine. This ships hosted as the
*default* because the brief's five-minute install-and-run requirement and its
LLM preference conflict on a modest laptop: even `llama3.2:3b` is a ~2 GB pull
plus a running daemon. A hard setup requirement outranks a stated preference,
and the preference is satisfied by one environment variable — the local path is
documented in the README, not an afterthought. No auto-detection: a provider
that varies by machine would undercut the reproducibility that `temperature=0`
exists to guarantee.

## 6. Running it, and the hardware floor

Two documented paths; the five-minute claim is measured for each, not asserted.

| | pip + venv | Docker |
|---|---|---|
| Disk | ~370 MB | ~«750» MB image |
| RSS, hosted LLM | ~«300» MB — fits 4 GB | same |
| Cold start | «0.4» s | «0.4» s |
| Setup time | «m:ss» | «m:ss» |

The image bakes both the embedding model and the built index at build time, so a
container starts ready and needs no network at runtime. Compose wires
`host.docker.internal` so the Ollama path reaches a daemon on the host, including
on Linux. Adding a leaflet needs no rebuild: mount `docs/` and run ingestion.

## 7. Evaluation

11 questions: in-scope rows deliberately targeting the §6/§3 collision hotspots;
hard negatives on topics verified absent from every leaflet (alcohol, missed
dose, overdose); and two asymmetry pairs where a topic **is** in the corpus but
absent from the leaflet being asked about — children and driving.

| Result | |
|---|---|
| Correct doc retrieved @8 | «x/y» |
| Correct section cited | «x/y» |
| Correct refusals, topic absent everywhere | «x/y» |
| Correct refusals, topic present in another product | «x/y» |
| False refusals on in-scope | «x/y» |

The two refusal rows are reported separately on purpose: the first is what τ can
catch, the second is what τ cannot catch by construction.

«One or two sentences on what failed and why — a specific honest failure is
worth more here than a clean sweep.»

## 8. Scope, and what I would do next

Deliberately **not** built, with reasons:

- **Reranking** — top_k=8 of 35 chunks is nearly a quarter of the corpus;
  reordering that fraction is noise, and it costs a second model download.
- **Query rewriting / HyDE** — an extra LLM call and failure mode for questions
  that are one literal sentence.
- **Graph orchestration** — the pipeline is linear. It would earn its place at
  the point where a failed τ gate triggers retrieval retry with a rewritten
  query; that is the first thing I would add.
- **Cross-document synthesis** — comparative questions get per-product
  attribution, not comparison.
- **Multi-turn memory, streaming, auth, hosted vector DB** — out of scope per brief.

Two things were built, measured, and are reported either way: the dual embedding
(§4) and hybrid retrieval (§3). Each carries a kill criterion — if the simpler
baseline separates as cleanly, it is deleted and the removal documented here.
«State the outcome for each.»

With more time, in priority order: «1» cross-document synthesis for comparative
questions ("which of these are unsafe in pregnancy?"); «2» a larger eval set with
per-section breakdown; «3» «...».

## 9. Assumptions

- 5 leaflets were provided in `docs/`; the numbered 1–6 heading scheme is
  assumed stable for any leaflet added later, and ingestion **fails loudly** if a
  document does not match it rather than indexing unusable chunks.
- Page-break characters (`\f`) appear immediately before some headings and are
  stripped before section detection — without this, «k» headings are silently
  missed (observed in 2 of 5 documents).
- Product scope is resolved by literal brand and active-ingredient matching, so a
  question that refers to a product without naming it is treated as unscoped.
- File I/O is explicitly UTF-8; the platform default on Windows is cp1252 and
  corrupts the leaflets' punctuation.
- Single-turn Q&A; no user accounts or persistence between requests.
