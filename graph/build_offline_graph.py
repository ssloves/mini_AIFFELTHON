"""Step 3 (검증/폴백) — Neo4j 없이 동일 스키마의 그래프를 메모리에 구축.

목적:
  1) 그래프 모델 정합성 검증(노드/엣지 수, 끊긴 참조 점검)
  2) 멀티홉 탐색이 실제로 동작함을 예시로 증명
  3) Step 4가 Neo4j 없이도 진행 가능하도록 graph/offline_graph.pkl 로 직렬화
산출: graph/offline_graph.pkl, graph/graph_build_report.md
"""
from __future__ import annotations

import json
import pickle
from collections import defaultdict

import graph_common as gc

# 레지스트리(위계) — DELEGATES(위임) 규칙용
PARENT_IN_CORPUS = {"조특법시행령": "조특법"}
DOC_TYPE = {"고용보험법시행령": "시행령", "근로기준법시행령": "시행령",
            "조특법시행령": "시행령", "조특법": "법률", "장려금지침": "행정지침"}


class Graph:
    def __init__(self):
        self.nodes = defaultdict(dict)          # label -> {id: props}
        self.edges = defaultdict(list)          # type -> [(src, dst, props)]
        self.adj = defaultdict(lambda: defaultdict(list))  # node_id -> etype -> [dst]

    def add_node(self, label, nid, **props):
        self.nodes[label][nid] = props

    def add_edge(self, etype, src, dst, **props):
        self.edges[etype].append((src, dst, props))
        self.adj[src][etype].append(dst)
        # 무방향 탐색 편의를 위해 충돌은 양방향
        if etype == "CONFLICTS_WITH":
            self.adj[dst][etype].append(src)

    def n_nodes(self):
        return {k: len(v) for k, v in self.nodes.items()}

    def n_edges(self):
        return {k: len(v) for k, v in self.edges.items()}


def build():
    g = Graph()
    chunks = gc.load_chunks()
    conflicts = gc.load_conflicts()
    conditions = gc.load_conditions()
    concepts = gc.load_concepts()

    # --- 노드: Document ---
    for doc, dtype in DOC_TYPE.items():
        g.add_node("Document", doc, doc_type=dtype)

    # --- 노드: Article (청크에서 수집) + Chunk ---
    article_ids = set()
    for c in chunks:
        if c.get("article"):
            aid = f"{c['source_doc']}|{c['article']}"
            if aid not in article_ids:
                g.add_node("Article", aid, doc=c["source_doc"], number=c["article"],
                           title=c.get("article_title"), chapter=c.get("chapter"))
                article_ids.add(aid)
        ext = [r for r in c.get("references", []) if r["status"] == "external"]
        g.add_node("Chunk", c["chunk_id"], doc=c["source_doc"], page=c.get("page"),
                   token_count=c["token_count"], paragraph=c.get("paragraph"),
                   article=c.get("article"), section=c.get("section"),
                   external_refs=[r.get("target_law") for r in ext])

    # --- 노드: Concept ---
    for name, by_doc in concepts.items():
        g.add_node("Concept", name, defined_in=[d for d, v in by_doc.items() if v.get("defines")])

    # --- 노드: Condition ---
    for cd in conditions:
        g.add_node("Condition", cd["condition_id"], type=cd["type"],
                   applies_to=cd.get("applies_to"), page=cd.get("page"))

    # --- 엣지: CONTAINS (Doc->Article, Article->Chunk, Doc->Chunk(지침)) ---
    for aid, props in g.nodes["Article"].items():
        g.add_edge("CONTAINS", props["doc"], aid)
    for c in chunks:
        if c.get("article"):
            g.add_edge("CONTAINS", f"{c['source_doc']}|{c['article']}", c["chunk_id"])
        else:
            g.add_edge("CONTAINS", c["source_doc"], c["chunk_id"])

    # --- 엣지: NEXT_PARAGRAPH (같은 조 연속 청크) ---
    prev = None
    for c in chunks:
        if prev and prev.get("article") and prev["source_doc"] == c["source_doc"] \
           and prev.get("article") == c.get("article"):
            g.add_edge("NEXT_PARAGRAPH", prev["chunk_id"], c["chunk_id"])
        prev = c

    # --- 엣지: REFERENCES (Chunk->Article, 코퍼스 내 도착지 존재 시) ---
    dangling = 0
    for c in chunks:
        seen = set()
        for r in c.get("references", []):
            if r["status"] not in ("internal", "cross_corpus"):
                continue
            tgt = f"{r['target_doc']}|{r['article']}"
            if tgt in seen:
                continue
            seen.add(tgt)
            if tgt in article_ids:
                g.add_edge("REFERENCES", c["chunk_id"], tgt, status=r["status"])
            else:
                dangling += 1

    # --- 엣지: DEFINES / MENTIONS (Chunk->Concept) ---
    for c in chunks:
        for k in c.get("defines", []):
            g.add_edge("DEFINES", c["chunk_id"], k)
        for k in c.get("keywords", []):
            g.add_edge("MENTIONS", c["chunk_id"], k)

    # --- 엣지: CONFLICTS_WITH (Chunk<->Chunk) ---
    for r in conflicts:
        g.add_edge("CONFLICTS_WITH", r["chunk_a"], r["chunk_b"],
                   concept=r["concept"], axis=r.get("axis") or r.get("shared_metric", ""),
                   verified=r.get("verified", True))

    # --- 엣지: HAS_CONDITION (Doc->Condition, Chunk->Condition) ---
    for cd in conditions:
        g.add_edge("HAS_CONDITION", cd["source_doc"], cd["condition_id"])
        if cd.get("source_chunk_id") in g.nodes["Chunk"]:
            g.add_edge("HAS_CONDITION", cd["source_chunk_id"], cd["condition_id"])

    # --- 엣지: DELEGATES (법률 -> 시행령, 위임관계). '우선적용'이 아니라 위임·구체화. ---
    for child, parent in PARENT_IN_CORPUS.items():
        g.add_edge("DELEGATES", parent, child)

    return g, dangling


