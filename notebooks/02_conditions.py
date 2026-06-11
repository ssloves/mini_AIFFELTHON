"""Step 2 — 장려금지침 Condition 구조화 추출 (타깃 LLM).

지침은 반정형이라 구조 파싱 대신, 본문이 깨끗한 점을 활용해 핵심 섹션의 지침 청크를
LLM으로 구조화 추출한다(엄격 JSON 스키마). 결과: data/conditions.jsonl

Condition 스키마:
  {condition_id, type, description, numeric_criteria[], applies_to, page, refs[], source_chunk_id}
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import legal_common as lc

ROOT = lc.ROOT
CHUNKS = ROOT / "data" / "chunks" / "all_chunks.jsonl"
OUT = ROOT / "data" / "conditions.jsonl"

# 요건/조건이 담긴 청크 선별 큐
SELECT_CUES = ["요건", "대상", "제외", "근로계약", "근로시간", "소정근로", "중복",
               "사후관리", "지급 중단", "피보험자", "5인", "30시간", "12개월",
               "만 15", "34세", "39세", "참여", "지원금"]
MAX_CHUNKS = 45

SYS = (
    "당신은 행정지침에서 '지원 요건/조건'을 구조화하는 추출기입니다. 주어진 지침 텍스트에서 "
    "독립적인 조건을 모두 뽑아 JSON 객체 {\"conditions\":[...]} 로만 답하십시오. 각 condition은 "
    "{type, description, numeric_criteria, applies_to, refs} 필드를 가집니다.\n"
    "- type: 지원대상/지원제외/기업요건/근로조건/중복지원/사후관리/기타 중 하나\n"
    "- description: 조건을 한 문장으로 명확히 (원문 근거)\n"
    "- numeric_criteria: 수치 기준 배열(예: [\"만 15~34세\",\"주 30시간 이상\",\"피보험자 5인 이상\"]). 없으면 []\n"
    "- applies_to: 유형Ⅰ/유형Ⅱ/공통 중 추정값(불명이면 공통)\n"
    "- refs: 본문이 인용하는 별첨·법령 배열(예: [\"별첨1\",\"고용정책기본법 제32조\"]). 없으면 []\n"
    "텍스트에 실제 조건이 없으면 conditions를 빈 배열로 두십시오. 추측으로 만들지 마십시오."
)


def select_chunks():
    rows = [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]
    g = [c for c in rows if c["source_doc"] == "장려금지침" and c.get("section") == "지침"]
    scored = []
    for c in g:
        t = c["text"]
        if c["token_count"] < 80:
            continue
        s = sum(1 for kw in SELECT_CUES if kw in t)
        if s >= 2:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:MAX_CHUNKS]]


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        print("[SKIP] OPENAI_API_KEY 미설정 — Condition 추출 생략(.env 저장 후 재실행).")
        return
    from openai import OpenAI
    client = OpenAI(api_key=key)
    model = os.getenv("CONDITION_MODEL", "gpt-4o-mini")

    chunks = select_chunks()
    print(f"선별된 지침 청크: {len(chunks)}개 (model={model})")
    conditions = []
    cid = 0
    for i, c in enumerate(chunks, 1):
        user = (f"[페이지 {c['page']}] [섹션 {c.get('section_path','')}]\n{c['text'][:1800]}")
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYS},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"}, temperature=0)
            data = json.loads(resp.choices[0].message.content)
            items = data.get("conditions", []) if isinstance(data, dict) else []
        except Exception as e:
            items = []
            print(f"  [ERR p{c['page']}] {str(e)[:60]}")
        for it in items:
            cid += 1
            conditions.append({
                "condition_id": f"COND_{cid:03d}",
                "source_doc": "장려금지침",
                "type": it.get("type", "기타"),
                "description": it.get("description", ""),
                "numeric_criteria": it.get("numeric_criteria", []),
                "applies_to": it.get("applies_to", "공통"),
                "page": c["page"],
                "refs": it.get("refs", []),
                "source_chunk_id": c["chunk_id"],
            })
        if i % 10 == 0:
            print(f"  {i}/{len(chunks)} processed, {len(conditions)} conditions")

    with OUT.open("w", encoding="utf-8") as f:
        for x in conditions:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    from collections import Counter
    by_type = Counter(x["type"] for x in conditions)
    print(f"[DONE] {len(conditions)} conditions -> {OUT}")
    print("  by type:", dict(by_type))


if __name__ == "__main__":
    main()
