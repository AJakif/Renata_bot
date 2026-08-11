# PRD — Grounded Leaflet Assistant

**Triage label:** `ready-for-agent`
**Status:** ready to build
**Issues:** broken down into 14 vertical slices at
`github.com/AJakif/Renata_bot/issues` — #1–#12 `ready-for-agent`, #13–#14
`ready-for-human`.

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
cosine similarity between different products' storage sections — pending re-run
over the fifth document), and **22 of 30** sections never name their own product
in the body text. A naive retrieval system will confidently answer a question
about one medicine using another medicine's leaflet.

A sharper version of the same risk: some topics appear in exactly one leaflet.
Driving is covered only by Fenadin; children only by Doxicap, Rolip and Fenadin.
Asked about a product whose leaflet omits the topic, retrieval returns a
genuinely relevant passage from a *different* product — so the failure looks like
a well-cited answer rather than an obvious error.

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
37. As a staff member, I want a question naming one product never answered from
    another product's leaflet, even when that other leaflet genuinely covers the
    topic, so that a well-cited answer is never a wrong-product answer.
38. As a staff member, I want a question that names no product to be answered for
    each relevant product by name, so that I am not silently given one product's
    answer to a question I asked generally.
39. As an evaluator, I want to run the whole service on a machine with no Python
    toolchain, so that my environment is not a prerequisite for assessing it.
40. As an evaluator, I want the service to work with no network access after
    setup, so that ingestion and retrieval are verifiable offline.
41. As a maintainer, I want every configuration of the retrieval design
    measurable from a single command, so that a design claim can be re-verified
    rather than trusted.

## Implementation Decisions

**Modules**

- **Ingestion** — reads leaflet PDFs, produces chunks with metadata. Extraction
  is **pure Python** (`pypdf`, layout mode), never a system binary: the usual
  choice, `pdftotext`, is a poppler executable that is absent from stock Windows
  and macOS and cannot be pip-installed, which alone would break the five-minute
  setup requirement. Layout mode is required to keep dose tables readable, and
  has been verified against the worst case. Page-break form-feed characters are
  stripped *before* heading detection; without this, headings that fall
  immediately after a page break are silently missed (observed in 2 of 5
  documents). The identical trailing legal disclaimer is stripped — at roughly 20
  words against a 40-word median chunk it is over a third noise. Unicode dashes
  and quotes are normalized, and **all file I/O is explicitly UTF-8** (the
  platform default on Windows is cp1252 and corrupts the text).
- **Chunking** — one chunk per leaflet section, no splitting and no overlap.
  Sections measured at 17–97 words, so there is nothing to split, and the
  citation contract requires an exact section name rather than an inferred one. A
  synthetic **Product overview** chunk captures the header block (brand, active
  ingredient, form, manufacturer, pack and price) that sits above the first
  numbered section and would otherwise be unretrievable. 35 chunks total.
- **Index** — local vector store plus a keyword index over the same text. Vector
  space set explicitly to cosine; the default is Euclidean and returns distance
  rather than similarity, which silently inverts threshold logic.
- **Retrieval** — hybrid. Reciprocal rank fusion over dense and keyword results,
  returning eight chunks — one slot per product plus headroom for fusion noise,
  since an unscoped question must be answerable for all five products at once.
- **Grounding** — four independent layers (below).
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

**Grounding — four layers**

1. **Product scope.** If the question names a known brand or active ingredient,
   retrieved chunks belonging to any other product are discarded before scoring.
   Literal, case-insensitive, word-boundary matching against the list ingestion
   already produces. A question naming no product is left unfiltered.
2. **Similarity gate.** Best body-only cosine below threshold → refuse without
   calling the model.
3. **Prompt constraint.** Numbered context, answer only from context, must return
   the ids of chunks used. Temperature zero.
4. **Cite-or-refuse.** Returned ids are validated against the retrieved set,
   unrecognised ids are discarded, and if none survive the answer is dropped in
   favour of a refusal.

Layer 2 alone is insufficient: a question about alcohol interaction retrieves the
contraindications section at reasonable similarity, but that section says nothing
about alcohol. Layer 4 requires id validation, or a hallucinated citation passes
the check it was meant to fail.

**Layer 1 exists because layers 2–4 defend the topic axis and not the product
axis.** Asked "can I drive after taking Rolac?", retrieval surfaces Fenadin's
driving passage at high similarity, the threshold passes, the model answers from
it, and the id it cites genuinely was in the retrieved set — so cite-or-refuse
passes too. The output is an honest citation above a wrong answer. Validating
that a chunk was *retrieved* is not the same as validating that it concerns the
product asked about. This layer must sit outside the retrieval function, so that
retrieval quality can still be measured independently of it.

**Unscoped questions.** When no product is named, every disambiguation mechanism
is inert at once. Rather than answer from whichever product happens to rank
first, the response answers per product and attributes each by name, citing all
of them. This is attribution, not synthesis — no comparison across documents.

**API contract**

`POST /ask` takes a question and returns an answer plus a citation list, each
citation carrying source document, section name, and score. Refusals return the
same shape with a fixed refusal message and an empty citation list — a single
response shape, so the client needs no branching.

**Model choices**

