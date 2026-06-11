"""Step 4 — LangGraph 워크플로우 조립 + 실행 진입점.

흐름: Router → Retriever → Reasoner → Verifier →(ACCEPT) Output
                                   └────(REJECT, ≤N회) Retriever 회귀
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langgraph.graph import END, StateGraph

import nodes
from graph_state import AgentState


def build_app():
    g = StateGraph(AgentState)
    g.add_node("router", nodes.router)
    g.add_node("retriever", nodes.retriever)
    g.add_node("reasoner", nodes.reasoner)
    g.add_node("verifier", nodes.verifier)
    g.add_node("output", nodes.output)

    g.set_entry_point("router")
    g.add_edge("router", "retriever")
    g.add_edge("retriever", "reasoner")
    g.add_edge("reasoner", "verifier")
    g.add_conditional_edges("verifier", nodes.route_after_verify,
                            {"retry": "retriever", "accept": "output"})
    g.add_edge("output", END)
    return g.compile()


def run(query: str, qa_id: str | None = None) -> AgentState:
    app = build_app()
    init: AgentState = {"query": query, "qa_id": qa_id, "retry_count": 0, "trace": []}
    return app.invoke(init, config={"recursion_limit": 25})


def print_state(st: AgentState):
    print("=" * 70)
    print(f"Q: {st['query']}")
    print(f"route: {st.get('route')} | verdict: {st.get('verdict')} | retries: {st.get('retry_count')}")
    print(f"확장: {st.get('expanded_via')}")
    print("\n[상태 추적(trace)]")
    for i, t in enumerate(st.get("trace", []), 1):
        node = t.pop("node")
        print(f"  {i}. {node}: {json.dumps(t, ensure_ascii=False)[:300]}")
    print("\n[답변]")
    print(" ", st.get("answer", "")[:1200])
    if st.get("conflict_flagged"):
        print("\n[충돌]", st.get("conflict_desc", "")[:400])
    print("\n[근거 추적]")
    for c in st.get("citations", []):
        print(f"  - {c.get('chunk_id')}: {c.get('quote','')[:80]}")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else \
        "노무사가 근기법용으로 낸 '상시 4.8명'을 통합고용세액공제 신청서에 그대로 써도 되나요?"
    print_state(run(q))
