# Renata Take-Home — Plan & Decision Log

Working document. Not for submission — `DESIGN.md` is the deliverable.
This records *why* each decision was made, what was rejected, and what still
needs measuring.

---

## 1. How the brief is being read

The task is a small grounded RAG service over 5 medicine leaflets: FastAPI
`POST /ask`, citations, honest refusal, barebones UI, README + DESIGN.md.
Budget ~4–5 hours.

Two things in the brief carry more weight than they first appear:

- **The citation schema includes `"section": "How to take"`.** That is not
  decoration — it makes section-awareness a hard requirement of the output
  contract, not a stylistic choice about chunking.
- **"Shortlisted candidates will make a small change to it live."** Every
  decision is therefore judged twice: does it work, and can it be explained
  and modified under pressure. This is the main argument against cleverness.

The brief also warns against over-building three separate times. Given the
stack I work in daily (LangGraph, multi-agent, MCP), restraint is the specific
risk to manage here.

## 2. Evidence base — EDA findings

**Resolved: all 5 leaflets are present** — `doxicap`, `fenadin`, `maxpro`,
`rolac`, `rolip`. The original EDA ran over 4 (`fenadin` was missing at design
time). Every number below has been **re-measured over all 5** except the TF-IDF
collision table, which is flagged inline.

**Documents are clean.** All are pandoc → WeasyPrint output. Single column,
embedded fonts, real text layer. No OCR risk, no multi-column reflow problem.

**Structure is rigid.** All six sections present in all five documents:

```
1. What {Brand} is and what it is used for
2. Before you take {Brand}
3. How to take {Brand}
4. Possible side effects
5. Use in pregnancy and breast-feeding
6. How to store {Brand}
```

**Gotcha:** page breaks insert a form-feed immediately before some headings
(`\f4. Possible side effects`). A naive `^\d+\.` regex silently misses these —
it dropped 1 of 6 headings in two of the four documents. Strip `\f` first.

**The corpus is tiny.** Measured over all 5, disclaimer stripped, §0 synthesized:

| | over 4 (stale) | **over 5 (current)** |
|---|---|---|
| Numbered sections | 24 | **30** |
| Total chunks incl. §0 | 28 | **35** |
| Corpus words | 1,477 | **1,572** |
| Section words min / median / max | 21 / 39 / 96 | **17 / 40 / 97** |
| Chunks never naming own brand | 19/24 (79%) | **22/30 (73%)** |

Nothing needs splitting. 35 chunks × 384 dims = **53 KB of float32** — the
entire corpus fits in a numpy array smaller than this document.

**Cross-document collision is the real risk.** Same-section, different-drug
TF-IDF cosine — ⚠️ **measured over 4 documents; MUST be re-run over 5** before
any of these numbers appears in `DESIGN.md`. A fifth §6 chunk (Fenadin's, two
lines of near-boilerplate) enters exactly the worst-colliding cluster:

| Section | avg | worst |
|---|---|---|
| §6 How to store | **0.82** | 0.85 (doxicap vs rolac) |
| §3 How to take | 0.41 | 0.56 (maxpro vs rolip) |
| §5 Pregnancy | 0.15 | 0.19 |
| §2 Before you take | 0.14 | 0.37 |
| §4 Side effects | 0.12 | 0.30 |
| §1 What it is | 0.04 | 0.06 |

All five worst-colliding pairs in the corpus are §6 vs §6. Storage instructions
are near-boilerplate across products. Dense embeddings will collapse these
harder than TF-IDF does.

**The decisive finding: 22 of 30 chunks never name their own drug.** The brand
appears in the heading and then never again in the body. So for "how should I
store Rolip?", the chunk *text* contains no signal at all about which product
it belongs to. This is an information-availability problem, not a tuning
problem — no amount of threshold work fixes it. The finding survived the fifth
document at full strength.

**Two cleanup items.** Every document ends with an identical ~20-word
disclaimer (35% noise on a 39-word chunk) → strip at parse. The header block
above section 1 (brand, active ingredient, form, manufacturer, pack & price,
~54w) belongs to no numbered section → currently unretrievable, so it becomes a
synthetic §0.

