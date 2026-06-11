"""Step 2 (충돌 확정 — 감사 후 재설계).

배경: 자동 후보(conflict_candidates.jsonl, 70쌍)는 유지하되, 격리 LLM 이진판정이
'같은 지표·다른 목적' 경계에서 불안정(루브릭에 따라 1↔25건)함을 감사로 확인.
→ 최종 CONFLICTS_WITH 엣지는 **정준 조문에 앵커링한 전문가 확정 집합**으로 둔다.
   (QA 정답 미참조 — 멀티문서 개념의 정의 차이라는 도메인 사실에 기반)

각 충돌: 같은 이름의 지표를 두 문서가 다른 기준으로 규정 → 한 수치를 다른 맥락에
재사용하면 결과가 달라지는 쌍. 조문 번호·용도(purpose)를 정확히 명시한다.
산출: data/conflicts_confirmed.jsonl (재작성)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import legal_common as lc

ROOT = lc.ROOT
CHUNKS = ROOT / "data" / "chunks" / "all_chunks.jsonl"
OUT = ROOT / "data" / "conflicts_confirmed.jsonl"

# 정준 충돌 명세 — (concept, axis, anchor_a, anchor_b, purpose_a, purpose_b, note, verified)
# anchor = (doc, article) 또는 지침은 (doc, None, page)
CANON = [
    dict(concept="상시근로자", axis="상시근로자 수 산정식",
         a=("근로기준법시행령", "제7조의2"), b=("조특법시행령", "제26조의8"),
         purpose_a="근로기준법 적용범위 산정", purpose_b="세액공제 대상 상시근로자 산정",
         note="근기령 §7의2①: 연인원÷가동일수 / 조특령 §26의8: 매월 말일 인원 평균 + 1년미만·월60h미만·임원·친족 제외. 같은 '상시근로자 수'를 다르게 산정 → 한 수치 재사용 시 오류(예: 근기 4.8명을 세액공제 신청서에 전용 불가).",
         verified=True),
    dict(concept="상시근로자", axis="상시근로자 수 산정(고용보험 규모판정)",
         a=("고용보험법시행령", "제12조"), b=("조특법시행령", "제26조의8"),
         purpose_a="우선지원대상기업 규모 판정", purpose_b="세액공제 대상 상시근로자 산정",
         note="고보령 §12(⑤)는 단시간 0.5명 가중 등 '우선지원대상기업 규모' 판정용 산정 — 세법 §26의8 산정과 목적·공식 모두 별개. (피보험자 자격 기준 §3①과도 다른 조문)",
         verified=True),
    dict(concept="단시간근로자", axis="단시간 근로자 제외 기준",
         a=("고용보험법시행령", "제3조"), b=("조특법시행령", "제26조의8"),
         purpose_a="고용보험 피보험자 자격(주15h/월60h)", purpose_b="세법 상시근로자 단시간 제외(월60h)",
         note="고보령 §3①: 1개월 소정 60h 미만(또는 주 15h 미만)은 피보험자 적용제외(단 3개월 이상 계속근로 시 적용) / 조특령 §26의8②2호: 월 60h 미만 단시간은 세법 상시근로자에서 제외. 기준 표현·용도가 달라 동일 근로자도 결과가 갈릴 수 있음.",
         verified=True),
    dict(concept="기간제근로자", axis="1년 미만 계약직 포함 여부",
         a=("근로기준법시행령", "제7조의2"), b=("조특법시행령", "제26조의8"),
         purpose_a="근기 상시 산정(고용형태 불문 포함)", purpose_b="세법 상시 산정(계약 1년미만 제외)",
         note="근기령 §7의2④: 기간제·단시간 모두 포함(1년 미만 제외규정 없음) / 조특령 §26의8②1호: 근로계약 1년 미만 제외(단 총 1년 이상 계속고용은 포함). ※ '기간제라서' 제외가 아니라 '1년 미만이라서' 제외 — 1년 이상 기간제는 조특령도 포함.",
         verified=True),
    dict(concept="청년", axis="청년 연령 기준",
         a=("고용보험법시행령", "제17조"), b=("조특법시행령", "제81조"),
         purpose_a="고용촉진 청년(실업자) 15~34세", purpose_b="중소기업 취업 청년 소득세 감면(15~34세, 군복무기간 차감)",
         note="제도별 청년 연령 정의·산정(군복무 차감 여부) 차이.",
         verified=True),
    dict(concept="청년", axis="청년 정의(장려금 참여)",
         a=("조특법시행령", "제26조의8"), b=("장려금지침", None, 25),
         purpose_a="세법 청년등상시근로자", purpose_b="장려금 참여 청년 요건",
         note="⚠️ 지침의 청년 참여연령 세부(예: 군필 가산 39세 등)는 지침 원문 대조 미완료 — LLM 추출(02_conditions.py) 기반 값이므로 KG/메트릭 반영 전 원문 검증 필요.",
         verified=False),
    dict(concept="인원산정기준(피보험자vs상시근로자)", axis="장려금 피보험자 수 vs 세법 상시근로자 수",
         a=("장려금지침", None, 36), b=("조특법시행령", "제26조의8"),
         purpose_a="장려금: 직전 1년 평균 고용보험 피보험자 수", purpose_b="세액공제: 세법 상시근로자 수",
         note="동일 인원이 제도별로 다른 기준(피보험자 수 vs 1년·60h·임원/친족 제외 상시근로자)으로 집계 — 장려금 신청서 인원을 세액공제에 전용 불가(QA Q10). cross-concept 축(도메인 명시 입력).",
         verified=True),
]

CRIT = ["제외", "이상", "미만", "산정", "연인원", "가동일수", "말한다", "범위", "포함", "피보험자", "60", "15", "34"]


def pick_chunk(chunks, doc, article=None, page=None, concept=None):
    aliases = lc.CONCEPTS.get(concept, [concept]) if concept else []
    cand = []
    for c in chunks:
        if c["source_doc"] != doc:
            continue
        if article is not None and c.get("article") != article:
            continue
        if page is not None and c.get("page") != page:
            continue
        t = c["text"]
        score = sum(t.count(a) for a in aliases) + sum(1 for k in CRIT if k in t)
        cand.append((score, c["chunk_id"], c))
    if not cand:
        return None
    cand.sort(key=lambda x: -x[0])
    return cand[0][2]


def main():
    chunks = [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]
    rows = []
    for spec in CANON:
        a = spec["a"]
        b = spec["b"]
        ca = pick_chunk(chunks, a[0], a[1] if len(a) > 1 else None,
                        a[2] if len(a) > 2 else None, spec["concept"])
        cb = pick_chunk(chunks, b[0], b[1] if len(b) > 1 else None,
                        b[2] if len(b) > 2 else None, spec["concept"])
        if ca is None or cb is None:
            print(f"[WARN] 앵커 청크 못 찾음: {spec['axis']} (a={ca is not None}, b={cb is not None})")
            continue
        rows.append({
            "is_conflict": True,
            "method": "curated_from_candidates",
            "concept": spec["concept"],
            "axis": spec["axis"],
            "doc_a": a[0], "doc_b": b[0],
            "chunk_a": ca["chunk_id"], "chunk_b": cb["chunk_id"],
            "article_a": ca.get("article"), "article_b": cb.get("article"),
            "legal_purpose_a": spec["purpose_a"], "legal_purpose_b": spec["purpose_b"],
            "shared_metric": spec["axis"],
            "note": spec["note"],
            "verified": spec["verified"],
        })
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[DONE] 정준 충돌 {len(rows)}건 -> {OUT}")
    for r in rows:
        flag = "" if r["verified"] else "  [미검증]"
        print(f"  - {r['axis']}: {r['chunk_a']} <-> {r['chunk_b']}{flag}")


if __name__ == "__main__":
    main()
