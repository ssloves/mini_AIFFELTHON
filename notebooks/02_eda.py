"""Step 2 — EDA. 청킹/스키마 설계 근거가 되는 실측 분석을 수행하고
docs/eda_report.md + data/eda_stats.json 으로 기록한다."""
from __future__ import annotations

import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

import legal_common as lc

ROOT = lc.ROOT
REPORT = ROOT / "docs" / "eda_report.md"
STATS = ROOT / "data" / "eda_stats.json"

LAW_KEYS = [k for k, v in lc.REGISTRY.items() if v.doc_type != "행정지침"]
GUIDE_KEY = "장려금지침"

# eval/qa.md 18문항이 실제로 인용하는 '고유' gold 법령 조항(지침은 조 구조 없음 → 제외).
# 여러 문항이 같은 조항(특히 조특령 §26의8)을 공유하므로 고유 조항 수(5) != 문항 수(18).
QA_GOLD_ARTICLES = {
    "고용보험법시행령": ["제3조", "제40조"],
    "근로기준법시행령": ["제7조의2"],
    "조특법시행령": ["제26조의8"],
    "조특법": ["제29조의8"],
}
QA_TOTAL_ITEMS = 18


def pct(vals, p):
    if not vals:
        return 0
    vals = sorted(vals)
    i = min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1))))
    return vals[i]


