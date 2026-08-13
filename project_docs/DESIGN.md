# DESIGN.md

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
              ├─ GATE 1  best cosine < τ  ─────────────────────► refuse
              ├─ generate (temperature 0, numbered context, must emit chunk ids)
              ├─ GATE 2  validate ids ∈ retrieved set; none valid ─► refuse
              └─ return { answer, citations[{source, section, score}] }
```

Gate 4 lives in the grounding layer, deliberately **outside** `retrieve()` — so
retrieval quality can still be measured without it (§3).

**Embed (b) / dual embedding is built and measurable but disabled by default** —
see §4, kill criterion triggered.

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
| §6 How to store | **0.61** |
| §3 How to take | 0.32 |
| others | ≤ 0.30 |

And decisively: **22 of 30 chunks never name their own drug.** The brand appears
in the heading and never again in the body. Chunk text alone therefore carries
*zero* signal about which product it belongs to.

Two fixes, addressing orthogonal axes of the query:

- **Contextual headers** (prepended before embedding) — puts the brand into the vector.
- **BM25 + RRF hybrid** — brand names are rare literal tokens with high IDF, so
  keyword search resolves *which drug*; dense retrieval resolves *which section*.

Measured on the 11-row eval set (`python scripts/eval.py`, full table in
`eval_results.md`), retrieval-only (product filter off, isolating retrieval
itself): correct document @8 held at **7/7** whether headers were on or off, and
whether hybrid was on or off, once the product-scope filter (§4, D13) is
excluded from the comparison. The headers/hybrid contribution is therefore *not*
visible on "correct document retrieved" in this small corpus — every
configuration already gets the right document at top_k=8 of 35 chunks. Their
measured effect shows up instead in the **wrong-product refusal** numbers (§4).
The matrix's only pair isolating the filter's effect keeps dual embedding on
(`default` vs `no_filter`, per D16): filter on refuses 1/2 wrong-product cases,
filter off refuses 0/2, regardless of headers/hybrid. **This is the strongest
evidence for D13 (§4), not for headers or hybrid** — see the kill criterion note
there. (The shipped configuration disables dual embedding — §4 — so this
specific 1/2-vs-0/2 pair is retrieval-ablation data, not the shipped system's
live number; the shipped number is in §7.)

## 4. Grounding and "not in the documents"

Four layers, because no single one is sufficient:

1. **Product scope.** If the query names a brand or active ingredient, chunks
   belonging to other products are dropped before anything is scored.
2. **Similarity gate.** Best cosine < τ → refuse without calling the LLM.
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

### Dual embedding — kill criterion triggered, disabled by default

D6 proposed retrieving on a header+body vector while gating/scoring on a
body-only vector, to stop contextual headers inflating out-of-scope similarity.
**Measured against the single-vector baseline (`no_dual` config) on the full
11-row eval set:**

| Config | Correct doc | Correct section | False refusals |
|---|---|---|---|
| dual embedding (was default) | 6/7 | **2/7** | 1/7 |
| single vector (`no_dual`) | 7/7 | **7/7** | 0/7 |

The single-vector baseline is strictly better on every metric. Concretely: dual
embedding's body-only rescoring dropped "How should I store Rolip?" to cosine
0.276 — below τ — producing a **false refusal on an answerable, in-scope
question** (user story 2). The single-vector score for the same query is 0.498.

**Per D6's own stated criterion ("if the single-vector baseline separates as
cleanly, delete the dual-embed and say so"): the criterion is triggered.**
`DUAL_EMBEDDING` now defaults to `false`. The code path is kept (not deleted)
because it is the reproducible record of the ablation and `scripts/eval.py`'s
config matrix depends on it to regenerate this comparison — but production
behaviour no longer uses it. Built, measured, turned off, documented.

### Hybrid retrieval (D4/D16) — kept, weaker justification than originally argued

With the product-scope filter on, BM25 does not change which document is
retrieved (§3: 7/7 either way). Its measured contribution is on the
wrong-product refusal count, and it is retained mainly for the case D4's caveat
already narrowed it to: **unscoped queries**, where the filter never fires and
BM25's literal brand-token matching is the only mechanism connecting a
per-product answer to the right leaflet. Not deleted, but the justification is
now the narrower one from the D4 caveat, not the original two-axis argument.

**Threshold.** τ = 0.35 (`SIMILARITY_THRESHOLD`, single-vector mode). Measured
cosines for the 7 answerable rows range **0.34–0.77**; τ sits just below the
lowest answerable score, prioritising few false refusals. It is **not** a clean
in-scope/out-of-scope gap — the two absent-topic rows (alcohol, missed dose)
score **0.53–0.56**, well above τ, and the two wrong-product rows score
0.30–0.55. The gate alone does not separate "answerable" from "not answerable
for this product"; **that separation is what layers 3–4 exist for.** This is
reported honestly rather than claiming a gap that isn't there — see §7.

The reported citation `score` is cosine similarity against the indexed vector
(header+body, since dual embedding is off by default). RRF scores are
rank-based and stay internal to the retriever. Citations are sorted by cosine
descending.

**Unscoped questions.** *"How should I store this?"* names no product, so layer 1
cannot fire. The prompt instructs the model to attribute per product when
retrieved chunks span more than one source ("Doxicap: …; Rolip: …"), rather than
answer from a coin-flip product. This is a prompt-level instruction, not
structurally enforced — the eval set's stub-generator tests confirm the
mechanism wires correctly, but a live 3B model does not always follow it (§7).

Refusals return the same response shape with a fixed message and empty citations,
so the client needs no branching.

## 5. Model choices

| | Choice | Trade-off |
|---|---|---|
| Embeddings | `all-MiniLM-L6-v2`, **ONNX build** via ChromaDB | CPU, no key, deterministic, already a dependency — no torch, no `transformers`. Output is L2-normalized, so cosine is a dot product. Weak on domain pharma terms; acceptable since BM25 covers literal matches. |
| Parsing | `pypdf`, `extraction_mode="layout"` | Pure Python, no system binary (no `pdftotext`/poppler — that was the original plan and was replaced, see D19). Verified to reproduce `pdftotext -layout` on the Maxpro dose table. |
| Generation | Ollama, `qwen2.5:3b`, from `.env`. `LLM_PROVIDER=groq` is the hosted escape hatch. | ~2.1 GB resident. Thin adapter — one `generate()`, not a routing layer and not auto-detecting. |
| Decoding | `temperature=0`, `seed=42`, **JSON-schema-constrained via Ollama's `format` field** | Byte-identical output across repeated runs, verified. |

**The whole system is local.** Embeddings, retrieval and generation all run on
CPU with no API key and no network. The hosted provider exists only for a
machine that cannot spare the memory.

**Model choice is measured, not argued** — both candidates scored on the same
6 grounding cases, schema-constrained, temperature=0:

| | resident | valid JSON | fully correct | clean refusals |
|---|---|---|---|---|
| `llama3.2:3b` | 2.3 GB | 6/6 | 3/6 | 1/2 |
| **`qwen2.5:3b`** | **2.1 GB** | 6/6 | **4/6** | **2/2** |

Asked *"can I drive after taking Rolac?"*, `llama3.2:3b` answered from
**Fenadin's** driving text while citing **Rolac's** contraindications chunk —
the wrong-product failure this design is built against, produced by the model
the brief recommends. `qwen2.5:3b` refuses it, and fails in the safe direction
instead. Smaller *and* better on both axes.

### Why the output is schema-constrained

Layer 4 requires the model to return the ids of chunks it used. On a 3B local
model, asking for that in prose alone is unreliable — free-text id emission
regularly fails to parse or mixes a refusal with citations in the same
response. The Ollama adapter passes `GenerationResult`'s JSON schema via the
`format` field, constraining decoding to valid `{answered, answer, chunk_ids}`
JSON rather than relying on the prompt instruction alone.

The schema carries an explicit `answered` boolean. When false, the service emits
the fixed refusal with an empty citation list regardless of what ids came back —
refusal is a structured field, never inferred by matching the answer string.

## 6. Running it, and the hardware floor

Two documented paths: pip/venv and Docker (Dockerfile + compose.yaml).

The Docker image bakes the embedding model at build time, so a container starts
without needing network access for the embedding model. **Ollama runs on the
host, not in the container** — Compose maps `host.docker.internal` explicitly so
this works on Linux as well as Docker Desktop. Ingestion (parsing + building the
Chroma collection) runs at container **startup** against the mounted `docs/`
volume, not baked into the image — this is a deliberate choice, not a gap: it is
what makes "drop a new leaflet into docs/ and restart, no rebuild" (maintainer
story 33) actually true. Cost: ~10–20s of re-ingestion on every container start,
which the README states explicitly.

Adding a leaflet needs no rebuild in either path: mount/populate `docs/` and
restart.

**The graded core needs no model at all.** Ingestion, retrieval and the entire
evaluation harness run without Ollama installed, so the retrieval design can be
assessed before anything is downloaded — `python scripts/eval.py` and
`python scripts/measure_similarity.py` need no LLM provider.

## 7. Evaluation

11 questions (`scripts/eval.py`, results in `eval_results.md`): in-scope rows
deliberately targeting the §6/§3 collision hotspots; hard negatives on topics
verified absent from every leaflet (alcohol, missed dose); and two asymmetry
pairs where a topic **is** in the corpus but absent from the leaflet being asked
about — children and driving.

**Shipped configuration** is the `no_dual` row of `eval_results.md` (single
vector, filter on — the new default per §4's kill-criterion fix):

| Result | |
|---|---|
| Correct doc retrieved @8 | 7/7 |
| Correct section cited | 7/7 |
| False refusals on in-scope | 0/7 |
| Correct refusals, topic absent everywhere (gate alone) | 0/2 |
| Correct refusals, topic present in another product (gate alone) | 0/2 |

**What the gate alone does and doesn't catch — reported honestly.** These last
two rows measure **only** the similarity gate (layer 2), by design — `retrieve()`
plus the product-scope filter, no LLM call. Read literally, the gate on its own
refuses neither the absent-topic rows nor the wrong-product rows: absent-topic
questions (alcohol, missed dose) retrieve a plausible same-product passage at a
cosine similar to genuinely answerable questions, and the wrong-product row 7
("Is Rolac safe for children?") retrieves Rolac's own "Before you take" section
— same product, adjacent-but-wrong topic — at a cosine comfortably above τ. **τ
was calibrated to avoid false-refusing the 7 answerable rows (which score
0.34–0.77), not to separate answerable from unanswerable** — no such clean gap
exists in this corpus, and claiming one would misrepresent the measurement (an
earlier draft of this document asserted a gap that the data does not support;
corrected here). This is exactly why layers 3–4 (prompt constraint,
cite-or-refuse) exist as independent defenses rather than relying on the gate
alone — the eval harness deliberately measures the gate in isolation so this
distinction is visible instead of folded into one end-to-end pass rate.

Manually testing the full pipeline (`POST /ask`, live Ollama `qwen2.5:3b`,
temperature 0) showed layers 3–4 correctly refusing both absent-topic rows
(alcohol, missed dose) — the LLM declines to answer from a passage that
doesn't address the question even though it passed the gate. The wrong-product
row 7 is the harder, **unresolved** failure: the model answered "No" from
Rolac's contraindications text, which doesn't specifically address children,
and cite-or-refuse did not catch it because the cited chunk genuinely was
retrieved — this is a same-product, wrong-topic failure that product-scope
filtering (layer 1) cannot catch by construction (it only removes *other*
products). Known, unresolved, and the top item in §8's next-steps list.

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

Two things were built, measured, and reported either way, per the kill-criterion
commitment in §4:

- **Dual embedding (D6): kill criterion triggered.** Single-vector baseline beat
  it on every metric (7/7 vs 2/7 correct section, 0/7 vs 1/7 false refusals).
  Disabled by default; code kept for the reproducible ablation.
- **Hybrid retrieval (D4/D16): kept, narrower justification.** With the product
  filter on, it does not change which document is retrieved (7/7 either way in
  this corpus); retained for unscoped queries, where the filter never fires and
  BM25's brand-token matching is the only product-disambiguation signal left.

With more time, in priority order:
1. The row-7 failure class (right product, wrong-but-adjacent section) —
   likely needs a stricter prompt constraint ("does this specific passage
   address the question's specific topic, not just its general subject") or a
   small labelled-negative set per section to detect topic mismatch, since
   product-scope filtering structurally cannot catch it.
2. Verify D14 per-product attribution holds reliably on the live model, not
   just the stub — add a Seam-1 style test that exercises the real Ollama
   adapter for the unscoped-question path, or accept and document it as a
   known best-effort prompt instruction.
3. A larger eval set with a per-section breakdown, once row 1's priority is
   addressed.

## 9. Assumptions

- 5 leaflets were provided in `docs/`; the numbered 1–6 heading scheme is
  assumed stable for any leaflet added later, and ingestion **fails loudly** if a
  document does not match it rather than indexing unusable chunks.
- Page-break characters (`\f`) appear immediately before some headings and are
  stripped before section detection — without this, headings were silently
  missed in 2 of the 5 source documents during initial parsing.
- Product scope is resolved by literal brand and active-ingredient matching, so a
  question that refers to a product without naming it is treated as unscoped.
- File I/O is explicitly UTF-8; the platform default on Windows is cp1252 and
  corrupts the leaflets' punctuation.
- Single-turn Q&A; no user accounts or persistence between requests.
