"""Measure same-section cross-product TF-IDF cosine similarity over all 5 leaflets.

Reproduces the collision table from PLAN_AND_DECISIONS.md §EDA, now over all
five leaflets (doxicap, fenadin, maxpro, rolac, rolip).

Methodology
-----------
- Parse each PDF with app.parser.parse_pdf (same parsing the app uses).
- Drop the §0 "Product overview" chunk; retain §1–§6 in order.
- For each section position k (0-indexed), gather the five body strings.
- Vectorise with TF-IDF (sklearn-compatible smooth IDF, L2-normalised).
- Compute cosine similarity for all C(5,2)=10 cross-product pairs.
- Report mean and worst (max) pair per section.

Dependency: numpy (transitively available via chromadb). scikit-learn is NOT
required — a minimal numpy TF-IDF is implemented here to keep the script
self-contained without adding a heavy dev dependency for a one-off measurement.
The smooth-IDF formula (log((1+N)/(1+df))+1, L2-normalised) matches sklearn's
TfidfVectorizer defaults. Two deviations from sklearn's own defaults: (1)
tokenization here is alpha-only (`[a-z]+`), dropping digits and 1-character
tokens, vs sklearn's `\b\w\w+\b` which keeps digits but drops 1-char tokens —
numeric quantities (doses, temperatures) are excluded from vocabulary here;
(2) IDF is fit per-section, over just the 5 same-section bodies being compared,
not corpus-wide — this measures within-section cross-product overlap, which is
the collision question this table answers, but means df/idf values are not
comparable across section rows.

Usage (from repo root)
----------------------
    python scripts/measure_similarity.py
    python scripts/measure_similarity.py --docs-dir docs          # explicit path
    python scripts/measure_similarity.py --docs-dir docs --verbose # show all pairs
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
from pathlib import Path
from typing import TypedDict

import numpy as np
import numpy.typing as npt

# ---------------------------------------------------------------------------
# Make app/ importable when run as a top-level script from the repo root.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.parser import Chunk, parse_pdf  # noqa: E402  (after sys.path tweak)

# ---------------------------------------------------------------------------
# Canonical section labels (for display only — matching is positional).
# ---------------------------------------------------------------------------
SECTION_LABELS: list[str] = [
    "§1 What it is",
    "§2 Before you take",
    "§3 How to take",
    "§4 Side effects",
    "§5 Pregnancy",
    "§6 How to store",
]


class SectionResult(TypedDict):
    """Similarity measurement for one section position."""

    section: str
    mean: float
    worst_sim: float
    worst_pair: tuple[str, str]


# ---------------------------------------------------------------------------
# Minimal TF-IDF implementation (numpy, no scikit-learn).
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase alphabetic tokens only — no stop-word removal."""
    return _TOKEN_RE.findall(text.lower())


def _tfidf_matrix(docs: list[str]) -> npt.NDArray[np.float64]:
    """Return an L2-normalised TF-IDF matrix (n_docs × n_terms).

    Formula: tfidf(t,d) = tf(t,d) * idf(t)
      - tf(t,d)  = count(t in d)                        (raw count)
      - idf(t)   = log((1 + N) / (1 + df(t))) + 1      (sklearn smooth IDF)
      - rows are L2-normalised so dot product == cosine similarity.
    """
    token_lists = [_tokenize(doc) for doc in docs]
    vocab = sorted({tok for toks in token_lists for tok in toks})
    term_index = {tok: i for i, tok in enumerate(vocab)}
    N = len(docs)
    n_terms = len(vocab)

    # Raw count matrix (float64 for subsequent arithmetic)
    tf = np.zeros((N, n_terms), dtype=np.float64)
    for doc_i, toks in enumerate(token_lists):
        for tok in toks:
            tf[doc_i, term_index[tok]] += 1.0

    # Document frequency per term
    df = (tf > 0).sum(axis=0).astype(np.float64)

    # Smooth IDF (sklearn default)
    idf = np.log((1.0 + N) / (1.0 + df)) + 1.0

    tfidf = tf * idf  # broadcast: (N, n_terms) * (n_terms,)

    # L2 normalise each row
    norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)  # avoid divide-by-zero on empty docs
    return tfidf / norms


