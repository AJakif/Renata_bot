#!/usr/bin/env python3
"""Evaluation harness for the leaflet RAG retrieval pipeline.

Runs an 11-question adversarial eval set (D10, project_docs/PLAN_AND_DECISIONS.md)
across 8 named retrieval configurations and emits a Markdown results table.

Configuration matrix rationale (D16/D18 -- project_docs/PLAN_AND_DECISIONS.md):
  The full flag space is 2^4 = 16 combinations.  Per D18, 8 configs that isolate
  each flag's effect against the shipped default are more informative than the full
  product and cost the same wall-clock time (the expense is the harness, not the runs).

  Configs chosen and their purpose:
  1. default         (H/BM/D/F) -- all on; shipped system; D16 end-to-end baseline
  2. no_filter       (H/BM/D/-) -- filter off; D16 retrieval-only; honest D3/D4 ablation
                                   without D13 masking product-axis errors
  3. no_headers      (-/BM/D/F) -- headers off; ablate D3 (contextual headers)
  4. no_hybrid       (H/--/D/F) -- hybrid off; ablate D4 (BM25 fusion)
  5. no_dual         (H/BM/-/F) -- dual embedding off; ablate D6 (body-only rescoring)
  6. no_hdrs_no_flt  (-/BM/D/-) -- headers off + filter off; D16 retrieval-only without D3
  7. no_hyb_no_flt   (H/--/D/-) -- hybrid off + filter off; D16 retrieval-only without D4
  8. bare_baseline   (-/--/-/-) -- everything off; full ablation reference point

  Key comparison pairs (D16):
  - configs 1 & 2: end-to-end vs retrieval-only (filter on vs off)
  - configs 2 & 6: headers contribution in retrieval-only mode
  - configs 2 & 7: hybrid contribution in retrieval-only mode

Changing the threshold and re-running:
  Edit SIMILARITY_THRESHOLD below, or set the SIMILARITY_THRESHOLD environment
  variable (same name as app/main.py for consistency).  Then: python scripts/eval.py

Usage:
  python scripts/eval.py
  SIMILARITY_THRESHOLD=0.40 python scripts/eval.py
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import chromadb
import numpy as np
import numpy.typing as npt

# Allow ``import app.*`` when executed as a top-level script outside the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.parser import Chunk, ProductInfo, extract_product_info, parse_pdf  # noqa: E402
from app.retrieve import (  # noqa: E402
    _get_embedding_function,  # shared MiniLM singleton; LRU-cached, loaded once per process
    build_bm25_index,
    build_body_vectors,
    build_collection,
    retrieve,
)
from app.scope import filter_by_product_scope  # noqa: E402

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Edit this value or set SIMILARITY_THRESHOLD env var; matches app/main.py's name.
SIMILARITY_THRESHOLD: float = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
DOCS_DIR: Path = _REPO_ROOT / "docs"
TOP_K: int = 8
OUTPUT_PATH: Path = _REPO_ROOT / "eval_results.md"

# ---------------------------------------------------------------------------
# Eval set -- 11 questions, adversarial by construction (D10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalRow:
    """One question in the adversarial eval set."""

    idx: int
    question: str
    # "answerable"            -- should retrieve and answer
    # "absent_everywhere"     -- topic not in any leaflet; similarity gate should refuse
    # "present_wrong_product" -- topic in corpus but not the asked-about product;
    #                            gate alone cannot refuse; requires product-scope filter
    kind: str
    expected_source_prefix: str | None  # first chars of PDF filename, e.g. "rolip"
    expected_section_kw: str | None  # case-insensitive substring of section name


EVAL_ROWS: list[EvalRow] = [
    # Answerable rows -- must retrieve correct doc + section
    EvalRow(1, "How should I store Rolip?", "answerable", "rolip", "store"),
    EvalRow(2, "How should Doxicap be stored?", "answerable", "doxicap", "store"),
    EvalRow(
        3,
        "How do I take Maxpro if I can't swallow tablets?",
        "answerable",
        "maxpro",
        "how to take",
    ),
    EvalRow(4, "What is Rolac used for?", "answerable", "rolac", "used for"),
    EvalRow(5, "What is Fenadin used for?", "answerable", "fenadin", "used for"),
    EvalRow(6, "Is Doxicap safe for children?", "answerable", "doxicap", "before"),
    # Present-but-wrong-product rows -- gate alone cannot refuse; requires product-scope filter
    EvalRow(7, "Is Rolac safe for children?", "present_wrong_product", None, None),
    EvalRow(8, "Can I drive after taking Fenadin?", "answerable", "fenadin", "before"),
    EvalRow(9, "Can I drive after taking Rolac?", "present_wrong_product", None, None),
    # Absent-everywhere rows -- topic not in any leaflet; similarity gate should refuse
    EvalRow(10, "Can I drink alcohol while taking Maxpro?", "absent_everywhere", None, None),
    EvalRow(11, "What if I miss a dose of Rolip?", "absent_everywhere", None, None),
]

# ---------------------------------------------------------------------------
# Configuration matrix (D16/D18)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """One retrieval pipeline configuration."""

    name: str
    use_contextual_headers: bool
    use_hybrid: bool
    use_dual: bool
    use_filter: bool


CONFIGS: list[Config] = [
    Config("default", True, True, True, True),  # 1 -- D16 end-to-end
    Config("no_filter", True, True, True, False),  # 2 -- D16 retrieval-only
    Config("no_headers", False, True, True, True),  # 3 -- ablate D3
    Config("no_hybrid", True, False, True, True),  # 4 -- ablate D4
    Config("no_dual", True, True, False, True),  # 5 -- ablate D6
    Config("no_hdrs_no_flt", False, True, True, False),  # 6 -- D16 retrieval-only, no headers
    Config("no_hyb_no_flt", True, False, True, False),  # 7 -- D16 retrieval-only, no hybrid
    Config("bare_baseline", False, False, False, False),  # 8 -- full ablation baseline
]

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class QuestionResult:
    """Outcome for one question under one configuration."""

    row: EvalRow
    top_source: str | None
    top_section: str | None
    gate_score: float  # score from retrieve(); body-only when dual=on, indexed when dual=off
    body_cosine: float  # always body-only cosine -- the value tau is calibrated on
    refused: bool

    def correct_doc(self) -> bool:
        if self.row.kind != "answerable" or self.refused:
            return False
        prefix = self.row.expected_source_prefix
        return (
            self.top_source is not None
            and prefix is not None
            and self.top_source.startswith(prefix)
        )

    def correct_section(self) -> bool:
        # Section correctness only counts when the top chunk is also the right
        # product -- section names repeat verbatim across drugs ("How to
        # store"), so scoring section in isolation would count a wrong-product
        # hit as a correct citation and mask the exact cross-document
        # confusion this eval exists to catch.
        if not self.correct_doc():
            return False
        kw = self.row.expected_section_kw
        return (
            self.top_section is not None
            and kw is not None
            and kw.lower() in self.top_section.lower()
        )


@dataclass
class ConfigResult:
    """Aggregated outcomes for all questions under one configuration."""

    config: Config
    question_results: list[QuestionResult]

    def _of_kind(self, kind: str) -> list[QuestionResult]:
        return [r for r in self.question_results if r.row.kind == kind]

    def answerable_n(self) -> int:
        return len(self._of_kind("answerable"))

    def absent_n(self) -> int:
        return len(self._of_kind("absent_everywhere"))

    def wrong_product_n(self) -> int:
        return len(self._of_kind("present_wrong_product"))

    def correct_doc_count(self) -> int:
        return sum(1 for r in self._of_kind("answerable") if r.correct_doc())

    def correct_section_count(self) -> int:
        return sum(1 for r in self._of_kind("answerable") if r.correct_section())

    def false_refusals(self) -> int:
        return sum(1 for r in self._of_kind("answerable") if r.refused)

    def absent_refused(self) -> int:
        return sum(1 for r in self._of_kind("absent_everywhere") if r.refused)

    def wrong_product_refused(self) -> int:
        return sum(1 for r in self._of_kind("present_wrong_product") if r.refused)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def load_corpus(docs_dir: Path) -> tuple[list[Chunk], list[ProductInfo]]:
    """Parse all PDFs in docs_dir; return (all_chunks, product_infos)."""
    all_chunks: list[Chunk] = []
    product_infos: list[ProductInfo] = []
    pdf_paths = sorted(docs_dir.glob("*.pdf"))
    if not pdf_paths:
        raise RuntimeError(f"No PDFs found in {docs_dir}")
    for pdf_path in pdf_paths:
        chunks = parse_pdf(pdf_path)
        all_chunks.extend(chunks)
        product_infos.append(extract_product_info(chunks))
    return all_chunks, product_infos


def _body_cosine_manual(
    query_vec: npt.NDArray[np.float32],
    chunk_id: str,
    body_vectors: dict[str, npt.NDArray[np.float32]],
) -> float:
    """Compute body-only cosine via dot product (MiniLM is L2-normalised)."""
    bv = body_vectors.get(chunk_id)
    if bv is None:
        return 0.0
    return max(0.0, round(float(np.dot(query_vec, bv)), 6))


def eval_config(
    cfg: Config,
    chunks: list[Chunk],
    product_infos: list[ProductInfo],
    body_vectors: dict[str, npt.NDArray[np.float32]],
    query_vecs: dict[int, npt.NDArray[np.float32]],
    threshold: float,
) -> ConfigResult:
    """Run all eval questions against one configuration; return aggregated results."""
    client = chromadb.EphemeralClient()
    pi_dict: dict[str, ProductInfo] = {pi.source: pi for pi in product_infos}

    # Build a fresh in-memory collection per config -- different header settings
    # produce different embedding vectors, so configs cannot share a collection.
    collection = build_collection(
        chunks,
        client,
        pi_dict,
        use_contextual_headers=cfg.use_contextual_headers,
    )
    # BM25 index mirrors the dense embedding corpus exactly (same header flag).
    # Cheap to build (pure tokenisation); always built to keep the loop simple.
    bm25_index, bm25_chunk_ids = build_bm25_index(
        chunks,
        pi_dict,
        use_contextual_headers=cfg.use_contextual_headers,
    )

    question_results: list[QuestionResult] = []

    for row in EVAL_ROWS:
        retrieved = retrieve(
            row.question,
            collection,
            top_k=TOP_K,
            bm25=bm25_index if cfg.use_hybrid else None,
            bm25_chunk_ids=bm25_chunk_ids if cfg.use_hybrid else None,
            use_hybrid=cfg.use_hybrid,
            body_vectors=body_vectors if cfg.use_dual else None,
            use_dual_embedding=cfg.use_dual,
        )

        # Product-scope filter is applied after retrieval (D13 placement rationale):
        # measuring it here replicates the shipped pipeline exactly.
        if cfg.use_filter:
            retrieved = filter_by_product_scope(row.question, retrieved, product_infos)

        if not retrieved:
            question_results.append(
                QuestionResult(
                    row=row,
                    top_source=None,
                    top_section=None,
                    gate_score=0.0,
                    body_cosine=0.0,
                    refused=True,
                )
            )
            continue

        top = retrieved[0]
        gate_score = top.score
        chunk_id = f"{top.source}::{top.section}"

        # Body-only cosine for calibration: when dual=on the score IS already
        # body-only (dual rescoring replaced the indexed cosine inside retrieve()).
        # When dual=off, retrieve() returns the indexed (header+body) cosine, so
        # we compute body-only separately via a pre-built dot product.
        body_cos: float = (
            gate_score
            if cfg.use_dual
            else _body_cosine_manual(query_vecs[row.idx], chunk_id, body_vectors)
        )

        question_results.append(
            QuestionResult(
                row=row,
                top_source=top.source,
                top_section=top.section,
                gate_score=gate_score,
                body_cosine=body_cos,
                refused=gate_score < threshold,
            )
        )

    return ConfigResult(config=cfg, question_results=question_results)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_KIND_SHORT: dict[str, str] = {
    "answerable": "ans",
    "absent_everywhere": "absent",
    "present_wrong_product": "wrong-prod",
}


def render_markdown(config_results: list[ConfigResult], threshold: float) -> str:
    """Render the full results report as a Markdown string."""
    an = config_results[0].answerable_n() if config_results else 7
    abn = config_results[0].absent_n() if config_results else 2
    wpn = config_results[0].wrong_product_n() if config_results else 2

    lines: list[str] = [
        "# Eval Results",
        "",
        f"**Threshold (tau):** {threshold} | **Questions:** {len(EVAL_ROWS)} "
        f"| **Configs:** {len(config_results)}",
        "",
        "## Summary Table",
        "",
        f"| Config | Flags (H/BM/D/F) | Correct Doc ({an}) "
        f"| Correct Section ({an}) | False Refusals ({an}) "
        f"| Absent Refused ({abn}) | Wrong-Product Refused ({wpn}) |",
        "|---|---|---|---|---|---|---|",
    ]

    for cr in config_results:
        h = "H" if cr.config.use_contextual_headers else "-"
        bm = "BM" if cr.config.use_hybrid else "--"
        d = "D" if cr.config.use_dual else "-"
        f_flag = "F" if cr.config.use_filter else "-"
        flags = f"{h}/{bm}/{d}/{f_flag}"
        lines.append(
            f"| `{cr.config.name}` | `{flags}` "
            f"| {cr.correct_doc_count()}/{an} "
            f"| {cr.correct_section_count()}/{an} "
            f"| {cr.false_refusals()}/{an} "
            f"| {cr.absent_refused()}/{abn} "
            f"| {cr.wrong_product_refused()}/{wpn} |"
        )

    lines += [
        "",
        "## Body-Only Cosines per Question",
        "",
        "Top body-only cosine for the first retrieved chunk (after filter, before gate).",
        "Values below tau are gated as refusals. Primary calibration data for choosing tau.",
        "",
    ]

    config_labels = [f"`{cr.config.name}`" for cr in config_results]
    lines.append("| Q | Kind | Question | " + " | ".join(config_labels) + " |")
    lines.append("|---|---|---|" + "---|" * len(config_results))

    for row in EVAL_ROWS:
        cosines: list[str] = []
        for cr in config_results:
            qr = next(r for r in cr.question_results if r.row.idx == row.idx)
            cosines.append(f"{qr.body_cosine:.3f}")
        q_abbrev = row.question[:46] + ("..." if len(row.question) > 46 else "")
        kind_label = _KIND_SHORT.get(row.kind, row.kind)
        lines.append(f"| {row.idx} | {kind_label} | {q_abbrev} | " + " | ".join(cosines) + " |")

    lines += [
        "",
        "## Notes",
        "",
        "**Flags:** H = contextual headers (D3), BM = BM25 hybrid (D4), "
        "D = dual embedding (D6), F = product-scope filter (D13).",
        "",
        "**D16 comparison pairs:**",
        "- `default` vs `no_filter`: end-to-end (filter on) vs retrieval-only (filter off)",
        "- `no_filter` vs `no_hdrs_no_flt`: headers contribution in retrieval-only mode",
        "- `no_filter` vs `no_hyb_no_flt`: hybrid contribution in retrieval-only mode",
        "",
        "**Refusal kinds:**",
        "- *Absent* (rows 10-11): topic absent from every leaflet; the similarity gate "
        "alone should refuse these.",
        "- *Wrong-product* (rows 7, 9): topic present in corpus but not for the asked-about "
        "product; the gate cannot refuse these by construction -- requires the product-scope "
        "filter (D13). This is why the two counts are reported separately.",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"Docs:      {DOCS_DIR}")
    print(f"Threshold: {SIMILARITY_THRESHOLD}")
    print(f"Output:    {OUTPUT_PATH}")
    print()

    print("Loading corpus...")
    chunks, product_infos = load_corpus(DOCS_DIR)
    print(f"  {len(chunks)} chunks from {len(product_infos)} documents")

    print("Building body-only vectors...")
    body_vectors = build_body_vectors(chunks)

    # Pre-compute all query embeddings in one batch so the per-question,
    # per-config loop only needs dot products (not extra inference calls)
    # when dual=off configs need body-only cosines for calibration.
    print("Pre-computing query embeddings (batch)...")
    ef = _get_embedding_function()
    raw_q_vecs = ef([row.question for row in EVAL_ROWS])
    query_vecs: dict[int, npt.NDArray[np.float32]] = {
        row.idx: np.array(vec, dtype=np.float32)
        for row, vec in zip(EVAL_ROWS, raw_q_vecs, strict=True)
    }

    print(
        f"\nRunning {len(CONFIGS)} configs x {len(EVAL_ROWS)} questions "
        f"(tau={SIMILARITY_THRESHOLD})...\n"
    )
    config_results: list[ConfigResult] = []

    for i, cfg in enumerate(CONFIGS, 1):
        print(f"  [{i}/{len(CONFIGS)}] {cfg.name}", flush=True)
        cr = eval_config(cfg, chunks, product_infos, body_vectors, query_vecs, SIMILARITY_THRESHOLD)
        config_results.append(cr)
        an = cr.answerable_n()
        print(
            f"    doc={cr.correct_doc_count()}/{an}  "
            f"sec={cr.correct_section_count()}/{an}  "
            f"false_refuse={cr.false_refusals()}  "
            f"absent_refused={cr.absent_refused()}/{cr.absent_n()}  "
            f"wrong_prod_refused={cr.wrong_product_refused()}/{cr.wrong_product_n()}"
        )

    md = render_markdown(config_results, SIMILARITY_THRESHOLD)
    OUTPUT_PATH.write_text(md, encoding="utf-8")
    print(f"\nResults written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
