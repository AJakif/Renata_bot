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

Run against the 4 leaflets available (`doxicap`, `maxpro`, `rolac`, `rolip`).
**Note: the brief says 5 — confirm the full `docs/` folder was received.**

**Documents are clean.** All are pandoc → WeasyPrint output. Single column,
embedded fonts, real text layer. No OCR risk, no multi-column reflow problem.

**Structure is rigid.** All six sections present in all four documents:

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

**The corpus is tiny.** 1,477 words total, 24 section-chunks. Section lengths:
min 21w, median 39w, max 96w. Nothing needs splitting.

**Cross-document collision is the real risk.** Same-section, different-drug
TF-IDF cosine:

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

**The decisive finding: 19 of 24 chunks never name their own drug.** The brand
appears in the heading and then never again in the body. So for "how should I
store Rolip?", the chunk *text* contains no signal at all about which product
it belongs to. This is an information-availability problem, not a tuning
problem — no amount of threshold work fixes it.

**Two cleanup items.** Every document ends with an identical ~20-word
disclaimer (35% noise on a 39-word chunk) → strip at parse. The header block
above section 1 (brand, active ingredient, form, manufacturer, pack & price,
~54w) belongs to no numbered section → currently unretrievable, so it becomes a
synthetic §0.

**Absent topics, verified across all four documents:** alcohol, missed dose,
overdose, driving. These are standard PIL sections these leaflets lack, making
them ideal hard negatives — an LLM's parametric knowledge will answer them
confidently, which is exactly what grounding is being tested on.

**Useful asymmetry:** "children" appears in Doxicap §2 (tetracycline, under-8s)
and Rolip §1 (ages 10–17) but nowhere in Rolac. So "is this safe for children?"
must be *answered* for two drugs and *refused* for a third — one question
testing grounding and disambiguation simultaneously.

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

It is also on the brief's optional-extras list, so it reads as motivated rather
than as padding.

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

### D8 — top_k = 5

24 chunks total, so top_k=5 costs nothing and covers comparative questions
better than 3. Full cross-document synthesis ("which of these are unsafe in
pregnancy?" needs all four §5 chunks) is declared out of scope.

### D9 — LLM: thin provider adapter, Groq default

One function, `generate(prompt) -> str`, two implementations behind
`LLM_PROVIDER`. **Not** a routing layer, fallback chain, or cost-aware
selector — that instinct is from day-job work and does not belong here.

**Justification for DESIGN.md (one line):** it de-risks *their* run — an
evaluator without Ollama flips an env var instead of filing a bug.

**Default = Groq**, because the README promises setup in under 5 minutes and
`ollama pull llama3.1:8b` is a 4.7 GB download. Ollama documented as the
offline path. Free-tier key signup is ~60 seconds.

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

| # | Question | Expected |
|---|---|---|
| 1 | How should I store Rolip? | Rolip §6 — §6 collision, 0.82 |
| 2 | How should Doxicap be stored? | Doxicap §6 — same trap, other doc |
| 3 | How do I take Maxpro if I can't swallow tablets? | Maxpro §3 — §3 collision, 0.56 |
| 4 | What is Rolac used for? | Rolac §1 — control, should score high |
| 5 | Is Doxicap safe for children? | Doxicap §2 — answerable |
| 6 | Is Rolac safe for children? | **Refuse** — absent from Rolac |
| 7 | Can I drink alcohol while taking Maxpro? | **Refuse** — pulls §2, absent |
| 8 | What if I miss a dose of Rolip? | **Refuse** — pulls §3 hard, absent |

Rows 6–8 calibrate τ: log top cosine for each, log it for rows 1–5, τ sits in
the gap. **If there is no gap, that is itself the finding** — and it is the
argument for why layer 3 exists. Either outcome is a good paragraph.

### D11 — Scope: deliberately not built

| Not building | Reason |
|---|---|
| LangGraph orchestration | Pipeline is linear. A graph for a straight line invites "why?" with no good answer. |
| MCP server | Zero relevance to a `POST /ask` brief. |
| Cross-encoder reranker | top_k=5 of 24 is a fifth of the corpus; reordering that fraction is noise, plus a second model download. |
| Query rewriting / HyDE | Extra LLM call and failure mode for one-sentence literal questions. |
| Multi-turn memory | Spec is single-shot; state means bugs during a live demo. |
| PGVector / Postgres | Brief says local vector DB. Chroma persists to disk. |
| Streaming, auth, deployment | Explicitly out of scope per brief. |
| Chunk-size ablation | Sections are 21–96 words. Nothing to ablate. |
| **Docker** | Counterintuitive skip: a cold build installing torch (~2 GB) runs well past the 5-minute README promise. Adding an optional extra that undercuts a core requirement is a bad trade. |

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

## 4. Open items

- [ ] **Confirm the 5th leaflet** — only 4 received.
- [ ] Measure τ separation, both single-vector and dual-embed (D6 kill criterion).
- [ ] Measure retrieval accuracy with headers off/on and hybrid off/on — DESIGN.md
      §3 needs a real impact number, not an assertion.
- [ ] Decide fallback behaviour if a future document doesn't match the 1–6
      heading scheme (fail loudly vs. fall back to recursive split).
- [ ] Verify `pdftotext -layout` keeps the Maxpro dose table readable once chunked.

## 5. Time budget (5h)

| | |
|---|---|
| Ingest + chunk | 45m |
| Retrieval + hybrid | 60m |
| API + grounding layers | 60m |
| UI | 20m |
| Eval run + τ calibration | 45m |
| README + DESIGN.md | 40m |

The eval slot will get squeezed and it carries the best DESIGN.md material.
Protect it — if something has to go, cut UI time to 10 minutes.

## 6. Live-round prep

Likely probes and the honest answers:

- **"Why no reranker?"** → top_k=5 of 24 chunks. Reordering a fifth of the
  corpus is noise, and it costs a second model download for no measured gain.
- **"Why sections instead of fixed chunks?"** → measured 21–96 words; nothing
  to split, and the citation contract asks for an exact section.
- **"How do you know grounding works?"** → three hard negatives on topics
  verified absent from all documents, plus the children question that must be
  answered for two drugs and refused for a third.
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