**Absent topics, re-verified across all five documents:** alcohol, missed dose,
overdose. These are standard PIL sections these leaflets lack, making them ideal
hard negatives — an LLM's parametric knowledge will answer them confidently,
which is exactly what grounding is being tested on.

**Correction — "driving" was wrong.** It was listed as absent based on the
4-document EDA. Fenadin is an antihistamine and its §2 addresses it directly:

> "This medicine is unlikely to affect your ability to drive or use machines,
> but check how it affects you first."

So driving is present in exactly one of five leaflets. It is **promoted from a
hard negative to a second asymmetry pair** (see D10) — which is a strictly
*harder* test than a topic absent everywhere, because retrieval will surface a
genuinely on-topic chunk from the wrong product.

**Two asymmetries, both testing grounding and disambiguation at once:**

| Topic | Answerable for | Must refuse for |
|---|---|---|
| children | Doxicap §2 (under-8s), Rolip §1 (10–17), Fenadin §1/§3 (12+) | Rolac, Maxpro |
| driving | Fenadin §2 | Doxicap, Maxpro, Rolac, Rolip |

These two rows are the sharpest in the eval set: the topic exists in the corpus
but not in the leaflet being asked about, so every retrieval-side mechanism
returns something plausible and only the product-scope rules can refuse
correctly.

---

## 3. Decision log

### D1 — Chunking: one chunk per section, no splitting

**Considered:** (a) section-aware then recursive split within section;
(b) fixed-size recursive split, infer section post-hoc; (c) whole-section chunks.

**Chose (c).** Initially leaned (a), assuming "Possible side effects" would be
a wall of text. EDA disproved it — that section is 21–43 words. With a 96-word
maximum there is nothing to split, and splitting would fragment semantically
complete units while making the `section` citation approximate rather than exact.

**Rejected (b)** because inferring the section after the fact is fragile and
produces exactly the citation field the brief asks for, badly.

### D2 — Synthetic §0 "Product overview"

The preamble block sits above section 1 and matches no heading. Without it,
"what does Maxpro cost?" or "who manufactures Rolac?" are unanswerable despite
the information being present. Cost: three lines. The `section` value is
synthetic and not literally in the document — flagged honestly in DESIGN.md.

### D3 — Contextual headers before embedding

Prepend `Maxpro 20 mg (Esomeprazole) — How to store` to chunk text before
encoding. Directly addresses the 19/24 finding. Not a preference — without it
retrieval on the drug axis is close to a coin flip across four candidates, and
the model will cite the wrong leaflet confidently.

### D4 — Hybrid retrieval: BM25 + dense, RRF fusion

**Framing that justifies it:** queries have two orthogonal axes — *which drug*
and *which section*. BM25 resolves the drug (brand names are rare literal
tokens, high IDF). Dense embedding resolves the section (semantic,
paraphrase-tolerant). Neither does both. This is a stronger argument than
"hybrid generally helps," and it is specific to this corpus.

**Caveat added after D13.** A deterministic product filter does BM25's stated
job — resolving *which drug* — exactly and more cheaply. D4's justification
therefore narrows: BM25 now earns its place on queries that name **no** product
(where D13 never fires) and on literal clinical vocabulary MiniLM handles poorly
(`urticaria`, `tetracycline`, `Zollinger-Ellison`). That is a weaker claim than
the original, so it gets a kill criterion of its own — see D16.

*(An earlier draft claimed hybrid retrieval was on "the brief's optional-extras
list." The brief as received lists no such extras. Sentence removed — the
two-axis argument stands on its own and a false appeal to the brief is worse
than no appeal.)*

### D5 — Refusal: three layers, not one

1. **Similarity gate** — best body-only cosine < τ → refuse before the LLM call.
2. **Prompt constraint** — numbered context, answer-only-from-context, must
   emit ids of chunks used.
3. **Cite-or-refuse** — validate returned ids against the retrieved set, drop
   unknowns, refuse if none survive.

**Why not layer 1 alone:** "can I drink alcohol with Maxpro?" retrieves §2 at
reasonable similarity — §2 is about contraindications — but §2 says nothing
about alcohol. Only layer 3 catches this.

**Why layer 3 needs id validation:** the model can emit an id that was never in
context. Without validation, "cite-or-refuse" degrades into
"hallucinate-a-citation-and-pass."

