"""Step 4 — 하이브리드 검색기 (GraphRAG 핵심).

벡터 시드 → 그래프 확장(개념 브리지 / CONFLICTS_WITH / REFERENCES) → 재랭킹.

프로젝트 최적화 포인트(가이드 기본 설계에 추가):
 - **개념 브리지**: 고보·근기·지침↔조특은 직접 참조가 0건(EDA)이라, 문서 간 확장은
   Concept 노드(상대편은 DEFINES 청크=권위 정의)를 경유한다.
 - **DEFINES 부스트 재랭킹**: 벡터 top-1이 부수 언급 조문일 때(예: 조특령 §136의2),
   '정의/산정' 청크를 끌어올려 정답 조문이 컨텍스트 상단에 오게 한다.
 - **충돌 우선(edge_priority)**: route=conflict면 CONFLICTS_WITH를 먼저·강하게 확장.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "graph"))
sys.path.insert(0, str(ROOT / "notebooks"))
import graph_common as gc  # noqa: E402

try:
    from legal_common import CONCEPTS
except Exception:  # noqa
    CONCEPTS = {}

INDEX = "chunk_embedding_index"


# 질문(구어) 대응 별칭 보강 — 그래프 빌드용 legal_common.CONCEPTS는 건드리지 않음
EXTRA_ALIASES = {
    "단시간근로자": ["단시간", "파트타임", "시간제"],
    "상시근로자": ["상시 근로", "상시근로", "상시 사용", "상시 4", "상시 5", "상시 인원", "상시 직원"],
    "기간제근로자": ["기간제", "계약직", "계약 1년", "1년 미만"],
    "피보험자": ["피보험"],
    "출산전후휴가": ["출산", "육아"],
    "가동일수": ["가동", "연인원"],
}


def detect_concepts(query: str) -> list[str]:
    """질문 텍스트에서 등장하는 개념(정식명)을 별칭 매칭으로 감지(구어 별칭 보강)."""
    found = []
    for name, aliases in CONCEPTS.items():
        al = list(aliases) + EXTRA_ALIASES.get(name, [])
        if any(a in query for a in al):
            found.append(name)
    return found


class Retriever:
    def __init__(self):
        self.drv = gc.get_driver()
        self.cli = gc.get_openai()

    def close(self):
        self.drv.close()

    def embed(self, text: str) -> list[float]:
        return self.cli.embeddings.create(model=gc.EMBED_MODEL, input=text).data[0].embedding

    def vector_search(self, vec: list[float], k: int) -> list[dict]:
        q = """CALL db.index.vector.queryNodes($idx, $k, $v) YIELD node, score
               RETURN node.chunk_id AS id, node.doc AS doc, node.article AS article,
                      node.context_header AS ctx, node.text AS text, score"""
        with self.drv.session() as s:
            return [dict(r) for r in s.run(q, idx=INDEX, k=k, v=vec)]

    def _conflicts(self, ids: list[str]) -> list[dict]:
        q = """MATCH (c:Chunk)-[r:CONFLICTS_WITH]-(o:Chunk)
               WHERE c.chunk_id IN $ids
               RETURN c.chunk_id AS src, o.chunk_id AS id, o.doc AS doc, o.article AS article,
                      o.context_header AS ctx, o.text AS text,
                      r.axis AS axis, r.note AS note, r.verified AS verified"""
        with self.drv.session() as s:
            return [dict(r) for r in s.run(q, ids=ids)]

    def _conflict_by_concept(self, concepts: list[str]) -> list[dict]:
        """질문 개념과 일치하는 CONFLICTS_WITH 엣지의 양끝 청크쌍을 직접 가져온다.
        (벡터가 못 잡아도 정준 충돌 조문을 보장 — KG 강점 직접 활용)"""
        q = """MATCH (a:Chunk)-[r:CONFLICTS_WITH]->(b:Chunk)
               WHERE any(d IN $cs WHERE r.concept CONTAINS d OR d CONTAINS r.concept
                                       OR r.axis CONTAINS d)
               RETURN a.chunk_id AS a_id, a.doc AS a_doc, a.article AS a_art,
                      a.context_header AS a_ctx, a.text AS a_text,
                      b.chunk_id AS b_id, b.doc AS b_doc, b.article AS b_art,
                      b.context_header AS b_ctx, b.text AS b_text,
                      r.axis AS axis, r.note AS note, r.verified AS verified"""
        with self.drv.session() as s:
            return [dict(r) for r in s.run(q, cs=concepts)]

    def _concept_bridge(self, ids: list[str], cap: int) -> list[dict]:
        # 상대편은 DEFINES 청크(권위 정의)만 → 노이즈 억제, 문서 간 다리
        q = """MATCH (c:Chunk)-[:DEFINES|MENTIONS]->(k:Concept)<-[:DEFINES]-(o:Chunk)
               WHERE c.chunk_id IN $ids AND o.doc <> c.doc
               RETURN DISTINCT o.chunk_id AS id, o.doc AS doc, o.article AS article,
                      o.context_header AS ctx, o.text AS text, k.name AS concept
               LIMIT $cap"""
        with self.drv.session() as s:
            return [dict(r) for r in s.run(q, ids=ids, cap=cap)]

    def _references(self, ids: list[str], cap: int) -> list[dict]:
        q = """MATCH (c:Chunk)-[:REFERENCES]->(a:Article)-[:CONTAINS]->(o:Chunk)
               WHERE c.chunk_id IN $ids
               RETURN DISTINCT o.chunk_id AS id, o.doc AS doc, o.article AS article,
                      o.context_header AS ctx, o.text AS text
               LIMIT $cap"""
        with self.drv.session() as s:
            return [dict(r) for r in s.run(q, ids=ids, cap=cap)]

    def retrieve(self, query: str, params: dict) -> dict:
        """params: route, seed_top_k, max_hops, edge_priority, max_context_chunks."""
        k = params.get("seed_top_k", 5)
        edge_priority = params.get("edge_priority", ["concept", "references"])
        max_ctx = params.get("max_context_chunks", 12)
        ref_cap = 12
        bridge_cap = 6

        vec = self.embed(query)
        seeds = self.vector_search(vec, k)
        seed_ids = [s["id"] for s in seeds]
        concepts = detect_concepts(query)

        scored: dict[str, dict] = {}
        for s in seeds:
            scored[s["id"]] = {**s, "score": float(s["score"]), "via": "seed"}

        expanded_via: dict[str, int] = {}
        conflicts: list[dict] = []

        # 0) 개념 구동 충돌 앵커 — 질문 개념과 일치하는 정준 CONFLICTS_WITH 쌍을 직접 주입
        #    (벡터 시드가 부수 조문만 잡는 문제를 KG 구조로 보정)
        #    단순조회(simple, edge_priority=['concept'])에는 발동 안 함 → distractor 과잉탐지 방지
        anchor_on = ("conflict" in edge_priority) or ("references" in edge_priority)
        if concepts and anchor_on:
            anchored = 0
            for r in self._conflict_by_concept(concepts):
                for side in ("a", "b"):
                    cid = r[f"{side}_id"]
                    # 검증된 정준 충돌쌍 → 최상위 우선순위(브리지·참조보다 위)
                    scored[cid] = {"id": cid, "doc": r[f"{side}_doc"], "article": r[f"{side}_art"],
                                   "ctx": r[f"{side}_ctx"], "text": r[f"{side}_text"],
                                   "score": 0.95, "via": "concept-conflict"}
                conflicts.append({"src": r["a_id"], "id": r["b_id"], "axis": r["axis"],
                                  "note": r["note"], "verified": r["verified"]})
                anchored += 1
            expanded_via["concept_conflict_anchor"] = anchored

        # 1) CONFLICTS_WITH (시드에서 직접 연결된 충돌)
        if "conflict" in edge_priority:
            cf = self._conflicts(seed_ids)
            for r in cf:
                conflicts.append({"src": r["src"], "id": r["id"], "axis": r["axis"],
                                  "note": r["note"], "verified": r["verified"]})
                cur = scored.get(r["id"])
                boost = 0.30
                if not cur or cur["score"] < 0.5 + boost:
                    scored[r["id"]] = {"id": r["id"], "doc": r["doc"], "article": r["article"],
                                       "ctx": r["ctx"], "text": r["text"],
                                       "score": 0.5 + boost, "via": "conflict"}
            expanded_via["CONFLICTS_WITH"] = len(cf)

        # 2) 개념 브리지(문서 간) — DEFINES 부스트
        if "concept" in edge_priority:
            br = self._concept_bridge(seed_ids, bridge_cap)
            for r in br:
                if r["id"] not in scored:
                    scored[r["id"]] = {"id": r["id"], "doc": r["doc"], "article": r["article"],
                                       "ctx": r["ctx"], "text": r["text"],
                                       "score": 0.58, "via": f"concept:{r['concept']}"}
            expanded_via["DEFINES(concept-bridge)"] = len(br)

        # 3) REFERENCES(인용 사슬)
        if "references" in edge_priority:
            rf = self._references(seed_ids, ref_cap)
            for r in rf:
                if r["id"] not in scored:
                    scored[r["id"]] = {"id": r["id"], "doc": r["doc"], "article": r["article"],
                                       "ctx": r["ctx"], "text": r["text"],
                                       "score": 0.55, "via": "reference"}
            expanded_via["REFERENCES"] = len(rf)

        ranked = sorted(scored.values(), key=lambda x: -x["score"])[:max_ctx]
        return {
            "seeds": seeds,
            "context": ranked,
            "conflicts": conflicts,
            "expanded_via": expanded_via,
            "has_conflict_edge": len(conflicts) > 0,
        }
