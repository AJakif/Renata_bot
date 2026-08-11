# PRD — Grounded Leaflet Assistant

**Triage label:** `ready-for-agent`
**Status:** ready to build
**Note:** no issue tracker is configured in this environment — publish manually.

---

## Problem Statement

Renata staff need to answer questions about the company's product inserts and
medicine leaflets. Today that means opening the right PDF, finding the right
section, and reading it — slow, and easy to get wrong when several products
have near-identical wording.

A general-purpose chatbot makes this worse rather than better. Medicine leaflets
are exactly the domain where a plausible-sounding wrong answer is dangerous: an
assistant that answers "what if I miss a dose?" from its own training data, when
the leaflet has no such section, is actively harmful. Staff cannot tell the
difference between a grounded answer and a fluent invention.

Compounding this, the leaflets are structurally near-identical. Storage
instructions across products are effectively boilerplate (measured 0.82 mean
cosine similarity between different products' storage sections), and 19 of 24
sections never name their own product in the body text. A naive retrieval system
will confidently answer a question about one medicine using another medicine's
leaflet.

Staff therefore need three things at once: the right answer, proof of where it
came from, and an explicit refusal when the documents don't cover the question.

## Solution

A small internal service with a single question box. Staff type a question in
plain language and get back either:

- an answer drawn strictly from the leaflets, with citations naming the source
  document, the specific section, and a relevance score; or
- an explicit statement that the information is not in the provided documents.

Never anything in between. The system does not use outside knowledge, does not
guess, and does not soften a refusal into a hedge.

Citations are precise enough to verify: not "see the Maxpro leaflet" but
"Maxpro — How to take", so staff can confirm the answer in seconds.

## User Stories

1. As a staff member, I want to ask a question in plain English, so that I don't
   have to know which leaflet or section holds the answer.
2. As a staff member, I want to ask "how should I store Rolip?", so that I get
   Rolip's storage section and not another product's near-identical one.
3. As a staff member, I want to name a product in my question, so that the
   answer is scoped to that product.
4. As a staff member, I want to ask about a product's active ingredient, so that
   I can confirm what a brand name corresponds to.
5. As a staff member, I want to ask about pack sizes and unit price, so that I
   can answer commercial questions from the same tool.
6. As a staff member, I want to ask who manufactures a product, so that I don't
   need a separate source for company details.
7. As a staff member, I want to ask what a medicine is used for, so that I can
   confirm its indications.
8. As a staff member, I want to ask about contraindications, so that I can check
   who should not take a product.
9. As a staff member, I want to ask about dosing for a specific condition, so
   that I get the right row of the dose table rather than a general answer.
10. As a staff member, I want to ask about administration practicalities (for
    example, taking a medicine when swallowing is difficult), so that I can
    answer patient-facing questions accurately.
11. As a staff member, I want to ask about side effects, so that I can report
    them accurately.
12. As a staff member, I want to ask about use in pregnancy or breast-feeding,
    so that I can give a correctly scoped answer on a sensitive topic.
13. As a staff member, I want to ask about storage conditions, so that I can
    advise on handling.
14. As a staff member, I want every answer to carry citations, so that I can
    verify it before repeating it.
15. As a staff member, I want each citation to name the source document, so that
    I know which leaflet to open.
16. As a staff member, I want each citation to name the section within that
    document, so that I can find the passage without reading the whole leaflet.
17. As a staff member, I want each citation to carry a relevance score, so that
    I can judge how strongly the answer is supported.
18. As a staff member, I want citations ordered strongest-first, so that the most
    relevant source is the one I check.
19. As a staff member, I want to be told plainly when the documents don't cover
    my question, so that I don't act on an invented answer.
20. As a staff member, I want a refusal rather than a partial guess when only
    adjacent information exists, so that near-misses don't read as answers.
21. As a staff member, I want the system to refuse questions about topics the
    leaflets omit entirely (alcohol interactions, missed doses, overdose,
    driving), so that I know to escalate rather than assume.
22. As a staff member, I want a question that is answerable for one product and
    not another to be answered for the first and refused for the second, so that
    coverage gaps are visible rather than papered over.
23. As a staff member, I want the assistant never to supplement the leaflets with
    general medical knowledge, so that everything I read is traceable.
24. As a staff member, I want a visible note that this is an internal reference
    grounded in the provided leaflets and not medical advice, so that the tool's
    standing is unambiguous.
25. As a staff member, I want the same question to give the same answer, so that
    I can trust and repeat what I find.
26. As a staff member, I want a single web page with a question box and an answer
    area, so that there is nothing to learn.
27. As a staff member, I want the citations rendered beneath the answer, so that
    the answer and its evidence are read together.
28. As an evaluator, I want to install and run the service in under five minutes,
    so that I can assess it without a setup investment.
29. As an evaluator, I want to verify ingestion and retrieval without any API
    key, so that I can inspect the graded core offline.
30. As an evaluator, I want to switch the language model provider with one
    environment variable, so that a missing local model doesn't block me.
31. As an evaluator, I want a documented evaluation set with results, so that I
    can see measured behaviour rather than claims.
32. As an evaluator, I want the design document's reasoning to match the code, so
    that I can trace decisions to implementation.
33. As a maintainer, I want a new leaflet to be ingested by dropping it in the
    documents folder and re-running ingestion, so that adding products needs no
    code change.
34. As a maintainer, I want a leaflet that doesn't match the expected heading
    scheme to fail loudly at ingestion, so that documents aren't silently indexed
    into unusable chunks.
35. As a maintainer, I want the retrieval threshold to be configurable, so that
    the honesty/helpfulness balance can be tuned without a code change.
36. As a maintainer, I want retrieval measurable independently of generation, so
    that I can tell which layer caused a bad answer.

## Implementation Decisions

**Modules**

- **Ingestion** — reads leaflet PDFs, produces chunks with metadata. Layout-
  preserving text extraction to keep dose tables readable. Page-break form-feed
  characters are stripped *before* heading detection; without this, headings that
  fall immediately after a page break are silently missed (observed in 2 of 4
  documents). The identical trailing legal disclaimer is stripped — at roughly 20
  words against a 39-word median chunk it is over a third noise.
- **Chunking** — one chunk per leaflet section, no splitting and no overlap.
  Sections measured at 21–96 words, so there is nothing to split, and the
  citation contract requires an exact section name rather than an inferred one. A
  synthetic **Product overview** chunk captures the header block (brand, active
  ingredient, form, manufacturer, pack and price) that sits above the first
  numbered section and would otherwise be unretrievable.
- **Index** — local vector store plus a keyword index over the same text. Vector
  space set explicitly to cosine; the default is Euclidean and returns distance
  rather than similarity, which silently inverts threshold logic.
- **Retrieval** — hybrid. Reciprocal rank fusion over dense and keyword results,
  returning five chunks.
- **Grounding** — three independent layers (below).
- **LLM provider adapter** — a single `generate(prompt) -> str` behind an
  environment variable. Deliberately not a routing layer, fallback chain, or
  cost-aware selector.
- **API** — one `POST /ask` endpoint.
- **UI** — one static page.

**Contextual chunk headers**

Chunk text is prefixed with brand, active ingredient and section name *before
embedding*. This is not an optimisation: 19 of 24 chunk bodies never name their
own product, so without the prefix the chunk text carries no signal on the
product axis and retrieval is close to a coin flip across four candidates.

**Why hybrid retrieval**

Queries vary along two orthogonal axes — *which product* and *which section*.
Keyword search resolves the product (brand names are rare, high-IDF literal
tokens). Dense retrieval resolves the section (semantic, paraphrase-tolerant).
Neither handles both.

**Dual embedding, and its kill criterion**

Contextual headers create a conflict with the threshold: they inflate similarity
for any query naming a product, including out-of-scope ones, compressing exactly
the score gap the threshold depends on. Resolution: retrieve on the header+body
vector, gate the threshold on a **body-only** vector held in memory alongside the
index. The query is encoded once and reused for both.

This must be validated, not assumed. Both configurations are measured on the
evaluation set and the separation gap reported. **If the single-vector baseline
separates as cleanly, the dual embedding is removed** and the removal documented.

**Two scores, two jobs**

Fusion scores are rank-derived and are neither similarities nor confidences. They
order results and never leave the retriever. The **body-only cosine** is the
value reported as the citation score and the value the threshold gates on — so
the number shown to the user is the number the system trusted. Citations are
sorted by that score descending, since ranking by fusion while reporting cosine
would otherwise emit non-monotonic scores that read as a defect.

**Grounding — three layers**

1. **Similarity gate.** Best body-only cosine below threshold → refuse without
   calling the model.
2. **Prompt constraint.** Numbered context, answer only from context, must return
   the ids of chunks used. Temperature zero.
3. **Cite-or-refuse.** Returned ids are validated against the retrieved set,
   unrecognised ids are discarded, and if none survive the answer is dropped in
   favour of a refusal.

Layer 1 alone is insufficient: a question about alcohol interaction retrieves the
contraindications section at reasonable similarity, but that section says nothing
about alcohol. Layer 3 requires id validation, or a hallucinated citation passes
the check it was meant to fail.

**API contract**

`POST /ask` takes a question and returns an answer plus a citation list, each
citation carrying source document, section name, and score. Refusals return the
same shape with a fixed refusal message and an empty citation list — a single
response shape, so the client needs no branching.

**Model choices**

Local sentence-embedding model on CPU, no key required. Hosted free-tier
generation by default, local model via environment variable as the offline path.
The hosted default exists because the setup promise is five minutes and pulling a
local model is a multi-gigabyte download. Embeddings and retrieval are fully
local either way, so ingestion and retrieval can be verified with no API key at
all.

## Testing Decisions

**What makes a good test here:** it asserts observable behaviour — the response a
caller receives, or the chunks a query returns — and never the internal shape of
fusion, prompt construction, or storage. Tests must survive the implementation
being modified live, which is the specific pressure this build is under.

**Seam 1 — `POST /ask` with the LLM provider injected.** The highest available
seam. A stub `generate()` returns canned output, making the whole grounding chain
deterministic. Covers: response shape for answers and refusals; refusal when best
similarity falls below threshold; refusal when the model returns no citation ids;
discarding of ids absent from the retrieved set; citations ordered by score
descending; identical output for repeated identical questions.

**Seam 2 — `retrieve(query)`.** Separate because it must run with real embeddings
and no model. Covers: the evaluation set's expected source and section per
question; threshold calibration data (top score for in-scope versus out-of-scope
questions); and the headers-on/off and hybrid-on/off comparisons. Kept apart from
Seam 1 precisely so retrieval quality is never entangled with generation
behaviour — the design argument depends on measuring them independently.

**Ingestion is deliberately not a seam.** Parsing and chunking are asserted
through Seam 2: if the form-feed heading defect regresses, the affected sections
disappear from retrieval results and Seam 2 fails loudly. One startup assertion
on chunk count and section coverage per document covers the remainder.

**Evaluation set.** Eight questions, adversarial by construction. In-scope rows
target the measured collision hotspots (storage at 0.82, administration at 0.56)
rather than easy cases. Hard negatives use topics verified absent from every
leaflet — alcohol, missed dose, overdose, driving. One question is answerable for
two products and absent for a third, testing grounding and disambiguation
together. Reported: correct document retrieved, correct section cited, correct
refusals, false refusals.

**No prior art** — greenfield.

## Out of Scope

- Cross-document synthesis. Comparative questions spanning all products return a
  partial answer from the retrieved set; this is a known limitation, not a defect.
- Multi-turn conversation and memory. Single-shot only.
- Reranking. Retrieving five of twenty-four chunks already covers a fifth of the
  corpus; reordering that fraction adds a model download for no expected gain.
- Query rewriting or hypothetical-document expansion.
- Graph or agent orchestration. The pipeline is linear. The point at which it
  would earn its place is retrieval retry with a rewritten query after a failed
  threshold gate — the first thing to add with more time.
- Containerisation. A cold build installing the embedding stack exceeds the
  five-minute setup promise, so it would undercut a core requirement to satisfy
  an optional one.
- Authentication, user accounts, deployment infrastructure, hosted vector
  database, response streaming.
- Any UI work beyond a single unstyled page.

## Further Notes

**Confirm document count.** The brief specifies five leaflets; four were
available at design time. All measurements above are over four and should be
re-run once the fifth is present.

**Restraint must be documented.** Every out-of-scope item belongs in the design
document with its reasoning. Undocumented restraint reads as ignorance;
documented restraint reads as judgment — particularly the reranker, where the
arithmetic on corpus size is a stronger signal than shipping it would be.

**Anticipated live modifications**, which the seams above are chosen to survive:
filtering by product, changing the retrieval count, adding a section type,
swapping the model provider, returning retrieved chunk text alongside citations,
and adjusting the threshold to observe its effect on the evaluation set.

**Time allocation.** Roughly: ingestion and chunking 45m, retrieval 60m, API and
grounding 60m, UI 20m, evaluation and threshold calibration 45m, documentation
40m. The evaluation slot carries the strongest material and is the one most
likely to be squeezed — protect it ahead of UI time.
