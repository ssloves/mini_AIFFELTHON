# My Contribution — Se Mi Song (송세미)

Fork of a team project ([original repo](https://github.com/seongyeon-h/mini_AIFFELTHON)).
Team MultiHop: 송세미 (Se Mi Song) · 이희선 (Huiseon Lee) · 황성연 (Seongyeon Hwang).

This file documents the parts I personally led. For the full system, see the
[main README](https://github.com/seongyeon-h/mini_AIFFELTHON/blob/main/README.md).

## Project in one line
A GraphRAG evaluation framework for Korean labor & tax law, testing whether a
graph that anchors *cross-statute conflicts* retrieves legal evidence that plain
vector RAG misses — and rigorously attributing *where* any advantage comes from.

## Role — Evaluation Design & Methodology

**Evaluation framework.** Designed the evaluation framework: a 6-type question
taxonomy (cross-document conflict, single-passage precision, multi-hop,
same-name no-conflict, abstention, purpose-divergence), held-out controls
(45 held-out / 33 generated, separating the QA set from graph construction to
avoid circular validation), and data integrity gates G0–G6
(`eval/dataset_gates.py`). Co-authored the 78-item human-reviewed gold set
(`eval/gold_set_final.json`) with the team.

**Metrics.** Designed and implemented the evaluation metrics:
- Context Recall at 조 / 항·호 (article / clause) precision (`score_precision.py`)
- Path Faithfulness — connection precision vs. coincidental arrival (`path_metric.py`)
- Factor attribution analysis (`attribution_metrics.py`)

**Ablation.** Built the B0–B4 ablation ladder (`ablation_ladder.py`) that
decomposed *why* GraphRAG won — attributing the gain to concept-conflict anchors
(net Context Recall **+0.321**), while showing that plain graph expansion *hurt*
performance (**−0.088**).

**Honest limitations.** Documented the failure modes rather than hiding them —
anchor overflagging on distractors (**FP 14**, the key open problem) and small-n
caveats (hop3 n=8, held-out conflict n=8).

## The finding I'm most proud of
Not "GraphRAG is better," but a sharper, falsifiable claim: in a domain where
documents barely cross-reference each other, value comes from anchoring the
*few decisive conflict points* — and indiscriminate graph expansion is actively
harmful (−0.088). I designed the evaluation specifically to **attribute** the
advantage to its real source, not just to report that it existed.