def multihop_demo(g):
    """개념 '상시근로자'에서 출발해 정의 청크 → 충돌 상대까지 멀티홉 탐색."""
    lines = []
    concept = "상시근로자"
    definers = [s for etype, dsts in [("x", [])] for s in []]  # placeholder
    # 역방향: 어떤 Chunk가 이 Concept를 DEFINES 하는가
    definers = [src for (src, dst, _) in g.edges["DEFINES"] if dst == concept]
    lines.append(f"### 멀티홉 예시: 개념 '{concept}'")
    lines.append(f"- 1홉) 이 개념을 **정의(DEFINES)** 하는 청크: {len(definers)}개")
    docs = sorted({g.nodes['Chunk'][c]['doc'] for c in definers if c in g.nodes['Chunk']})
    lines.append(f"  - 정의 문서: {', '.join(docs)}")
    # 2홉: 정의 청크들의 CONFLICTS_WITH 상대
    conf_pairs = []
    for c in definers:
        for nb in g.adj[c].get("CONFLICTS_WITH", []):
            conf_pairs.append((c, nb))
    # 전체 충돌 엣지도 포함
    lines.append(f"- 2홉) 정의 청크에서 **CONFLICTS_WITH**로 연결된 상대: {len(conf_pairs)}건")
    for a, b in conf_pairs[:6]:
        da = g.nodes['Chunk'].get(a, {}).get('doc', '?')
        db = g.nodes['Chunk'].get(b, {}).get('doc', '?')
        lines.append(f"  - {a} ({da}) ↔ {b} ({db})")
    return "\n".join(lines)


def main():
    g, dangling = build()
    nn, ne = g.n_nodes(), g.n_edges()

    out = ["# 그래프 구축 검증 리포트 (오프라인 빌드)", "",
           "> `graph/build_offline_graph.py` 산출. Neo4j 없이 동일 스키마로 그래프를 메모리에 구축해",
           "> 정합성과 멀티홉 동작을 검증한다. Neo4j 적재(build_graph.py)와 동일한 노드/엣지 정의를 사용.", ""]
    out.append("## 노드 수")
    for k, v in nn.items():
        out.append(f"- {k}: {v:,}")
    out.append("\n## 엣지 수")
    for k, v in ne.items():
        out.append(f"- {k}: {v:,}")
    out.append(f"\n- 끊긴 참조(코퍼스 내 도착 조항 없음, 엣지 미생성): {dangling:,}")
    out.append("\n## 무결성 점검")
    total_ext = sum(len(p.get("external_refs", [])) for p in g.nodes["Chunk"].values())
    out.append(f"- 외부 참조(속성 보존, 노드화 X): {total_ext:,}")
    out.append(f"- 고립 청크(어떤 엣지에도 없음) 점검: ", )
    referenced = set()
    for et, lst in g.edges.items():
        for s, d, _ in lst:
            referenced.add(s); referenced.add(d)
    isolated = [c for c in g.nodes["Chunk"] if c not in referenced]
    out.append(f"  → {len(isolated)}개")
    out.append("")
    out.append(multihop_demo(g))

    report = gc.ROOT / "graph" / "graph_build_report.md"
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    with (gc.ROOT / "graph" / "offline_graph.pkl").open("wb") as f:
        pickle.dump({"nodes": dict(g.nodes), "edges": dict(g.edges)}, f)
    print("nodes:", nn)
    print("edges:", ne)
    print("dangling refs:", dangling, "| isolated chunks:", len(isolated))
    print("wrote", report)


if __name__ == "__main__":
    main()
