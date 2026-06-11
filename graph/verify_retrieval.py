"""Step 3 검증 — 하이브리드 검색(벡터 진입점 → 그래프 확장)이 동작하는지 확인.

벡터 인덱스로 질문과 가까운 청크를 찾고(1단계), 그 청크에서 REFERENCES/CONFLICTS_WITH
엣지를 1홉 확장(2단계)해 GraphRAG 검색 기반이 작동함을 보인다.
"""
from __future__ import annotations

import sys

import numpy as np

import graph_common as gc

QUERIES = [
    "단시간근로자를 상시근로자 수에 포함하는지",
    "청년의 연령 기준은 몇 세인가",
    "상시근로자 수는 어떻게 산정하나",
]
TOPK = 3


def embed_query(client, q):
    r = client.embeddings.create(model=gc.EMBED_MODEL, input=[q])
    return r.data[0].embedding


def main():
    client = gc.get_openai()
    driver = gc.get_driver()
    driver.verify_connectivity()
    with driver.session() as s:
        for q in QUERIES:
            print("\n" + "=" * 70)
            print("Q:", q)
            vec = embed_query(client, q)
            # 1단계: 벡터 검색
            rows = s.run("""
                CALL db.index.vector.queryNodes('chunk_embedding_index', $k, $v)
                YIELD node, score
                RETURN node.chunk_id AS id, node.doc AS doc, score,
                       left(node.text, 70) AS preview""", k=TOPK, v=vec).data()
            for r in rows:
                print(f"  [{r['score']:.3f}] {r['id']} ({r['doc']}) : {r['preview']}")
            # 2단계: 최상위 진입점에서 그래프 확장
            seed = rows[0]["id"]
            exp = s.run("""
                MATCH (c:Chunk {chunk_id:$id})
                OPTIONAL MATCH (c)-[:REFERENCES]->(a:Article)
                OPTIONAL MATCH (c)-[cf:CONFLICTS_WITH]-(o:Chunk)
                RETURN collect(DISTINCT a.id)[..5] AS refs,
                       collect(DISTINCT {with:o.chunk_id, concept:cf.concept})[..5] AS conflicts""",
                id=seed).single()
            print(f"  └ 확장(진입점 {seed}):")
            print(f"     REFERENCES → {exp['refs']}")
            print(f"     CONFLICTS_WITH → {[c for c in exp['conflicts'] if c.get('with')]}")
    driver.close()
    print("\n[OK] 하이브리드 검색 동작 확인")


if __name__ == "__main__":
    main()
