"""Step 2 — Concept/DEFINES 집계 + CONFLICTS_WITH 후보 생성 → 격리 LLM 확정.

3단계 깔때기 (QA와 완전 분리, QA는 사후 recall 검증 전용):
  1) 자동 후보: 멀티문서 개념에 대해, 개념을 언급하며 '기준 신호'(수치/제외/산정/정의)를
     가진 청크를 문서별로 추려 cross-doc 쌍을 만든다. (DEFINES에만 의존하지 않음)
  2) 규칙 스코어: 두 청크의 수치/단위 집합 차이로 충돌 가능성 점수화.
  3) 격리 LLM 확정: 두 청크 텍스트'만' 보여주고 같은 개념에 다른 기준인지 판정(JSON).
     LLM은 QA를 절대 보지 않음 → 누수 없음.

산출:
  data/concepts.json            — 개념 인벤토리 + 문서별 멘션/정의 위치
  data/conflict_candidates.jsonl — 후보 쌍 + 규칙 스코어
  data/conflicts_confirmed.jsonl — LLM 확정 결과(키 있을 때만)
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import legal_common as lc

ROOT = lc.ROOT
CHUNKS = ROOT / "data" / "chunks" / "all_chunks.jsonl"
CONCEPTS_OUT = ROOT / "data" / "concepts.json"
CAND_OUT = ROOT / "data" / "conflict_candidates.jsonl"
CONF_OUT = ROOT / "data" / "conflicts_confirmed.jsonl"

# 충돌 축이 실재하는 핵심 개념 (EDA 멀티문서 개념 중 선별)
CONFLICT_CONCEPTS = [
    "상시근로자", "청년", "청년등상시근로자", "단시간근로자", "피보험자",
    "기간제근로자", "소정근로시간", "가동일수", "고용유지지원금", "중소기업",
]
# 정의/범위/산정 '큐' — 이 신호가 있어야 그 개념의 '기준'을 정하는 청크로 본다
DEF_CUE = ["란 ", "이란", "말한다", "범위", "제외한", "포함한", "산정", "연인원",
           "가동일수", "계산식", "으로 본다", "로 본다", "준용", "해당하는 사람",
           "해당하는 자", "다음 각 호"]
NUM_UNITS_REL = ("시간", "세", "주", "개월", "년", "명", "일")
MAX_PER_DOC = 3          # 개념·문서당 대표 청크 수(용도 다른 조문도 포함되도록 폭 확대)
MAX_CANDIDATES = 70      # LLM 비용 상한


def load_chunks():
    return [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]


def rel_numeric(text):
    """기준 관련 수치만(시간/세/주/개월/년/명/일). 금액·비율 제외."""
    return {s for s in lc.numeric_signals(text)
            if any(s.endswith(u) for u in NUM_UNITS_REL)}


def signal_score(chunk, concept):
    """이 청크가 해당 개념의 '기준(정의·범위·산정)'을 담고 있을 가능성 점수.
    정의 큐가 전혀 없으면 후보 자격 없음(0) → 단순 언급 청크 배제."""
    t = chunk["text"]
    aliases = lc.CONCEPTS[concept]
    if not any(a in t for a in aliases):
        return 0
    n_cue = sum(1 for kw in DEF_CUE if kw in t)
    if concept not in chunk.get("defines", []) and n_cue == 0:
        return 0  # 기준 정의 신호가 없으면 제외
    score = 0
    if concept in chunk.get("defines", []):
        score += 6
    score += n_cue
    score += min(len(rel_numeric(t)), 4)
    return score


def build_concepts(chunks):
    inv = {}
    for c in lc.CONCEPTS:
        docs = defaultdict(lambda: {"mentions": 0, "defines": []})
        for ch in chunks:
            if c in ch.get("keywords", []):
                docs[ch["source_doc"]]["mentions"] += 1
            if c in ch.get("defines", []):
                docs[ch["source_doc"]]["defines"].append(ch["chunk_id"])
        inv[c] = {k: v for k, v in docs.items()}
    CONCEPTS_OUT.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    return inv


def build_candidates(chunks):
    cands = []
    for concept in CONFLICT_CONCEPTS:
        by_doc = defaultdict(list)
        for ch in chunks:
            sc = signal_score(ch, concept)
            if sc > 0:
                by_doc[ch["source_doc"]].append((sc, ch))
        reps = {}
        for doc, lst in by_doc.items():
            lst.sort(key=lambda x: -x[0])
            reps[doc] = [c for _, c in lst[:MAX_PER_DOC]]
        docs = list(reps)
        for da, db in combinations(docs, 2):
            for ca in reps[da]:
                for cb in reps[db]:
                    na, nb = rel_numeric(ca["text"]), rel_numeric(cb["text"])
                    # 규칙 스코어: 기준 수치가 둘 다 있고 서로 다르면 충돌 가능성↑
                    diff = na.symmetric_difference(nb)
                    rule = (1 if na and nb else 0) + min(len(diff), 5) * 0.5
                    cands.append({
                        "concept": concept,
                        "doc_a": da, "doc_b": db,
                        "chunk_a": ca["chunk_id"], "chunk_b": cb["chunk_id"],
                        "ctx_a": ca.get("context_header", ""), "ctx_b": cb.get("context_header", ""),
                        "numeric_a": sorted(na), "numeric_b": sorted(nb),
                        "rule_score": round(rule, 2),
                        "text_a": ca["text"], "text_b": cb["text"],
                    })
    cands.sort(key=lambda x: -x["rule_score"])
    cands = cands[:MAX_CANDIDATES]
    with CAND_OUT.open("w", encoding="utf-8") as f:
        for c in cands:
            row = {k: v for k, v in c.items() if k not in ("text_a", "text_b")}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return cands


SYS_PROMPT = (
    "당신은 한국 법령 비교 분석가입니다. 제공된 두 텍스트(와 각 텍스트의 조문 위치/제목)만 근거로 "
    "(외부지식·평가질문 금지) 두 텍스트가 '같은 이름의 법적 지표/판단'에 대해 서로 다른 기준을 "
    "제시하는지 판정하십시오.\n"
    "판정 절차:\n"
    "1) legal_purpose_a / legal_purpose_b: 각 텍스트가 그 기준을 무슨 법적 판단을 위해 두는지 조문 "
    "제목으로 파악(예: '우선지원대상기업 규모 판정', '근로기준법 적용범위', '세액공제 대상 상시근로자', "
    "'고용보험 피보험자 자격').\n"
    "2) shared_metric: 두 텍스트가 공통으로 규정하는 '같은 이름의 지표/판단'(예: 상시근로자 수 산정, "
    "단시간 근로자 제외 여부, 청년 연령, 1년 미만 계약직 포함 여부). 공통 지표가 없으면 false.\n"
    "3) criterion_a / criterion_b: 그 지표에 대한 각 텍스트의 구체적 기준/공식/포함범위를 원문에서 인용.\n"
    "4) is_conflict=true 조건: 같은 이름의 지표를 두 문서가 '서로 다른 기준/공식/포함범위'로 규정하여, "
    "한 문서의 기준을 다른 문서의 맥락(같은 근로자/사업장)에 적용하면 결과(인원수·포함여부·자격·연령)가 "
    "달라지는 경우.\n"
    "   ※ 목적(legal_purpose)이 달라도 '같은 이름의 지표를 다르게 산정'하면 충돌로 본다 — 예: 근로기준법 "
    "상시근로자 수 vs 세법 상시근로자 수는 실무자가 한 수치를 재사용하면 틀리므로 충돌(true).\n"
    "   false 조건: (a) 한쪽이 구체적 기준을 제시하지 않음(단순 언급), (b) 서로 무관한 사안을 규율, "
    "(c) 단지 다른 조문을 인용할 뿐 기준이 동일.\n"
    'JSON으로만: {"legal_purpose_a":"...","legal_purpose_b":"...","shared_metric":"...",'
    '"criterion_a":"...","criterion_b":"...","is_conflict":true|false,"reason":"..."}'
)


def llm_confirm(cands):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        print("[SKIP] OPENAI_API_KEY 미설정 — LLM 확정 생략(.env 저장 후 재실행).")
        return False
    from openai import OpenAI
    client = OpenAI(api_key=key)
    model = os.getenv("CONFLICT_MODEL", "gpt-4o")
    # 모델 접근 가능성 1회 프로브 → 실패 시 mini 폴백
    try:
        client.chat.completions.create(model=model,
            messages=[{"role": "user", "content": "ping"}], max_tokens=1)
    except Exception as e:
        print(f"[WARN] {model} 사용 불가({str(e)[:60]}) → gpt-4o-mini 폴백")
        model = "gpt-4o-mini"
    results = []
    for i, c in enumerate(cands, 1):
        user = (f"[개념] {c['concept']}\n\n"
                f"[텍스트 A — 위치: {c.get('ctx_a','')}]\n{c['text_a'][:1600]}\n\n"
                f"[텍스트 B — 위치: {c.get('ctx_b','')}]\n{c['text_b'][:1600]}")
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYS_PROMPT},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            j = json.loads(resp.choices[0].message.content)
        except Exception as e:
            j = {"is_conflict": None, "reason": f"ERROR {e}"}
        j.update({"concept": c["concept"], "doc_a": c["doc_a"], "doc_b": c["doc_b"],
                  "chunk_a": c["chunk_a"], "chunk_b": c["chunk_b"], "rule_score": c["rule_score"]})
        results.append(j)
        if i % 10 == 0:
            print(f"  confirmed {i}/{len(cands)}")
    with CONF_OUT.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    pos = sum(1 for r in results if r.get("is_conflict") is True)
    print(f"[LLM] {len(results)}쌍 판정 → 충돌 확정 {pos}쌍  (model={model})")
    return True


def main():
    chunks = load_chunks()
    inv = build_concepts(chunks)
    print(f"concepts.json: {len(inv)} concepts")
    cands = build_candidates(chunks)
    print(f"conflict_candidates: {len(cands)}쌍 (상위 rule_score)")
    for c in cands[:8]:
        print(f"  [{c['rule_score']}] {c['concept']}: {c['doc_a']} ↔ {c['doc_b']}  "
              f"A{c['numeric_a'][:4]} B{c['numeric_b'][:4]}")
    llm_confirm(cands)


if __name__ == "__main__":
    main()
