"""Step 3 (1/3) — 청크 임베딩 생성 + 디스크 캐시 (DB 독립).

각 Chunk의 '컨텍스트 머리말 + 본문'을 text-embedding-3-small로 임베딩하여
data/embeddings/chunk_vectors.npy (float32, N x 1536) + chunk_ids.json + meta.json 저장.
이미 캐시가 있고 청크 수가 같으면 건너뛴다(재실행 안전).
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

import graph_common as gc

BATCH = 128


def main():
    chunks = gc.load_chunks()
    ids = [c["chunk_id"] for c in chunks]
    gc.EMB_DIR.mkdir(parents=True, exist_ok=True)
    vec_path = gc.EMB_DIR / "chunk_vectors.npy"
    ids_path = gc.EMB_DIR / "chunk_ids.json"

    if vec_path.exists() and ids_path.exists():
        old_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        if old_ids == ids:
            print(f"[SKIP] 캐시 최신({len(ids)}개). 재생성 불필요.")
            return

    client = gc.get_openai()
    texts = [gc.embed_text(c) for c in chunks]
    vectors = np.zeros((len(texts), gc.EMBED_DIM), dtype=np.float32)
    t0 = time.time()
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        resp = client.embeddings.create(model=gc.EMBED_MODEL, input=batch)
        for j, d in enumerate(resp.data):
            vectors[i + j] = np.asarray(d.embedding, dtype=np.float32)
        print(f"  embedded {min(i + BATCH, len(texts))}/{len(texts)}  ({time.time()-t0:.0f}s)")
        sys.stdout.flush()

    np.save(vec_path, vectors)
    ids_path.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
    (gc.EMB_DIR / "meta.json").write_text(json.dumps({
        "model": gc.EMBED_MODEL, "dim": gc.EMBED_DIM, "count": len(ids),
        "input": "context_header + text"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] {len(ids)}개 임베딩 -> {vec_path}  (dim={gc.EMBED_DIM})")


if __name__ == "__main__":
    main()