def cosine_similarities(
    matrix: npt.NDArray[np.float64],
) -> list[tuple[int, int, float]]:
    """Return all C(n,2) pairwise cosines as (i, j, sim) triples."""
    n = matrix.shape[0]
    results: list[tuple[int, int, float]] = []
    for i, j in itertools.combinations(range(n), 2):
        sim = float(np.dot(matrix[i], matrix[j]))
        results.append((i, j, sim))
    return results


# ---------------------------------------------------------------------------
# Main measurement logic
# ---------------------------------------------------------------------------


def collect_numbered_sections(pdf_paths: list[Path]) -> list[list[Chunk]]:
    """Parse all PDFs; return a list[6] where entry k holds one Chunk per PDF.

    Entry k corresponds to section number k+1 (§1=index 0, §6=index 5).
    """
    # sections_by_pos[k] = list of chunks for section position k, one per PDF
    sections_by_pos: list[list[Chunk]] = [[] for _ in range(6)]

    for pdf_path in pdf_paths:
        chunks = parse_pdf(pdf_path)
        numbered = [c for c in chunks if c.section != "Product overview"]
        if len(numbered) != 6:
            raise ValueError(f"{pdf_path.name}: expected 6 numbered sections, got {len(numbered)}")
        for k, chunk in enumerate(numbered):
            sections_by_pos[k].append(chunk)

    return sections_by_pos


def measure(
    docs_dir: Path,
    *,
    verbose: bool = False,
) -> list[SectionResult]:
    """Run the full measurement; return per-section SectionResult dicts."""
    pdf_paths = sorted(docs_dir.glob("*.pdf"))
    if len(pdf_paths) < 2:
        raise ValueError(f"Need at least 2 PDFs in {docs_dir}; found {len(pdf_paths)}")

    print(f"Measuring over {len(pdf_paths)} documents:")
    for p in pdf_paths:
        print(f"  {p.name}")
    print()

    sections_by_pos = collect_numbered_sections(pdf_paths)
    source_names = [c.source for c in sections_by_pos[0]]  # ordered list of filenames

    results: list[SectionResult] = []

    for k, chunks in enumerate(sections_by_pos):
        label = SECTION_LABELS[k]
        bodies = [c.body for c in chunks]
        matrix = _tfidf_matrix(bodies)
        pairs = cosine_similarities(matrix)

        sims = [sim for _, _, sim in pairs]
        mean_sim = sum(sims) / len(sims)
        worst = max(pairs, key=lambda t: t[2])
        worst_sim = worst[2]
        worst_pair: tuple[str, str] = (source_names[worst[0]], source_names[worst[1]])

        if verbose:
            print(f"{label}:")
            for i, j, sim in sorted(pairs, key=lambda t: -t[2]):
                print(f"  {source_names[i]:45s} vs {source_names[j]:45s}  {sim:.4f}")
            print()

        results.append(
            SectionResult(
                section=label,
                mean=mean_sim,
                worst_sim=worst_sim,
                worst_pair=worst_pair,
            )
        )

    return results


def _short_name(filename: str) -> str:
    """Strip path and .pdf suffix for compact display."""
    return Path(filename).stem.split("_")[0]  # e.g. "doxicap"


def print_table(results: list[SectionResult]) -> None:
    print("| Section | avg | worst |")
    print("|---|---|---|")
    for r in results:
        section = r["section"]
        mean = r["mean"]
        worst_sim = r["worst_sim"]
        a, b = r["worst_pair"]
        pair_str = f"{_short_name(a)} vs {_short_name(b)}"
        mean_str = f"**{mean:.2f}**" if mean >= 0.5 else f"{mean:.2f}"
        print(f"| {section} | {mean_str} | {worst_sim:.2f} ({pair_str}) |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docs-dir",
        default="docs",
        help="Directory containing the leaflet PDFs (default: docs/)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all pairwise similarities, not just mean/worst.",
    )
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir)
    if not docs_dir.is_dir():
        sys.exit(f"ERROR: docs directory not found: {docs_dir}")

    results = measure(docs_dir, verbose=args.verbose)

    print("Same-section cross-product TF-IDF cosine similarity (all 5 leaflets):")
    print()
    print_table(results)


if __name__ == "__main__":
    main()
