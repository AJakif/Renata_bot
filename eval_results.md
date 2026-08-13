# Eval Results

**Threshold (tau):** 0.35 | **Questions:** 11 | **Configs:** 8

## Summary Table

| Config | Flags (H/BM/D/F) | Correct Doc (7) | Correct Section (7) | False Refusals (7) | Absent Refused (2) | Wrong-Product Refused (2) |
|---|---|---|---|---|---|---|
| `default` | `H/BM/D/F` | 6/7 | 2/7 | 1/7 | 0/2 | 1/2 |
| `no_filter` | `H/BM/D/-` | 6/7 | 2/7 | 0/7 | 0/2 | 0/2 |
| `no_headers` | `-/BM/D/F` | 7/7 | 2/7 | 0/7 | 0/2 | 1/2 |
| `no_hybrid` | `H/--/D/F` | 7/7 | 2/7 | 0/7 | 0/2 | 0/2 |
| `no_dual` | `H/BM/-/F` | 7/7 | 7/7 | 0/7 | 0/2 | 0/2 |
| `no_hdrs_no_flt` | `-/BM/D/-` | 7/7 | 2/7 | 0/7 | 0/2 | 0/2 |
| `no_hyb_no_flt` | `H/--/D/-` | 7/7 | 2/7 | 0/7 | 0/2 | 0/2 |
| `bare_baseline` | `-/--/-/-` | 7/7 | 2/7 | 0/7 | 0/2 | 0/2 |

## Body-Only Cosines per Question

Top body-only cosine for the first retrieved chunk (after filter, before gate).
Values below tau are gated as refusals. Primary calibration data for choosing tau.

| Q | Kind | Question | `default` | `no_filter` | `no_headers` | `no_hybrid` | `no_dual` | `no_hdrs_no_flt` | `no_hyb_no_flt` | `bare_baseline` |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ans | How should I store Rolip? | 0.276 | 0.447 | 0.498 | 0.498 | 0.276 | 0.498 | 0.498 | 0.498 |
| 2 | ans | How should Doxicap be stored? | 0.573 | 0.573 | 0.573 | 0.573 | 0.368 | 0.573 | 0.573 | 0.573 |
| 3 | ans | How do I take Maxpro if I can't swallow tablet... | 0.574 | 0.574 | 0.574 | 0.574 | 0.391 | 0.574 | 0.574 | 0.574 |
| 4 | ans | What is Rolac used for? | 0.769 | 0.769 | 0.769 | 0.769 | 0.769 | 0.769 | 0.769 | 0.769 |
| 5 | ans | What is Fenadin used for? | 0.674 | 0.674 | 0.674 | 0.674 | 0.674 | 0.674 | 0.674 | 0.674 |
| 6 | ans | Is Doxicap safe for children? | 0.729 | 0.729 | 0.729 | 0.729 | 0.340 | 0.729 | 0.729 | 0.729 |
| 7 | wrong-prod | Is Rolac safe for children? | 0.616 | 0.616 | 0.616 | 0.616 | 0.324 | 0.616 | 0.616 | 0.616 |
| 8 | ans | Can I drive after taking Fenadin? | 0.503 | 0.503 | 0.503 | 0.503 | 0.412 | 0.503 | 0.503 | 0.503 |
| 9 | wrong-prod | Can I drive after taking Rolac? | 0.316 | 0.414 | 0.316 | 0.551 | 0.305 | 0.414 | 0.551 | 0.551 |
| 10 | absent | Can I drink alcohol while taking Maxpro? | 0.527 | 0.527 | 0.527 | 0.527 | 0.527 | 0.527 | 0.527 | 0.527 |
| 11 | absent | What if I miss a dose of Rolip? | 0.558 | 0.558 | 0.558 | 0.558 | 0.245 | 0.558 | 0.558 | 0.558 |

## Notes

**Flags:** H = contextual headers (D3), BM = BM25 hybrid (D4), D = dual embedding (D6), F = product-scope filter (D13).

**D16 comparison pairs:**
- `default` vs `no_filter`: end-to-end (filter on) vs retrieval-only (filter off)
- `no_filter` vs `no_hdrs_no_flt`: headers contribution in retrieval-only mode
- `no_filter` vs `no_hyb_no_flt`: hybrid contribution in retrieval-only mode

**Refusal kinds:**
- *Absent* (rows 10-11): topic absent from every leaflet; the similarity gate alone should refuse these.
- *Wrong-product* (rows 7, 9): topic present in corpus but not for the asked-about product; the gate cannot refuse these by construction -- requires the product-scope filter (D13). This is why the two counts are reported separately.
