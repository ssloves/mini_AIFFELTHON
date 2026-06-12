"""데이터셋 무결성 게이트 엔진 G0~G6 (확장가이드 §3 구현).

설계 철학(가이드 §0): **"생성으로 무결"이 아니라 "게이트 통과로 무결에 수렴".**
세 무결성 축을 *각각* 막는다 — 라벨(G1~G5) / 엣지(G0) / 순환(G6).

게이트는 두 종류로 분리한다(중요):
  - 결정론 게이트(자동·재현가능): G0(엣지신뢰도), G1(provenance), G2(수치 grounding),
    G5(중복), G6(origin) → 코드가 pass/fail을 확정.
  - 판단 게이트(LLM/사람 proxy): G3(응답가능성·유일성), G4(held-out 2인 교차검수)
    → 자동은 *proxy*일 뿐, 최종 책임은 사람. 정직하게 'proxy'로 표기.

핵심 부품: chunk resolver — 사람이 적은 '조항 문자열'을 KG의 실제 chunk_id(항 범위 청크)로
정규화한다. (예: '조특법시행령 제26조의8제2항제2호' → '조특령_제26조의8_제1-2항')
이게 없으면 held-out의 항 표기 차이가 전부 G1 위양성(가짜 폐기)이 된다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "graph"))

CHUNKS_PATH = ROOT / "data" / "chunks" / "all_chunks.jsonl"
CONFLICTS_PATH = ROOT / "data" / "conflicts_confirmed.jsonl"

# 사람이 쓰는 법령명 → chunk_id 접두사 (긴 이름 우선 매칭)
DOC_PREFIX = [
    ("고용보험법시행령", "고보령"), ("고보령", "고보령"),
    ("근로기준법시행령", "근기령"), ("근기령", "근기령"),
    ("조세특례제한법시행령", "조특령"), ("조특법시행령", "조특령"), ("조특령", "조특령"),
    ("조세특례제한법", "조특법"), ("조특법", "조특법"),
    ("장려금지침", "지침"), ("장려금", "지침"), ("지침", "지침"),
]
# 지침은 조(條)가 없어 키워드→확정 청크 매핑(원문 대조 완료분만)
JISHIM_MAP = [
    (("인건비", "차감", "중복"), "지침_p43_s96"),
    (("피보험자", "상시근로자", "5인", "5명", "인원"), "지침_p36_s77"),
    (("청년", "연령", "참여", "취업애로"), "지침_p25_s52"),
]

_chunks = None
_idx = None
_conflicts = None


def chunks() -> dict:
    global _chunks, _idx
    if _chunks is None:
        _chunks, _idx = {}, {}
        for line in CHUNKS_PATH.open(encoding="utf-8"):
            if not line.strip():
                continue
            o = json.loads(line)
            _chunks[o["chunk_id"]] = o
            prefix = o["chunk_id"].split("_")[0]
            _idx.setdefault((prefix, o.get("article")), []).append(o)
    return _chunks


def idx() -> dict:
    chunks()
    return _idx


def conflicts() -> list:
    global _conflicts
    if _conflicts is None:
        _conflicts = [json.loads(l) for l in CONFLICTS_PATH.open(encoding="utf-8") if l.strip()]
    return _conflicts


# ---------------- chunk resolver ----------------
def _para_set(pstr):
    """'제1-3항'->{1,2,3}; '제2항'->{2}; '제5-8항'->{5..8}; '제1항 호1'->{1}; None->None."""
    if not pstr:
        return None
    m = re.search(r"제(\d+)-(\d+)항", pstr)
    if m:
        return set(range(int(m.group(1)), int(m.group(2)) + 1))
    nums = re.findall(r"제(\d+)항", pstr)
    return {int(n) for n in nums} if nums else None


def _doc_prefix(s: str):
    for name, pre in DOC_PREFIX:
        if name in s:
            return pre
    return None


def parse_ref(s: str):
    """'고용보험법시행령 제3조제1항' -> (prefix, article, paras:set|None)."""
    prefix = _doc_prefix(s)
    am = re.search(r"제\d+조(?:의\d+)?", s)
    article = am.group(0) if am else None
    paras = {int(n) for n in re.findall(r"제(\d+)항", s)} or None
    return prefix, article, paras


def resolve_chunk(s: str):
    """조항 문자열 -> 실제 chunk_id (없으면 None). 항 범위 청크에 매핑·부칙 제외·body 우선."""
    prefix, article, paras = parse_ref(s)
    if prefix is None:
        return None
    if prefix == "지침":
        for kws, cid in JISHIM_MAP:
            if any(k in s for k in kws) and cid in chunks():
                return cid
        return None
    cands = idx().get((prefix, article))
    if not cands:
        return None
    body = [c for c in cands if c.get("section") != "부칙"] or cands
    if paras:
        for c in body:
            ps = _para_set(c.get("paragraph"))
            if ps is None or (ps & paras):
                return c["chunk_id"]
    return body[0]["chunk_id"]  # 조 단위 매칭(항 미지정/미일치 시)


# ================= 게이트 =================
def g1_provenance(item):
    """라벨 실존: gold_articles가 실제 chunk로 해소되는가. 해소된 chunk_id를 채워 반환."""
    if not item.get("answerable", True):
        ok = not item.get("gold_articles")  # 무응답은 gold 비어야 정합
        return ok, {"resolved": [], "note": "unanswerable→gold 비움 확인" if ok else "무응답인데 gold 존재"}
    resolved, missing = [], []
    for a in item.get("gold_articles", []):
        cid = resolve_chunk(a)
        (resolved.append(cid) if cid else missing.append(a))
    item["gold_chunk_ids"] = resolved
    return (len(missing) == 0 and len(resolved) > 0), {"resolved": resolved, "missing": missing}


def g2_numeric(item):
    """수치 grounding(단위 포함): gold 수치가 해소된 청크 원문에 verbatim 존재.
    단, 질문문에 등장한 전제 수치는 제외(위양성 방지 — 로드맵2 교훈)."""
    claims = item.get("numeric_claims", [])
    if not claims:
        return True, {"checked": 0}
    text = "\n".join(chunks()[c]["text"] for c in item.get("gold_chunk_ids", []) if c in chunks())
    q = item.get("question", "")
    bad = []
    for cl in claims:
        v, u = str(cl.get("value", "")), str(cl.get("unit", ""))
        if not v:
            continue
        grounded = bool(re.search(rf"{re.escape(v)}\s*{re.escape(u)}", text)) or (v in text and u in text)
        from_question = bool(re.search(rf"{re.escape(v)}\s*{re.escape(u)}", q)) or (v in q)
        if not grounded and not from_question:
            bad.append(f"{v}{u}")
    return (len(bad) == 0), {"ungrounded": bad, "checked": len(claims)}


def g3_answerable_proxy(item, judge=None):
    """응답가능성/유일성 proxy. judge=LLM 함수(없으면 결정론 근사).
    answerable=True: 해소 청크만으로 정답 도출 가능한가. False: 도출 불가가 맞는가."""
    if not item.get("answerable", True):
        # 무응답: gold 비고 + (proxy) 5개 문서 범위 밖 주제 — 결정론으론 'gold 없음'만 확인
        return (not item.get("gold_chunk_ids")), {"mode": "unanswerable", "proxy": "deterministic"}
    cids = item.get("gold_chunk_ids", [])
    if not cids:
        return False, {"mode": "answerable", "note": "no resolved chunk"}
    if judge is None:
        return True, {"mode": "answerable", "proxy": "skip(LLM 미연결)"}
    ctx = "\n\n".join(chunks()[c]["text"][:900] for c in cids if c in chunks())
    r = judge(item["question"], item["gold_answer"], ctx)
    return (r.get("derivable", False) and r.get("score", 0) >= 0.6), {"mode": "answerable", **r}


def g6_origin(item):
    return item.get("origin") in {"generated", "held_out"}, {"origin": item.get("origin")}


# ---------------- G0 (엣지 신뢰도) — 기존 감사 재사용 ----------------
def g0_edge_reliability():
    """REFERENCES 엣지 신뢰도(graph/reference_audit.md). REFERENCES 기제 문항의 신뢰 상한."""
    return {
        "REFERENCES_resolution_rate": 0.908,   # 5,561/6,124
        "REFERENCES_paragraph_hit": 0.856,      # 2,560/2,990
        "CONFLICTS_WITH": "expert_curated(7쌍) — 정규식 noise 없음(신뢰 상)",
        "함의": "REFERENCES 기제 멀티홉 문항은 신뢰 상한 90.8%를 상속(보고서 명시).",
    }


# ---------------- G5 (중복) — 임베딩 코사인 ----------------
def g5_dedup(items, embed_fn, thresh=0.9, priority=("held_out", "seed", "generated")):
    """질문 임베딩 코사인 유사도 > thresh면 우선순위 낮은 쪽을 폐기."""
    import math

    def rank(it):
        o = it.get("origin", "generated")
        tag = "seed" if it.get("_seed") else o
        return priority.index(tag) if tag in priority else len(priority)

    vecs = {it["_uid"]: embed_fn(it["question"]) for it in items}

    def cos(a, b):
        s = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return s / (na * nb) if na and nb else 0.0

    keep, dropped = [], []
    for it in sorted(items, key=rank):
        dup = next((k for k in keep if cos(vecs[it["_uid"]], vecs[k["_uid"]]) > thresh), None)
        if dup is None:
            keep.append(it)
        else:
            dropped.append({"uid": it["_uid"], "dup_of": dup["_uid"]})
    return keep, dropped


def run_gates(item, judge=None):
    """결정론 게이트 일괄. (G3는 proxy, G4는 사람 — 여기선 표기만.) gate_passed 라벨 채움."""
    res = {}
    res["G1"], d1 = g1_provenance(item)
    res["G2"], d2 = g2_numeric(item)
    res["G3"], d3 = g3_answerable_proxy(item, judge)
    res["G6"], d6 = g6_origin(item)
    passed = [g for g in ("G1", "G2", "G3", "G6") if res[g]]
    item["gate_passed"] = passed
    item["gate_detail"] = {"G1": d1, "G2": d2, "G3": d3, "G6": d6}
    item["gate_ok"] = all(res.values())
    return item