Local sentence-embedding model on CPU, no key required — `all-MiniLM-L6-v2` in
its ONNX form, which the vector store already depends on. This avoids pulling
`torch` and `transformers` (~41 additional packages, ~2.5 GB installed, and on
Linux a default `pip install torch` fetches the CUDA build — GPU libraries into
a service the brief specifies must run without a GPU). The ONNX path is the same
model at roughly a sixth of the footprint, and its output vectors arrive
L2-normalized, so cosine similarity is a plain dot product.

The whole stack must fit a modest laptop: ~370 MB installed, well under 400 MB
resident with the hosted provider, comfortable at 4 GB RAM.

Generation provider **and** model id are read from `.env` at startup, never
hardcoded — a shipped `.env.example` documents both paths. **Default is a local
model**, matching the brief's recommendation; the hosted free-tier provider is
the escape hatch for a machine that cannot spare the memory. Neither is a
special case: the adapter is a single `generate(prompt) -> str` with two
implementations, chosen by configuration, with **no runtime auto-detection** —
a provider that varies by machine would contradict the determinism requirement.

**Sizing to an 8 GB machine with no GPU.** The operating system takes 2–3 GB and
the service ~350 MB, leaving roughly 2.5 GB for the model. A 7 B model measured
at 4.9 GB resident and is therefore excluded; a 3 B model at 4-bit quantisation
fits at ~2.1 GB. Three settings that are not defaults do the real work: the
context window is pinned to 2048 (the prompt is around 700 tokens, so a larger
window only wastes key-value cache), parallel request slots are pinned to one
(the cache is sized *per slot* and can auto-select four, silently quadrupling
the one quantity being budgeted), and only one model may be resident at a time.

**Model selection is a measurement, not a preference.** Candidates are scored on
the evaluation set for schema adherence, correct refusal when the retrieved
passage belongs to a different product, and whether refusals arrive without
citations attached. A full pass costs about a minute, so there is no reason to
decide this by reputation. The result is recorded with the losing candidate's
numbers alongside.

**Generation is constrained to a JSON schema.** Requiring the model to name the
passages it used is a prompt instruction that a frontier model mostly honours
and a 3 B model does not honour at all — measured on identical prompts, free-text
responses failed to parse in every case, and one cited a passage and refused in
the same breath. Constrained decoding is what makes the cite-or-refuse layer
implementable at this model size. The schema carries an explicit *answered*
flag: refusal is a structured field, never inferred from the answer text, because
a model did return a refusal with three citations attached.

Embeddings and retrieval are fully local regardless, so ingestion, retrieval and
the entire evaluation harness run with no model installed at all — the graded
core can be assessed before anything is downloaded.

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

**Evaluation set.** Eleven questions, adversarial by construction. In-scope rows
target the measured collision hotspots (storage, administration) rather than easy
cases. Hard negatives use topics verified absent from all five leaflets —
alcohol, missed dose, overdose. Two asymmetry pairs cover topics that exist in
the corpus but not in the leaflet being asked about: children (answerable for
three products, absent for two) and driving (answerable for one, absent for
four). Reported: correct document retrieved, correct section cited, false
refusals, and refusals split into two counts — topic absent everywhere versus
topic present in another product. The split is deliberate: the first is what the
similarity threshold can catch, the second is what it cannot catch by
construction.

**Evaluation is a script, not a manual procedure.** The configuration matrix is
contextual headers on/off × hybrid on/off × single/dual vector × product filter
on/off. Run by hand that is up to eight passes over eleven questions, which will
not fit the time budget. Written once as a loop over a configuration dictionary
emitting a single table, the additional configurations cost nothing, and every
measured claim in the design document comes from one command. It also makes
threshold tuning demonstrable live rather than described.

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
- Authentication, user accounts, deployment infrastructure, hosted vector
  database, response streaming.
- Any UI work beyond a single unstyled page.

**Containerisation** — now in scope. A `Dockerfile` and Compose file ship
alongside the pip path, so the service runs on a machine with no Python
toolchain. Both the embedding model and the built index are baked at image build
time, so a container starts immediately and needs no network at runtime; without
this the model is fetched on first use, which would break the offline claim.
Compose maps the host gateway explicitly, because a container cannot reach a
local model daemon on `localhost` — on Linux this is the difference between the
offline path working and a connection error. Adding a leaflet still needs no
rebuild: the documents folder is mounted and ingestion is re-run.

*(An earlier revision excluded containerisation on the grounds that installing
the embedding stack exceeded the setup promise. That reasoning was wrong — the
pip path installed the same stack, so containerisation relocated the cost rather
than adding it. With the ONNX embedding path the question is moot: the image is
under a gigabyte.)*

## Further Notes

**Document count resolved.** All five leaflets are present. Counts and section
statistics have been re-measured over all five; the cross-document similarity
table is the one remaining figure still quoted from the four-document
measurement and must be re-run before it is published.

**Two decisions carry explicit kill criteria** — the dual embedding and hybrid
retrieval. Both are measured against their simpler baseline, and either is
deleted, with the removal documented, if the baseline performs as well.
Building, measuring, and removing is a stronger signal than either building or
skipping.

**Setup claims are measured, not asserted.** Install-and-run time, image size and
cold-start time are recorded for both the pip and container paths.

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
