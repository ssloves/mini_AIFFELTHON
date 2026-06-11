"""Step 3 (2/3) — Neo4j 그래프 적재 (노드 + 엣지). 멱등(MERGE) 구축.

사전: Neo4j 실행 + .env에 NEO4J_URI/USER/PASSWORD.
사용: python build_graph.py [--reset]
오프라인 빌드(build_offline_graph.py)와 동일한 스키마를 그대로 Cypher로 적재한다.
"""
from __future__ import annotations

import sys

import graph_common as gc

PARENT_IN_CORPUS = {"조특법시행령": "조특법"}
DOC_TYPE = {"고용보험법시행령": "시행령", "근로기준법시행령": "시행령",
            "조특법시행령": "시행령", "조특법": "법률", "장려금지침": "행정지침"}
BATCH = 500


def chunks_in_batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def run_unwind(session, query, rows):
    for b in chunks_in_batches(rows):
        session.run(query, rows=b)


def create_constraints(s):
    for label, key in [("Document", "key"), ("Article", "id"), ("Chunk", "chunk_id"),
                       ("Concept", "name"), ("Condition", "condition_id")]:
        s.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")


def load_nodes(s, chunks, concepts, conditions):
    # Document
    docs = [{"key": d, "doc_type": t} for d, t in DOC_TYPE.items()]
    run_unwind(s, "UNWIND $rows AS r MERGE (d:Document {key:r.key}) SET d.doc_type=r.doc_type", docs)

    # Article
    seen, arts = set(), []
    for c in chunks:
        if c.get("article"):
            aid = f"{c['source_doc']}|{c['article']}"
            if aid not in seen:
                seen.add(aid)
                arts.append({"id": aid, "doc": c["source_doc"], "number": c["article"],
                             "title": c.get("article_title"), "chapter": c.get("chapter")})
    run_unwind(s, """UNWIND $rows AS r MERGE (a:Article {id:r.id})
        SET a.doc=r.doc, a.number=r.number, a.title=r.title, a.chapter=r.chapter""", arts)

    # Chunk
    crows = []
    for c in chunks:
        ext = [r.get("target_law") for r in c.get("references", []) if r["status"] == "external"]
        crows.append({"chunk_id": c["chunk_id"], "doc": c["source_doc"], "text": c["text"],
                      "page": c.get("page"), "token_count": c["token_count"],
                      "paragraph": c.get("paragraph"), "article": c.get("article"),
                      "section": c.get("section"), "hierarchy_path": c.get("hierarchy_path"),
                      "context_header": c.get("context_header"), "external_refs": ext})
    run_unwind(s, """UNWIND $rows AS r MERGE (c:Chunk {chunk_id:r.chunk_id})
        SET c.doc=r.doc, c.text=r.text, c.page=r.page, c.token_count=r.token_count,
            c.paragraph=r.paragraph, c.article=r.article, c.section=r.section,
            c.hierarchy_path=r.hierarchy_path, c.context_header=r.context_header,
            c.external_refs=r.external_refs""", crows)

    # Concept
    krows = [{"name": n, "defined_in": [d for d, v in by.items() if v.get("defines")]}
             for n, by in concepts.items()]
    run_unwind(s, "UNWIND $rows AS r MERGE (k:Concept {name:r.name}) SET k.defined_in=r.defined_in", krows)

    # Condition
    cdrows = [{"condition_id": cd["condition_id"], "type": cd["type"],
               "description": cd.get("description"), "applies_to": cd.get("applies_to"),
               "page": cd.get("page"), "numeric_criteria": cd.get("numeric_criteria", []),
               "refs": cd.get("refs", [])} for cd in conditions]
    run_unwind(s, """UNWIND $rows AS r MERGE (n:Condition {condition_id:r.condition_id})
        SET n.type=r.type, n.description=r.description, n.applies_to=r.applies_to,
            n.page=r.page, n.numeric_criteria=r.numeric_criteria, n.refs=r.refs""", cdrows)