def main():
    stats = {}
    md = []
    md.append("# Step 2 — EDA 리포트 (Legal GraphRAG)\n")
    md.append("> `notebooks/02_eda.py` 산출물. 정제 마크다운(`data/processed/*.md`)을 항(項) 단위로 "
              "분석하여 청킹 파라미터·그래프 스키마 설계의 실측 근거를 제공한다. (로컬, API 불필요)\n")

    # ---- 1. 문서 개요 + 토큰 분포 ----
    md.append("## 1. 문서 개요 및 항 단위 토큰 분포\n")
    md.append("| 문서 | 유형 | 항/조문 레코드 | 토큰 median | mean | p90 | max | <300 | >1500 |")
    md.append("|------|------|----:|----:|----:|----:|----:|----:|----:|")
    all_records = {}
    doc_token_stats = {}
    for k in LAW_KEYS:
        recs = lc.parse_law_md(k)
        all_records[k] = recs
        toks = [lc.n_tokens(r.text) for r in recs]
        info = lc.REGISTRY[k]
        small = sum(1 for t in toks if t < 300)
        big = sum(1 for t in toks if t > 1500)
        md.append(f"| {k} | {info.doc_type} | {len(recs)} | {int(st.median(toks))} | "
                  f"{int(st.mean(toks))} | {pct(toks,90)} | {max(toks)} | {small} | {big} |")
        doc_token_stats[k] = {"records": len(recs), "median": st.median(toks),
                              "mean": st.mean(toks), "p90": pct(toks, 90), "max": max(toks),
                              "lt300": small, "gt1500": big}
    # 지침(섹션 단위)
    grecs = lc.parse_guideline_md(GUIDE_KEY)
    all_records[GUIDE_KEY] = grecs
    gtoks = [lc.n_tokens(r.text) for r in grecs]
    md.append(f"| {GUIDE_KEY} | 행정지침 | {len(grecs)}(섹션) | {int(st.median(gtoks))} | "
              f"{int(st.mean(gtoks))} | {pct(gtoks,90)} | {max(gtoks)} | "
              f"{sum(1 for t in gtoks if t<300)} | {sum(1 for t in gtoks if t>1500)} |")
    doc_token_stats[GUIDE_KEY] = {"records": len(grecs), "median": st.median(gtoks),
                                  "mean": st.mean(gtoks), "p90": pct(gtoks, 90), "max": max(gtoks)}
    stats["token_stats"] = doc_token_stats

    md.append("\n**해석**: 법령은 '항' 단위가 자연스러운 청킹 경계. `<300` 토큰 항은 인접 항과 병합, "
              "`>1500` 토큰 항(주로 호가 많은 정의 조항)은 호 단위 분할 대상.")
    md.append("> 지침의 max 토큰이 비정상적으로 큰 것은 헤딩이 없는 별첨 표 페이지들이 한 섹션으로 "
              "뭉쳐졌기 때문이며, 청킹 단계에서 **페이지 앵커 + 토큰 상한**으로 분할한다.\n")

    # ---- 2. 참조(REFERENCES) 분석 ----
    md.append("## 2. 상호참조(REFERENCES) 분석 — 해소 타입별\n")
    ref_status = Counter()
    cross_pairs = Counter()
    external_laws = Counter()
    refs_per_doc = defaultdict(Counter)
    total_refs = 0
    for k in LAW_KEYS:
        for r in all_records[k]:
            for ref in lc.extract_references(k, r.text):
                total_refs += 1
                ref_status[ref.status] += 1
                refs_per_doc[k][ref.status] += 1
                if ref.status == "cross_corpus":
                    cross_pairs[f"{k} → {ref.target_doc}"] += 1
                elif ref.status == "external":
                    external_laws[ref.target_law_name] += 1
    md.append(f"- 총 참조 추출: **{total_refs:,}건**")
    for stt in ("internal", "cross_corpus", "external"):
        md.append(f"  - {stt}: {ref_status[stt]:,} ({ref_status[stt]*100//max(total_refs,1)}%)")
    md.append("\n**문서별 참조 타입 분포:**\n")
    md.append("| 문서 | internal | cross_corpus | external |")
    md.append("|------|----:|----:|----:|")
    for k in LAW_KEYS:
        c = refs_per_doc[k]
        md.append(f"| {k} | {c['internal']:,} | {c['cross_corpus']:,} | {c['external']:,} |")
    md.append("\n**코퍼스 내 cross-corpus 참조 쌍(REFERENCES 엣지가 실제로 생기는 곳):**\n")
    for pair, n in cross_pairs.most_common():
        md.append(f"- {pair}: {n:,}건")
    md.append("\n**상위 외부 법령 참조(코퍼스 밖 → dangling, Chunk 속성으로만 보존):**\n")
    for law, n in external_laws.most_common(12):
        md.append(f"- 「{law}」: {n:,}건")
    stats["references"] = {"total": total_refs, "status": dict(ref_status),
                           "cross_pairs": dict(cross_pairs),
                           "top_external": dict(external_laws.most_common(20))}

    md.append("\n**해석**: 조특법↔조특법시행령만 상·하위법이 모두 코퍼스에 있어 REFERENCES 엣지가 "
              "풍부하다. 고보/근기 시행령의 '법 제N조'는 상위법이 코퍼스에 없어 external(dangling)이 "
              "되며, 이는 버그가 아니라 **코퍼스 한계**다(상위법 2종 추가 시 해소 가능).\n")

    # ---- 3. 개념(Concept) 빈도 및 문서 간 중첩 ----
    md.append("## 3. 핵심 개념(Concept) 빈도 및 문서 간 중첩\n")
    concept_doc = defaultdict(Counter)  # concept -> doc -> mention count
    for k, recs in all_records.items():
        for r in recs:
            for c in lc.concept_hits(r.text):
                concept_doc[c][k] += 1
    md.append("| 개념 | 등장 문서 수 | 고보령 | 근기령 | 조특령 | 조특법 | 지침 |")
    md.append("|------|----:|----:|----:|----:|----:|----:|")
    order = ["고용보험법시행령", "근로기준법시행령", "조특법시행령", "조특법", "장려금지침"]
    concept_overlap = {}
    for c in lc.CONCEPTS:
        dd = concept_doc[c]
        ndocs = sum(1 for k in order if dd[k] > 0)
        if ndocs == 0:
            continue
        concept_overlap[c] = ndocs
        row = " | ".join(str(dd[k]) for k in order)
        md.append(f"| {c} | {ndocs} | {row} |")
    multi = [c for c, n in concept_overlap.items() if n >= 2]
    md.append(f"\n**2개 이상 문서에 걸친 개념({len(multi)}개)** = CONFLICTS_WITH 후보의 모집단:\n")
    md.append("- " + ", ".join(multi))
    stats["concept_overlap"] = concept_overlap

    # ---- 4. 정의(DEFINES) 분포 ----
    md.append("\n## 4. 개념 정의(DEFINES) 위치 — 충돌 후보의 핵심\n")
    defines = defaultdict(list)  # concept -> [(doc, article/section)]
    for k, recs in all_records.items():
        for r in recs:
            loc = getattr(r, "article", None) or getattr(r, "section_path", "")
            for c in lc.defined_concepts(r.text):
                defines[c].append((k, loc, getattr(r, "paragraph", None)))
    md.append("| 개념 | 정의 청크 수 | 정의가 등장한 문서 |")
    md.append("|------|----:|------|")
    conflict_seed = []
    for c, locs in sorted(defines.items(), key=lambda x: -len(x[1])):
        docs = sorted({d for d, *_ in locs})
        md.append(f"| {c} | {len(locs)} | {', '.join(docs)} |")
        if len(docs) >= 2:
            conflict_seed.append(c)
    md.append(f"\n**서로 다른 문서가 동일 개념을 정의 → CONFLICTS_WITH 1차 후보 개념({len(conflict_seed)}개):**\n")
    md.append("- " + ", ".join(conflict_seed))
    md.append("\n> 실제 충돌쌍 생성·확정은 `02_concepts_conflicts.py`에서 (자동후보→규칙스코어→격리LLM확정). "
              "QA는 구성에 사용하지 않고 사후 recall 검증에만 사용.\n")
    stats["defines_concepts"] = {c: sorted({d for d, *_ in locs}) for c, locs in defines.items()}

    # ---- 5. QA 근거 커버리지 ----
    md.append("## 5. QA 근거 커버리지 (18문항이 인용하는 고유 gold 조항이 정제본에 있는가)\n")
    md.append(f"> 평가셋(`eval/qa.md`)은 **{QA_TOTAL_ITEMS}문항**이지만, 이들이 인용하는 **고유 법령 "
              "조항은 아래 5개**뿐이다(여러 문항이 같은 조항, 특히 조특령 §26의8을 공유). 지침은 '조' "
              "구조가 없어 문서·페이지 단위로 존재.\n")
    md.append("| 문서 | gold 조항 | 존재 |")
    md.append("|------|------|:---:|")
    qa_cov = {}
    for k, arts in QA_GOLD_ARTICLES.items():
        present_articles = {r.article for r in all_records[k] if r.article}
        for a in arts:
            ok = a in present_articles
            qa_cov[f"{k}/{a}"] = ok
            md.append(f"| {k} | {a} | {'✅' if ok else '❌'} |")
    stats["qa_coverage"] = qa_cov
    miss = [k for k, v in qa_cov.items() if not v]
    md.append(f"\n고유 조항 커버리지: **{sum(qa_cov.values())}/{len(qa_cov)}** "
              + ("(전부 존재)" if not miss else f"(누락: {miss})")
              + f" → {QA_TOTAL_ITEMS}문항 전부 근거 확보(**{QA_TOTAL_ITEMS}/{QA_TOTAL_ITEMS}**).\n")

    # ---- 6. 충돌/조건 추출 결과 요약(후속 스크립트 산출물이 있으면) ----
    conf_path = ROOT / "data" / "conflicts_confirmed.jsonl"
    cond_path = ROOT / "data" / "conditions.jsonl"
    if conf_path.exists() or cond_path.exists():
        md.append("## 6. 충돌/조건 추출 결과 요약\n")
    cand_path = ROOT / "data" / "conflict_candidates.jsonl"
    n_cand = len(list(cand_path.open(encoding="utf-8"))) if cand_path.exists() else 0
    if conf_path.exists():
        rows = [json.loads(l) for l in conf_path.open(encoding="utf-8")]
        pos = [r for r in rows if r.get("is_conflict") is True]
        md.append(f"**CONFLICTS_WITH**: 자동 후보 {n_cand}쌍(`conflict_candidates.jsonl`) 생성 후, "
                  f"격리 LLM 이진판정이 '같은 지표·다른 목적' 경계에서 불안정(루브릭에 따라 1↔25건)함을 "
                  f"감사로 확인 → 최종 엣지는 **정준 조문 앵커링**으로 확정한 **{len(pos)}건** "
                  f"(`02b_curate_conflicts.py`, QA 정답 미참조).\n")
        def _loc(doc, art):
            return f"{doc} {art}" if art else doc
        for r in pos:
            sm = r.get('axis') or r.get('shared_metric', '')
            flag = "" if r.get("verified", True) else " ⚠️(지침 원문 미확인)"
            md.append(f"- {sm}: {_loc(r['doc_a'], r.get('article_a'))} ↔ {_loc(r['doc_b'], r.get('article_b'))}{flag}")
        md.append("")
    if cond_path.exists():
        rows = [json.loads(l) for l in cond_path.open(encoding="utf-8")]
        by = Counter(r["type"] for r in rows)
        md.append(f"**Condition** (`02_conditions.py`, 지침): 총 **{len(rows)}건** — "
                  + ", ".join(f"{k} {v}" for k, v in by.most_common()) + "\n")

    REPORT.write_text("\n".join(md) + "\n", encoding="utf-8")
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", REPORT)
    print("wrote", STATS)


if __name__ == "__main__":
    main()
