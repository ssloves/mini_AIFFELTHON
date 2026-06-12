"""Step 4 — 에이전트 상태 스키마.

LangGraph가 노드 간에 넘기는 단일 상태(dict). 관찰가능성을 위해 모든 노드가
`trace`에 자신의 입·출력 요약을 누적한다(상태 추적). 기본 병합은 '치환'이므로
각 노드는 기존 trace를 읽어 append한 새 리스트를 반환한다.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class RouteParams(TypedDict, total=False):
    route: str           # simple | multihop | conflict
    seed_top_k: int
    max_hops: int
    edge_priority: list[str]


class AgentState(TypedDict, total=False):
    # 입력
    query: str
    qa_id: Optional[str]

    # Router 산출
    route: str
    params: RouteParams

    # Retriever 산출
    seeds: list[dict]              # 벡터 시드 [{id, doc, article, score, text}]
    context: list[dict]           # 최종 컨텍스트 청크(재랭킹 후)
    conflicts: list[dict]         # 수집된 CONFLICTS_WITH [{a,b,axis,note,verified}]
    expanded_via: dict            # {edge_type: count}
    paths: list[dict]             # node→edge→node 경로 트리플(관찰가능성, 점수 불변)

    # Reasoner 산출
    answer: str
    citations: list[dict]         # [{chunk_id, quote}]
    conflict_flagged: bool
    conflict_desc: str

    # Verifier 산출
    verdict: str                  # ACCEPT | REJECT
    reject_reasons: list[str]
    retry_count: int

    # 관찰
    trace: list[dict[str, Any]]


def add_trace(state: AgentState, node: str, **info) -> list[dict]:
    """기존 trace에 한 단계를 append한 새 리스트 반환."""
    tr = list(state.get("trace", []))
    tr.append({"node": node, **info})
    return tr
