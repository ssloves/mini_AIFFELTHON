"""Step 3 (3/3) — 캐시된 임베딩을 Neo4j Chunk 노드에 적재 + 벡터 인덱스 생성.

사전: build_embeddings.py 로 data/embeddings/* 생성, build_graph.py 로 노드 적재 완료.
사용: python populate_embeddings.py
"""
from __future__ import annotations

import json
import sys

import numpy as np

import graph_common as gc

INDEX = "chunk_embedding_index"
BATCH = 500


def main():
    vec_path = gc.EMB_DIR / "chunk_vectors.npy"
    ids_path = gc.EMB_DIR / "chunk_ids.json"
    if not vec_path.exists():
        print("[ERROR] 임베딩 캐시 없음. 먼저 python build_embeddings.py 실행.")
        sys.exit(1)
    vectors = np.load(vec_path)
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    assert len(ids) == len(vectors)

    try:
        driver = gc.get_driver()
        driver.verify_connectivity()
    except Exception as e:
        print(f"[ERROR] Neo4j 접속 실패: {str(e)[:120]}")
        sys.exit(1)

    with driver.session() as s:
        # 벡터 인덱스 (코사인 유사도, dim=1536)
        s.run(f"""CREATE VECTOR INDEX {INDEX} IF NOT EXISTS
            FOR (c:Chunk) ON (c.embedding)
            OPTIONS {{indexConfig: {{`vector.dimensions`: {gc.EMBED_DIM},
                                    `vector.similarity_function`: 'cosine'}}}}""")
        # 임베딩 적재(배치)
        rows = [{"id": ids[i], "v": vectors[i].tolist()} for i in range(len(ids))]
        for i in range(0, len(rows), BATCH):
            b = rows[i:i + BATCH]
            s.run("""UNWIND $rows AS r MATCH (c:Chunk {chunk_id:r.id})
                CALL db.create.setNodeVectorProperty(c, 'embedding', r.v)""", rows=b)
            print(f"  적재 {min(i+BATCH, len(rows))}/{len(rows)}")
            sys.stdout.flush()
        cnt = s.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS n").single()["n"]
        print(f"[DONE] 임베딩 적재 {cnt:,}개, 벡터 인덱스 '{INDEX}' 생성 완료(dim={gc.EMBED_DIM}, cosine)")
    driver.close()


if __name__ == "__main__":
    main()