### D6 — Dual embedding for the threshold *(revisit after measurement)*

**Problem found by pressure-testing D3:** contextual headers inflate similarity
for *any* query naming a brand — including out-of-scope ones. The fix for the
disambiguation problem compresses the in-scope / out-of-scope score gap that τ
depends on. D3 and D5 work against each other.

**Chose:** retrieve on the header+body vector, gate τ on a **body-only** vector.
Implementation is one Chroma collection plus an in-memory
`dict[chunk_id] -> body_vector` (24 × 384 floats). Encode the query once, take
dot products against only the retrieved ids. Normalize on encode so dot product
is cosine. ~10 lines, no new dependency.

**Kill criterion — this is deliberate.** Run the eval both ways and report the
separation gap for each. If the single-vector baseline separates just as
cleanly, delete the dual-embed and say so in DESIGN.md. "Built it, measured it,
removed it" is a better signal than either building or skipping it.

### D7 — Two scores, two jobs

RRF fusion scores are rank-based (`1/(k+rank)`), not similarities. They are
useless as a confidence gate and wrong for the citation `score` field the brief
shows as `0.82`.

- **RRF** → ordering only, never leaves the retriever.
- **Body-only cosine** → the reported `score`, and the value τ gates on.

Consistent story: the number shown to the user is the same number the system
trusted.

**Presentation bug to avoid:** ranking by RRF while reporting cosine can return
citations in non-monotonic score order (`0.71, 0.83, 0.64`), which reads as
broken. Sort citations by cosine descending.

**ChromaDB trap:** default space is L2 and `query()` returns *distance*, not
similarity. The habitual `1 - distance` produces negative "similarities" that
silently break the gate. Set `metadata={"hnsw:space": "cosine"}` explicitly.

### D8 — top_k = 8 *(was 5)*

**Revised.** `top_k=5` was set against 4 products and 24 chunks. With 5 products
and 35 chunks it is no longer viable: D14 answers an unscoped question
per-product, which needs one chunk from each of **five** products, and RRF over
dense + BM25 will spend slots on §0 overviews and stray sections. At `top_k=5`
the response D14 specifies is unreachable for the case it exists to serve.

**`top_k = 8`** — 23% of 35 chunks. One slot per product plus three for fusion
noise. Stays a single configurable constant with one value: no branching on
whether the query is scoped, nothing to explain twice, and it is the
"change top_k" live modification exercised from a value with a stated reason.

Context cost is nil — the entire corpus is 1,572 words. Wrong-product chunks in
view are removed by D13 when the query is scoped, and are the *point* when it
is not.

Full cross-document synthesis ("which of these are unsafe in pregnancy?") stays
out of scope; D14 does attribution, not comparison.

### D9 — LLM: thin provider adapter, provider configurable, Groq default

One function, `generate(prompt) -> str`, two implementations selected by
`LLM_PROVIDER`. **Not** a routing layer, fallback chain, or cost-aware
selector — that instinct is from day-job work and does not belong here.
Specifically **no runtime auto-detection**: probing for a local daemon and
silently switching provider would make the same question resolve differently on
two machines, which contradicts the `temperature=0` reproducibility argument
below.

**All provider config lives in `.env`**, read once at startup, with a shipped
`.env.example`:

| Var | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` \| `ollama` |
| `LLM_MODEL` | per-provider default | model id is config, not code — a deprecated hosted model id becomes a one-line fix, not a code change |
| `GROQ_API_KEY` | — | required only when provider is `groq` |
| `OLLAMA_HOST` | `http://localhost:11434` | |

Both paths are first-class and tested; neither is a code branch beyond the two
`generate()` implementations.

**Justification for DESIGN.md (one line):** it de-risks *their* run — an
evaluator without Ollama flips one env var instead of filing a bug.

**Default = Groq — a deliberate, stated deviation from the brief.** The brief
marks local Ollama ✅ *Recommended* and free-tier hosted ✅ *Also fine*, and it
pre-empts the obvious size objection by naming `llama3.2:3b` and `phi3` (~2 GB)
as completely acceptable. So the original "`llama3.1:8b` is a 4.7 GB download"
argument does not survive contact with the brief and has been dropped.