def load_edges(s, chunks, conflicts, conditions):
    # CONTAINS: Doc->Article
    art_rows = [{"doc": p["doc"], "id": p["id"]} for p in
                [{"doc": c["source_doc"], "id": f"{c['source_doc']}|{c['article']}"}
                 for c in chunks if c.get("article")]]
    art_rows = list({r["id"]: r for r in art_rows}.values())
    run_unwind(s, """UNWIND $rows AS r MATCH (d:Document {key:r.doc}),(a:Article {id:r.id})
        MERGE (d)-[:CONTAINS]->(a)""", art_rows)
    # CONTAINS: Article->Chunk / Doc->Chunk(지침)
    ac = [{"chunk_id": c["chunk_id"], "doc": c["source_doc"],
           "aid": f"{c['source_doc']}|{c['article']}" if c.get("article") else None} for c in chunks]
    run_unwind(s, """UNWIND $rows AS r WITH r WHERE r.aid IS NOT NULL
        MATCH (a:Article {id:r.aid}),(c:Chunk {chunk_id:r.chunk_id}) MERGE (a)-[:CONTAINS]->(c)""", ac)
    run_unwind(s, """UNWIND $rows AS r WITH r WHERE r.aid IS NULL
        MATCH (d:Document {key:r.doc}),(c:Chunk {chunk_id:r.chunk_id}) MERGE (d)-[:CONTAINS]->(c)""", ac)

    # NEXT_PARAGRAPH
    np_rows, prev = [], None
    for c in chunks:
        if prev and prev.get("article") and prev["source_doc"] == c["source_doc"] \
           and prev.get("article") == c.get("article"):
            np_rows.append({"a": prev["chunk_id"], "b": c["chunk_id"]})
        prev = c
    run_unwind(s, """UNWIND $rows AS r MATCH (a:Chunk {chunk_id:r.a}),(b:Chunk {chunk_id:r.b})
        MERGE (a)-[:NEXT_PARAGRAPH]->(b)""", np_rows)

    # REFERENCES: Chunk->Article (도착 Article 없으면 자동 미생성)
    ref_rows = []
    for c in chunks:
        seen = set()
        for r in c.get("references", []):
            if r["status"] not in ("internal", "cross_corpus"):
                continue
            tgt = f"{r['target_doc']}|{r['article']}"
            if tgt in seen:
                continue
            seen.add(tgt)
            ref_rows.append({"src": c["chunk_id"], "tgt": tgt, "status": r["status"]})
    run_unwind(s, """UNWIND $rows AS r MATCH (c:Chunk {chunk_id:r.src}),(a:Article {id:r.tgt})
        MERGE (c)-[e:REFERENCES]->(a) SET e.status=r.status""", ref_rows)

    # DEFINES / MENTIONS
    defr = [{"c": c["chunk_id"], "k": k} for c in chunks for k in c.get("defines", [])]
    menr = [{"c": c["chunk_id"], "k": k} for c in chunks for k in c.get("keywords", [])]
    run_unwind(s, """UNWIND $rows AS r MATCH (c:Chunk {chunk_id:r.c}),(k:Concept {name:r.k})
        MERGE (c)-[:DEFINES]->(k)""", defr)
    run_unwind(s, """UNWIND $rows AS r MATCH (c:Chunk {chunk_id:r.c}),(k:Concept {name:r.k})
        MERGE (c)-[:MENTIONS]->(k)""", menr)

    # CONFLICTS_WITH
    cf = [{"a": r["chunk_a"], "b": r["chunk_b"], "concept": r["concept"],
           "axis": r.get("axis") or r.get("shared_metric", ""),
           "note": r.get("note", ""), "verified": r.get("verified", True)} for r in conflicts]
    run_unwind(s, """UNWIND $rows AS r MATCH (a:Chunk {chunk_id:r.a}),(b:Chunk {chunk_id:r.b})
        MERGE (a)-[e:CONFLICTS_WITH]->(b)
        SET e.concept=r.concept, e.axis=r.axis, e.note=r.note, e.verified=r.verified""", cf)

    # HAS_CONDITION: Doc->Condition, Chunk->Condition
    hc_doc = [{"doc": cd["source_doc"], "cid": cd["condition_id"]} for cd in conditions]
    hc_ch = [{"src": cd["source_chunk_id"], "cid": cd["condition_id"]} for cd in conditions
             if cd.get("source_chunk_id")]
    run_unwind(s, """UNWIND $rows AS r MATCH (d:Document {key:r.doc}),(n:Condition {condition_id:r.cid})
        MERGE (d)-[:HAS_CONDITION]->(n)""", hc_doc)
    run_unwind(s, """UNWIND $rows AS r MATCH (c:Chunk {chunk_id:r.src}),(n:Condition {condition_id:r.cid})
        MERGE (c)-[:HAS_CONDITION]->(n)""", hc_ch)

    # DELEGATES (법률 -> 시행령): 위임관계(구체화). '우선적용'이 아님에 주의.
    ov = [{"p": p, "c": c} for c, p in PARENT_IN_CORPUS.items()]
    run_unwind(s, """UNWIND $rows AS r MATCH (p:Document {key:r.p}),(c:Document {key:r.c})
        MERGE (p)-[:DELEGATES]->(c)""", ov)


def verify(s):
    print("\n[검증] 노드/엣지 수")
    for label in ["Document", "Article", "Chunk", "Concept", "Condition"]:
        n = s.run(f"MATCH (n:{label}) RETURN count(n) AS n").single()["n"]
        print(f"  {label}: {n:,}")
    for et in ["CONTAINS", "NEXT_PARAGRAPH", "REFERENCES", "DEFINES", "MENTIONS",
               "CONFLICTS_WITH", "HAS_CONDITION", "DELEGATES"]:
        n = s.run(f"MATCH ()-[r:{et}]->() RETURN count(r) AS n").single()["n"]
        print(f"  {et}: {n:,}")


def main():
    reset = "--reset" in sys.argv
    try:
        driver = gc.get_driver()
        driver.verify_connectivity()
    except Exception as e:
        print(f"[ERROR] Neo4j 접속 실패: {str(e)[:120]}")
        print("Neo4j 실행 후 .env에 NEO4J_URI/USER/PASSWORD 설정 필요. (graph/docker-compose.yml 참고)")
        sys.exit(1)

    chunks = gc.load_chunks()
    conflicts = gc.load_conflicts()
    conditions = gc.load_conditions()
    concepts = gc.load_concepts()

    with driver.session() as s:
        if reset:
            print("[RESET] 기존 그래프 삭제")
            s.run("MATCH (n) DETACH DELETE n")
        create_constraints(s)
        print("노드 적재 중...")
        load_nodes(s, chunks, concepts, conditions)
        print("엣지 적재 중...")
        load_edges(s, chunks, conflicts, conditions)
        verify(s)
    driver.close()
    print("\n[DONE] 그래프 적재 완료. 다음: python populate_embeddings.py")


if __name__ == "__main__":
    main()
