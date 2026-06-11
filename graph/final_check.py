"""Step 3 최종 점검 — Neo4j 실제 상태를 산출물과 대조 검증.

검사 항목:
 1) 접속
 2) 노드 수(라벨별) vs 산출물 기대치
 3) 엣지 수(타입별) vs 오프라인 그래프
 4) 임베딩 적재율(Chunk.embedding) + 차원
 5) 벡터 인덱스 존재 + ONLINE
 6) 제약(고유 키) 존재
 7) 고립 Chunk(엣지 0) 수
 8) CONFLICTS_WITH 속성 충실성(axis/verified) + 양끝 노드 존재
 9) 벡터 검색 스모크 테스트

PASS/FAIL 요약을 출력한다.
"""
from __future__ import annotations

import json
import sys

import graph_common as gc

sys.stdout.reconfigure(encoding="utf-8")

EXPECT_NODES = {"Document": 5, "Article": 1210, "Chunk": 3902, "Concept": 20, "Condition": 158}
EXPECT_EDGES = {"CONTAINS": 5112, "NEXT_PARAGRAPH": 2094, "REFERENCES": 5561,
                "DEFINES": 119, "MENTIONS": 1599, "CONFLICTS_WITH": 7,
                "HAS_CONDITION": 316, "DELEGATES": 1}

results = []  # (name, ok, detail)


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}: {detail}")


def main():
    drv = gc.get_driver()
    with drv.session() as s:
        # 1) 접속
        s.run("RETURN 1").single()
        check("1. Neo4j 접속", True, gc.neo4j_config()["uri"])

        # 2) 노드
        for label, exp in EXPECT_NODES.items():
            n = s.run(f"MATCH (x:{label}) RETURN count(x) AS n").single()["n"]
            check(f"2. 노드 {label}", n == exp, f"{n:,} (기대 {exp:,})")

        # 3) 엣지
        for et, exp in EXPECT_EDGES.items():
            n = s.run(f"MATCH ()-[r:{et}]->() RETURN count(r) AS n").single()["n"]
            check(f"3. 엣지 {et}", n == exp, f"{n:,} (기대 {exp:,})")

        # 4) 임베딩 적재율 + 차원
        emb = s.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
                    "RETURN count(c) AS n").single()["n"]
        total = s.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]
        dim = s.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
                    "RETURN size(c.embedding) AS d LIMIT 1").single()
        dim = dim["d"] if dim else None
        check("4. 임베딩 적재율", emb == total, f"{emb:,}/{total:,}")
        check("4. 임베딩 차원", dim == gc.EMBED_DIM, f"{dim} (기대 {gc.EMBED_DIM})")

        # 5) 벡터 인덱스
        idx = s.run("SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties "
                    "WHERE type='VECTOR' RETURN name, state, labelsOrTypes, properties").data()
        online = bool(idx) and all(i["state"] == "ONLINE" for i in idx)
        check("5. 벡터 인덱스 ONLINE", online, json.dumps(idx, ensure_ascii=False))

        # 6) 제약
        cons = s.run("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties "
                     "RETURN labelsOrTypes, properties").data()
        check("6. 고유 제약 존재", len(cons) > 0, f"{len(cons)}개: {cons}")

        # 7) 고립 Chunk
        iso = s.run("MATCH (c:Chunk) WHERE NOT (c)--() RETURN count(c) AS n").single()["n"]
        check("7. 고립 Chunk 0", iso == 0, f"{iso}개")

        # 8) CONFLICTS_WITH 속성 + 양끝 존재
        cf = s.run("""MATCH (a:Chunk)-[r:CONFLICTS_WITH]->(b:Chunk)
            RETURN a.chunk_id AS a, b.chunk_id AS b, r.axis AS axis,
                   r.verified AS verified, r.concept AS concept""").data()
        with_axis = sum(1 for r in cf if r["axis"])
        check("8. CONFLICTS_WITH axis 충실", with_axis == len(cf), f"{with_axis}/{len(cf)} 보유")
        verified_cnt = sum(1 for r in cf if r["verified"])
        check("8. CONFLICTS_WITH verified 표기", len(cf) > 0,
              f"verified True {verified_cnt} / 전체 {len(cf)}")

        # 9) 벡터 검색 스모크
        try:
            cli = gc.get_openai()
            q = "상시근로자 수를 어떻게 산정하나"
            v = cli.embeddings.create(model=gc.EMBED_MODEL, input=q).data[0].embedding
            hit = s.run("""CALL db.index.vector.queryNodes('chunk_embedding_index', 3, $v)
                YIELD node, score RETURN node.chunk_id AS id, score""", v=v).data()
            check("9. 벡터 검색 스모크", len(hit) == 3,
                  f"top: {hit[0]['id']} ({hit[0]['score']:.3f})" if hit else "no hit")
        except Exception as e:  # noqa
            check("9. 벡터 검색 스모크", False, f"오류: {e}")

    drv.close()

    # 산출물 대조(디스크)
    n_conf = len(gc.load_conflicts())
    check("10. conflicts_confirmed.jsonl", n_conf == 7, f"{n_conf}건")

    print("\n" + "=" * 60)
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = len(results) - n_pass
    print(f"종합: {n_pass} PASS / {n_fail} FAIL (총 {len(results)})")
    if n_fail:
        print("FAIL 항목:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
    else:
        print("✅ Step 3 전 항목 통과 — Step 4 진입 가능")


if __name__ == "__main__":
    main()