The argument that does survive: the brief *also* requires install-and-run in
under five minutes, and no README makes a ~2 GB pull plus a running daemon fit
that on a modest laptop. Groq needs a ~60-second free-tier key and no download.
Where two requirements conflict, the hard setup requirement outranks a stated
preference — and the preference is fully satisfied by `LLM_PROVIDER=ollama`,
which is documented in the README as the offline path with `llama3.2:3b` as the
suggested model.

**Say the deviation out loud in DESIGN.md.** A reasoned deviation reads as
judgment; a silent one reads as not having read the brief.

**Safety net:** embeddings are local MiniLM regardless, so ingestion, indexing
and retrieval work with no API key at all. A key-less evaluator can still
verify the retrieval layer — which is the part being graded. Say this in the
README.

`temperature=0` — the live round involves modifying this, and the same question
must give the same answer twice.

### D10 — Eval set: 8 rows, adversarial by construction

Initially planned as in-scope + hard negatives, with cross-drug cases as an
optional third bucket. **Corrected:** cross-drug cases are not a third bucket,
they are in-scope questions chosen adversarially. Same row count, free signal —
and without them, nothing tests whether D3 and D4 actually worked.

**Revised to 11 rows** after the fifth document arrived and the driving
correction landed.

| # | Question | Expected | Tests |
|---|---|---|---|
| 1 | How should I store Rolip? | Rolip §6 | §6 collision |
| 2 | How should Doxicap be stored? | Doxicap §6 | same trap, other doc |
| 3 | How do I take Maxpro if I can't swallow tablets? | Maxpro §3 | §3 collision |
| 4 | What is Rolac used for? | Rolac §1 | control, should score high |
| 5 | What is Fenadin used for? | Fenadin §1 | 5th doc is indexed at all |
| 6 | Is Doxicap safe for children? | Doxicap §2 | answerable |
| 7 | Is Rolac safe for children? | **Refuse** | topic in corpus, absent from Rolac |
| 8 | Can I drive after taking Fenadin? | Fenadin §2 | answerable |
| 9 | Can I drive after taking Rolac? | **Refuse** | **hardest row** — see below |
| 10 | Can I drink alcohol while taking Maxpro? | **Refuse** | pulls §2, absent everywhere |
| 11 | What if I miss a dose of Rolip? | **Refuse** | pulls §3 hard, absent everywhere |

**Row 9 is the one that justifies D13.** Fenadin §2 is *literally about driving*,
so it scores high, layer 1 passes, and a model citing it emits an id that **was**
in the retrieved set — so layer 3 passes too. All three original grounding layers
green-light a confident answer about Rolac sourced from Fenadin. Row 7 fails the
same way. Neither was caught by the original design; both are caught by D13.

Rows 7 and 9–11 calibrate τ: log top body-only cosine for each, log it for the
answerable rows, τ sits in the gap. **If there is no gap, that is itself the
finding** — and it is the argument for why layers 3 and 4 exist. Either outcome
is a good paragraph.

Note the two failure modes are now measured separately:
**absent-everywhere** (rows 10–11, which τ alone should catch) versus
**present-but-wrong-product** (rows 7 and 9, which τ *cannot* catch by
construction). Reporting them as one refusal number would hide the distinction
the whole design turns on.

### D11 — Scope: deliberately not built

| Not building | Reason |
|---|---|
| LangGraph orchestration | Pipeline is linear. A graph for a straight line invites "why?" with no good answer. |
| MCP server | Zero relevance to a `POST /ask` brief. |
| Cross-encoder reranker | top_k=8 of 35 is nearly a quarter of the corpus; reordering that fraction is noise, plus a second model download. The larger top_k strengthens this, not weakens it. |
| Query rewriting / HyDE | Extra LLM call and failure mode for one-sentence literal questions. |
| Multi-turn memory | Spec is single-shot; state means bugs during a live demo. |
| PGVector / Postgres | Brief says local vector DB. Chroma persists to disk. |
| Streaming, auth, deployment | Explicitly out of scope per brief. |
| Chunk-size ablation | Sections are 17–97 words. Nothing to ablate. |
| ~~Docker~~ | **Reversed — see D21.** The original reason was wrong. |

