"""Step 4 — 노드 구현 (Router / Retriever / Reasoner / Verifier / Output).

각 노드는 AgentState(dict)를 받아 부분 갱신 dict를 반환하고, trace에 단계를 누적한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "graph"))
import graph_common as gc  # noqa: E402

import prompts  # noqa: E402
from graph_state import add_trace  # noqa: E402
from retrieval import Retriever  # noqa: E402

ROUTER_MODEL = "gpt-4o-mini"
REASONER_MODEL = "gpt-4o"
VERIFIER_MODEL = "gpt-4o-mini"
MAX_RETRY = 2

# route별 검색 전략(가이드 §5.2 + 프로젝트 최적화)
STRATEGY = {
    "simple":   {"seed_top_k": 4, "max_hops": 1, "edge_priority": ["concept"],
                 "max_context_chunks": 6},
    "multihop": {"seed_top_k": 5, "max_hops": 2, "edge_priority": ["references", "concept"],
                 "max_context_chunks": 12},
    "conflict": {"seed_top_k": 5, "max_hops": 2, "edge_priority": ["conflict", "concept", "references"],
                 "max_context_chunks": 12},
}

_cli = None
_retr = None


def _client():
    global _cli
    if _cli is None:
        _cli = gc.get_openai()
    return _cli


def get_retriever() -> Retriever:
    global _retr
    if _retr is None:
        _retr = Retriever()
    return _retr


def _chat_json(model: str, sys_p: str, user_p: str) -> dict:
    r = _client().chat.completions.create(
        model=model, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
    )
    return json.loads(r.choices[0].message.content)


# ---------------- Router ----------------
def router(state):
    j = _chat_json(ROUTER_MODEL, prompts.ROUTER_SYS, state["query"])
    route = j.get("route", "multihop")
    if route not in STRATEGY:
        route = "multihop"
    params = dict(STRATEGY[route])
    return {"route": route, "params": params,
            "trace": add_trace(state, "router", route=route, reason=j.get("reason", ""))}


# ---------------- Retriever ----------------
def retriever(state):
    params = dict(state["params"])
    # 회귀 시 탐색 '폭(k)'만 확대 — route 의미(edge_priority)는 바꾸지 않는다
    # (simple distractor가 재시도로 충돌 탐색으로 변질되어 과잉탐지하는 것을 방지)
    rc = state.get("retry_count", 0)
    if rc:
        params["seed_top_k"] = params.get("seed_top_k", 5) + 3
        params["max_context_chunks"] = params.get("max_context_chunks", 12) + 4
    out = get_retriever().retrieve(state["query"], params)
    return {
        "seeds": out["seeds"], "context": out["context"], "conflicts": out["conflicts"],
        "expanded_via": out["expanded_via"], "paths": out.get("paths", []),
        "trace": add_trace(state, "retriever",
                           seeds=[s["id"] for s in out["seeds"]],
                           context_ids=[c["id"] for c in out["context"]],
                           expanded_via=out["expanded_via"],
                           has_conflict_edge=out["has_conflict_edge"],
                           paths=out.get("paths", [])),
    }


# ---------------- Reasoner ----------------
def _fmt_context(ctx):
    lines = []
    for c in ctx:
        head = c.get("ctx") or f"{c['doc']} {c.get('article','')}"
        lines.append(f"[{c['id']}] ({head})\n{c['text'][:900]}")
    return "\n\n".join(lines)


def reasoner(state):
    ctx = state.get("context", [])
    has_conf = len(state.get("conflicts", [])) > 0
    conf_hint = ""
    if has_conf:
        axes = sorted({c.get("axis", "") for c in state["conflicts"] if c.get("axis")})
        conf_hint = "\n[충돌 엣지 감지된 축] " + "; ".join(axes)
    user = (f"질문: {state['query']}\n\nconflict_edge_present={str(has_conf).lower()}{conf_hint}\n\n"
            f"[컨텍스트]\n{_fmt_context(ctx)}")
    j = _chat_json(REASONER_MODEL, prompts.REASONER_SYS, user)
    flagged = bool(j.get("conflict_flagged", False))
    desc = j.get("conflict_desc", "")
    # 결정론적 게이팅: CONFLICTS_WITH 엣지가 없으면 충돌 단정 금지(distractor 과잉탐지 방지)
    if not has_conf:
        flagged, desc = False, ""
    return {
        "answer": j.get("answer", ""), "citations": j.get("citations", []),
        "conflict_flagged": flagged, "conflict_desc": desc,
        "trace": add_trace(state, "reasoner",
                           n_citations=len(j.get("citations", [])),
                           conflict_flagged=flagged),
    }


# ---------------- Verifier ----------------
def _article_key(cid: str) -> str:
    """chunk_id를 조문 단위로 정규화('근기령_제7조의2_제3항' → '근기령_제7조의2').
    Reasoner가 항 번호를 다르게 적어도 같은 조문이면 유효로 본다(#10 매핑 함정 완화)."""
    if not cid:
        return ""
    parts = cid.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else cid


def verifier(state):
    has_conf = len(state.get("conflicts", [])) > 0
    ctx_ids = {c["id"] for c in state.get("context", [])}
    ctx_by_art: dict[str, list[str]] = {}
    for i in ctx_ids:
        ctx_by_art.setdefault(_article_key(i), []).append(i)

    # 인용 보정(#10 함정): 항 표기가 달라도 같은 조문이면 실제 컨텍스트 chunk_id로 치환
    repaired, bad_cites = [], []
    for cit in state.get("citations", []):
        cid = cit.get("chunk_id")
        if cid in ctx_ids:
            repaired.append(cit)
        elif _article_key(cid) in ctx_by_art:
            repaired.append({**cit, "chunk_id": ctx_by_art[_article_key(cid)][0]})
        else:
            repaired.append(cit)
            bad_cites.append(cid)

    user = (f"질문: {state['query']}\nconflict_edge_present={str(has_conf).lower()}\n"
            f"컨텍스트 chunk_id 목록: {sorted(ctx_ids)}\n"
            f"답변: {state.get('answer','')}\n"
            f"인용(보정됨): {json.dumps(repaired, ensure_ascii=False)}\n"
            f"conflict_flagged: {state.get('conflict_flagged')}\n"
            "주의: 인용 chunk_id가 위 목록에 있으면 '근거 존재'는 통과로 본다.")
    j = _chat_json(VERIFIER_MODEL, prompts.VERIFIER_SYS, user)
    verdict = j.get("verdict", "ACCEPT")
    reasons = j.get("reasons", [])
    # 결정론적 가드 1: 조문 단위로도 컨텍스트에 없는 인용은 환각 → REJECT
    if bad_cites:
        verdict = "REJECT"
        reasons = list(reasons) + [f"컨텍스트에 없는 인용(조문 단위): {bad_cites}"]
    # 결정론적 가드 2: 충돌 엣지가 있는데 답변이 충돌을 다루지 않음 → REJECT
    if has_conf and not state.get("conflict_flagged"):
        verdict = "REJECT"
        reasons = list(reasons) + ["충돌 엣지 존재하나 답변이 충돌/차이 미서술"]
    return {
        "citations": repaired,
        "verdict": verdict, "reject_reasons": reasons,
        "retry_count": state.get("retry_count", 0) + (1 if verdict == "REJECT" else 0),
        "trace": add_trace(state, "verifier", verdict=verdict, reasons=reasons),
    }


def route_after_verify(state):
    if state.get("verdict") == "REJECT" and state.get("retry_count", 0) <= MAX_RETRY:
        return "retry"
    return "accept"


# ---------------- Output ----------------
def output(state):
    return {"trace": add_trace(state, "output", verdict=state.get("verdict"))}