Every row above becomes a line in DESIGN.md §7. **Restraint that is not
documented reads as ignorance; restraint that is documented reads as judgment.**
The reranker row especially — showing the arithmetic on why it wouldn't move
the needle beats shipping it.

LangGraph gets one sentence rather than silence: it would earn its place at the
point where a failed τ gate triggers retrieval retry with a rewritten query.
Shows the tool is known and was declined.

### D12 — One pharma-specific judgment call

A single line in the UI: internal reference, grounded in the provided leaflets,
not medical advice. The leaflets carry their own disclaimer. Costs nothing,
signals awareness of who is asking and about what.

---

## 3b. Second round — decisions D13–D22

D1–D12 were made against 4 documents and before the hardware constraints were
measured. This round came out of stress-testing them against the real corpus and
the real machine.

### D13 — Layer 4: deterministic product-scope filter

**The hole:** layers 1–3 defend the *topic* axis and say nothing about the
*product* axis. Cite-or-refuse validates that a chunk was **retrieved**, never
that it belongs to the product **asked about**. Eval rows 7 and 9 pass all three
layers while answering about the wrong drug — which is the exact failure the
problem statement names as the reason this project exists.

**Chosen:** if the query literally names a known brand or active ingredient,
drop every retrieved chunk belonging to a different product, before the τ gate.
Case-insensitive, word-boundary match against the brand/ingredient list that
ingestion already produces. No NLP, no model, no new dependency.

**Placement is load-bearing: outside `retrieve()`.** In the grounding layer, not
the retriever. If it sat inside `retrieve()`, the D3/D4 ablations in Seam 2 would
all be measured downstream of a filter that alone fixes the product axis, and
headers and hybrid would both measure as contributing nothing — not because they
fail, but because the measurement was taken in the wrong place. That number is
the strongest evidence in `DESIGN.md`.

**Degrades safely:** a query naming no product is not filtered, so D14 still
works. A query naming a product not in the corpus filters to empty → refuse,
which is correct.

**Bonus:** this *is* the "add a metadata filter by drug" live-change request
already anticipated in §6 — built rather than improvised under pressure.

### D14 — Unscoped queries: per-product attribution, no synthesis

When no product is named, every disambiguation mechanism is inert at once — D13
does not fire, BM25 has no brand token, and D3's headers have nothing to match.
"How should I store this?" then resolves on §6, the 0.82 collision cluster, and
answers about a coin-flip product.

**Chosen:** a prompt rule — if retrieved chunks span more than one product,
answer each separately and attribute by name. Citations list every product cited,
sorted by cosine. Response shape unchanged, no new code path, one prompt line.

Attribution is **not** comparison: cross-document synthesis stays out of scope.
This satisfies user story 1 ("ask in plain English without knowing which
leaflet") without building a comparison engine.

### D15 — see revised D8. `top_k` 5 → 8.

### D16 — Kill criterion on hybrid retrieval

D13 does BM25's stated job deterministically, so D4's original justification no
longer holds in full. Rather than argue it, measure it — the same treatment D6
already gets.

**Two eval passes:**
- **Retrieval-only** (D13 off) — the honest D3/D4 ablation. This is the number
  `DESIGN.md` §3 promises.
- **End-to-end** (D13 on) — correctness of the shipped system.

**If BM25 adds nothing with D13 on, delete it and document the removal.**
"Built it, measured it, removed it" was already the strongest signal in this
plan; this makes it a repeatable standard rather than a one-off.

### D17 — see revised D9. Provider and model from `.env`, Groq default, stated as a deviation.

### D18 — Eval is a config-matrix script, not a manual run

The matrix is now headers on/off × hybrid on/off × single/dual vector × D13
on/off. Run by hand that is up to 8 passes over 11 questions and will not fit
the budget — and the eval slot was already the predicted squeeze point.

**Chosen:** write it once as a loop over a config dict, emitting one markdown
table. The 4th through 8th configurations then cost **zero** marginal time; the
expense is the harness (~40m), not the runs. Every number in `DESIGN.md` §3, §4
and §6 falls out of one command.

It also survives the live round: "adjust τ and show the effect on the eval set"
becomes editing one value and re-running, in front of them.

Paid for by cutting UI to 10m, as §5 always planned.

### D19 — Parser: `pypdf`, not `pdftotext`

**Blocking defect found by measurement.** `pdftotext` is a poppler binary, not
pip-installable. On this very machine it exists only inside Git Bash's MSYS2
(`/mingw64/bin`) and is **invisible to `C:\Python313\python.exe`** — the
interpreter that runs the service. On stock Windows and macOS an evaluator has
no `pdftotext` at all, and installing poppler is a manual download plus a PATH
edit. That is fatal to the five-minute setup requirement.

**Verified replacement:** `pypdf` with `extraction_mode="layout"` reproduces
`pdftotext -layout` on the Maxpro dose table — all five rows intact, condition
and dose on the same line. Pure Python, ~4 MB, no required dependencies.
**This resolves the open item on the dose table.**

`pdfplumber` also works but pads with heavy indentation and blank lines — noise
against a 40-word chunk — and costs 45 MB (PIL, pdfminer, pypdfium2).

One difference to handle: `pdftotext` downgrades en-dashes to ASCII `--`,
`pypdf` preserves the real characters. Normalize unicode dashes and quotes at
clean time.

### D20 — Embeddings: ONNX MiniLM, not `sentence-transformers`

Same model — `all-MiniLM-L6-v2` — via the ONNX build ChromaDB already ships and
already depends on. Drops `torch`, `transformers` and `huggingface-hub`.

| | sentence-transformers | **ONNX (chosen)** |
|---|---|---|
| Packages | +41 on top of chromadb's 78 | **0 extra** |
| Disk | ~2.5 GB | **~370 MB total stack** |
| RSS | ~700 MB | **~250–350 MB** |
| Linux trap | plain `pip install torch` pulls the **CUDA** build, ~2.5 GB of GPU wheels into a no-GPU service | none |

**Measured, not assumed:** `chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2`
is directly callable, returns `(n, 384)`, first call downloads 167 MB in 16.5 s,
subsequent encoding is 21 ms for 2 texts (~0.4 s for the whole corpus).

**The payoff for D6/D7:** output vectors are **already L2-normalized**
(measured norms exactly 1.0), so dot product *is* cosine with no normalization
step. The body-only gate is a `(35, 384)` numpy array and one `@`. The seam I
was worried about costs nothing.

The brief says "e.g. `all-MiniLM-L6-v2` via `sentence-transformers`" — it names
the model, and the model is unchanged.

### D21 — Docker + Compose *(reverses D11)*

**The original rejection was reasoning against a cost both options pay.** D11
skipped Docker because "a cold build installing torch runs past the 5-minute
promise" — but the pip path installed torch too. Docker relocated that download,
it did not add it. And after D20 there is no torch at all.

**Shipped:** `Dockerfile` + `compose.yaml`, with the pip/venv path kept in the
README as the fast lane for anyone who already has Python. Two documented paths,
and the five-minute claim gets **measured per path** rather than asserted.

Four decisions inside it, each load-bearing:

1. **Bake the embedding model at build time.** A `RUN` step instantiating
   `ONNXMiniLM_L6_V2()` and encoding one string writes the 167 MB cache into a
   layer. Without it every cold container pays 16.5 s **and needs network**,
   which breaks the offline claim outright.
2. **Run ingestion at build time** into `/app/chroma_db`, so `docker compose up`
   serves immediately. Maintainer story 33 still holds: mount `docs/` as a
   volume and run `docker compose run app ingest` — no rebuild, no code change.
3. **`extra_hosts: host.docker.internal:host-gateway`** plus
   `OLLAMA_HOST=http://host.docker.internal:11434`. A container cannot reach the
   host's `localhost:11434`; on Linux this single line is the difference between
   the Ollama path working and a connection-refused bug report.
4. **No CUDA pin needed** — it disappears with torch (D20).

Estimated image: `python:3.11-slim` (~130 MB) + stack (~400 MB) + model
(167 MB) ≈ **750 MB**.

### D22 — Explicit `encoding="utf-8"` on every file operation

Windows `open()` defaults to **cp1252**. This is not hypothetical — it crashed a
tooling script during this very analysis with `UnicodeDecodeError: 'charmap'
codec can't decode byte 0x81`. Combined with D19 preserving real en-dashes where
`pdftotext` downgraded them, an unqualified `open()` is a live defect on the
target platform, not a style preference.

Applies to leaflet text, eval output, `.env` loading, and every JSON artifact.

---

## 4. Open items

- [x] ~~Confirm the 5th leaflet~~ — **all 5 present**; every count re-measured.
- [x] ~~Verify the Maxpro dose table survives extraction~~ — **verified with
      `pypdf` layout mode**, all five rows intact (D19).
- [x] ~~Decide fallback if a document doesn't match the 1–6 heading scheme~~ —
      **fail loudly** (user story 34); a startup assertion on chunk count and
      section coverage per document.
- [ ] **Re-run the TF-IDF collision table over 5 documents.** The 0.82 / 0.56
      figures are the only stale numbers left, and they are quoted three times
      across the docs. Requires `scikit-learn`, which is not currently installed.
- [ ] Measure τ separation, both single-vector and dual-embed (D6 kill criterion).
- [ ] Measure retrieval accuracy with headers off/on and hybrid off/on, with D13
      **off** — DESIGN.md §3 needs a real impact number, not an assertion (D16).
- [ ] Measure whether BM25 still contributes with D13 **on** — if not, delete it
      and document the removal (D16 kill criterion).
- [ ] Measure actual image size and cold-start time for both run paths, so the
      five-minute claim in the README is measured rather than asserted (D21).

## 5. Time budget (5h)

Re-planned after D13–D22. The added scope is paid for by D18 (measurement
becomes a script, so extra configurations are free) and by cutting UI.

| | was | **now** |
|---|---|---|
| Ingest + chunk (pypdf, §0, cleaning) | 45m | 45m |
| Retrieval + hybrid | 60m | 60m |
| API + grounding layers 1–3 | 60m | 60m |
| **D13 product filter + D14 attribution** | — | **30m** |
| UI | 20m | **10m** |
| Eval **harness** + τ calibration | 45m | **50m** |
| **Dockerfile + compose** | — | **25m** |
| README + DESIGN.md | 40m | 40m |
| | **4h30** | **5h20** |

The eval harness is the one slot that must not be cut: it is now the *only*
source of every measured number in `DESIGN.md`, and it is what makes the live
round demonstrable rather than described. If something has to go, cut Docker —
it is the newest decision and the pip path already works.

## 6. Live-round prep

Likely probes and the honest answers:

- **"Why no reranker?"** → top_k=8 of 35 chunks. Reordering a quarter of the
  corpus is noise, and it costs a second model download for no measured gain.
- **"Why sections instead of fixed chunks?"** → measured 17–97 words; nothing
  to split, and the citation contract asks for an exact section.
- **"How do you know grounding works?"** → two hard negatives on topics verified
  absent from all five documents, *plus* two asymmetry pairs where the topic is
  present in the corpus but absent from the leaflet being asked about — children
  (answer for 3, refuse for 2) and driving (answer for Fenadin, refuse for 4).
  The second kind is harder, and it is what layer 4 exists for.
- **"Why a product filter when you already have hybrid retrieval?"** → hybrid
  fixes *retrieval* on the product axis; nothing fixed *generation* on it. A
  correctly retrieved Fenadin chunk answering a Rolac question passes all three
  original layers with an honest citation and a wrong answer.
- **"Why not sentence-transformers?"** → same model, ONNX build, already a
  chromadb dependency. 370 MB instead of 2.5 GB, and the vectors come out
  normalized so cosine is a dot product.
- **"Why Docker after saying you wouldn't?"** → the original reason was wrong.
  It cited a torch download the pip path paid identically. After dropping torch
  the image is ~750 MB, and Docker is what makes "any machine" true.
- **"What's the weakest part?"** → cross-document synthesis. Comparative
  questions return a partial answer from top_k. Known, documented, first thing
  I'd fix.
- **"Why two embeddings?"** → contextual headers fix disambiguation but inflate
  out-of-scope similarity, compressing the gap the threshold depends on.
  Measured both ways; kept the version with the wider separation.

**Likely live change requests** — be ready to do these in under 5 minutes:
add a metadata filter by drug; change top_k; add a new section type; swap the
LLM provider; return the retrieved chunk text alongside citations; adjust τ and
show the effect on the eval set.
