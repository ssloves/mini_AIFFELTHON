"""로드맵 7번 — gold-path 추론경로 메트릭 (경로 비유일성 인지형).

문제: 같은 답에 이르는 *유효한 길은 하나가 아니다*(CONFLICTS_WITH 직접 / 개념 브리지 /
참조 사슬은 같은 두 노드를 잇는 동치 경로다). 따라서 모델의 실제 traverse를 단일 gold 경로와
*정확히* 비교하면 유효한 대안을 부당하게 깎는다.

해법 — 3계층으로 분리:
  1) 노드 계층(법적 근거)   : gold 조문 (Context Recall이 측정).
  2) 관계 계층(무엇을 이어야): R_gold = gold 노드 사이 *필수 연결*. **전문가 근거**(큐레이션 충돌쌍 +
     작성 gold_path)에서만 도출 → 위상에서 만들지 않음 → by-construction 순환 회피.
  3) 경로 계층(어떻게 이었나): 모델이 로깅한 경로(로드맵5)를 (조문쌍) 관계로 축약.
     **동치류**: 같은 조문쌍을 어떤 엣지로 잇든 동일 관계로 본다 → 길의 비유일성 흡수.

판정(요구관계 r=조문쌍별):
  - SATISFIED  : 두 노드 모두 컨텍스트에 있고 + 모델이 그 쌍을 실제 경로로 이었다 → 옳은 길.
  - COINCIDENT : 두 노드는 도달했으나 잇는 경로가 없다 → "맞는 자리, 틀린/없는 이유"(핵심 진단).
  - MISSED     : 한 노드라도 도달 못 함.

지표(R_gold≠∅ 문항만; origin×hop 분리집계 — 가이드 §6):
  - Path Recall   = SATISFIED / |R_gold|            (필수 관계를 옳은 길로 밟은 비율)
  - Coincidence   = COINCIDENT / (SAT+COINC)         (도달한 쌍 중 길 없이 우연히 닿은 비율)
  - Conn Precision= (모델이 이은 gold-gold쌍 ∩ R_gold) / (모델이 이은 gold-gold쌍)
  - Faithfulness  = "노드 다 도달한 문항 중 Path Recall=1 비율" = 맞는 *이유*로 맞은 비율.

정직한 범위: 로깅 경로는 *검색 단계 traverse*(3개 엣지유형)만 포착 → Path Recall은 하한.
LLM 내부 추론사슬은 측정 대상 아님. Naive RAG는 그래프 traverse가 없어 Path Recall=0(정의상) →
"길 없는 바닥선"으로 해석(노드는 닿아도 추적가능한 법적 연결은 0).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "graph"))
sys.stdout.reconfigure(encoding="utf-8")

GOLD = ROOT / "eval" / "gold_set_final.json"
CONFLICTS = ROOT / "data" / "conflicts_confirmed.jsonl"
RUNS = ROOT / "eval" / "_path_metric_runs.json"   # 검색 결과 캐시(재실행 저비용)
OUT = ROOT / "eval" / "_path_metric_results.json"

COMPARE_TYPES = {"문서간충돌", "동명무충돌", "용도상이"}


def art_key(cid: str) -> str:
    """chunk_id → 조문 노드 키(항 무시): '근기령_제7조의2_제3-4항' → '근기령_제7조의2'."""
    if not cid:
        return ""
    p = cid.split("_")
    return "_".join(p[:2]) if len(p) >= 2 else cid


def doc_of(art: str) -> str:
    return art.split("_")[0] if art else ""


def expert_pairs() -> dict:
    """큐레이션 충돌쌍(verified만) → {frozenset(조문쌍): concept}. 위상이 아닌 전문가 근거."""
    out = {}
    for line in CONFLICTS.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("verified", False):
            continue
        pair = frozenset({art_key(r["chunk_a"]), art_key(r["chunk_b"])})
        if len(pair) == 2:
            out[pair] = r.get("concept", "")
    return out


# ---------------- 검색 실행(router+retriever만; 캐시) ----------------
def run_retrieval(items):
    if RUNS.exists():
        cached = {int(k): v for k, v in json.loads(RUNS.read_text(encoding="utf-8")).items()}
        if all(it["id"] in cached for it in items):
            return cached
    from nodes import router, get_retriever
    retr = get_retriever()
    runs = {}
    for it in items:
        q = it["question"]
        st = router({"query": q, "trace": []})
        out = retr.retrieve(q, st["params"])
        runs[it["id"]] = {
            "route": st["route"],
            "context_arts": sorted({art_key(c["id"]) for c in out["context"]}),
            "paths": [{"src": art_key(p["src"]), "dst": art_key(p["dst"]),
                       "edge": p["edge"]} for p in out.get("paths", [])],
        }
        print(f"[{it['id']:>2}] route={st['route']:<8} ctx_arts={len(runs[it['id']]['context_arts'])} "
              f"paths={len(runs[it['id']]['paths'])}")
    RUNS.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    return runs


# ---------------- R_gold 도출 ----------------
def build_r_gold(item, exp):
    gold_arts = {art_key(c) for c in item.get("gold_chunk_ids", [])}
    R = {}  # frozenset(pair) -> anchor(concept/edge)
    # 1) 작성 gold_path — 단, 양끝이 모두 *해소된 gold 청크*일 때만(placeholder 노드 오염 차단)
    for e in item.get("gold_path", []):
        pair = frozenset({art_key(e.get("src", "")), art_key(e.get("dst", ""))})
        if len(pair) == 2 and pair <= gold_arts:
            R[pair] = e.get("axis", "authored")
    # 2) 전문가 충돌쌍 증강(gold 조문 안에 양끝이 모두 존재할 때만)
    for pair, concept in exp.items():
        if pair <= gold_arts:
            R[pair] = concept
    # 3) 비교형 보강: 전문가/작성이 비었고 정확히 2개 문서를 비교하는 문항이면 교차문서 1쌍
    if not R and item.get("type") in COMPARE_TYPES:
        by_doc = defaultdict(list)
        for a in sorted(gold_arts):
            by_doc[doc_of(a)].append(a)
        if len(by_doc) == 2:
            (a1,), (a2,) = ([v[0]] for v in by_doc.values())
            R[frozenset({a1, a2})] = "shared-concept(비교)"
    return R, gold_arts


# ---------------- 문항 채점 ----------------
def score_item(item, run, exp):
    R, gold_arts = build_r_gold(item, exp)
    present = set(run["context_arts"])
    model_pairs = defaultdict(set)  # frozenset(pair) -> {edge types}
    for p in run["paths"]:
        pr = frozenset({p["src"], p["dst"]})
        if len(pr) == 2:
            model_pairs[pr].add(p["edge"])

    cls = {}  # pair -> SATISFIED/COINCIDENT/MISSED
    sat_edges = []
    for pair in R:
        a, b = tuple(pair)
        if a in present and b in present:
            if pair in model_pairs:
                cls[pair] = "SATISFIED"
                sat_edges += list(model_pairs[pair])
            else:
                cls[pair] = "COINCIDENT"
        else:
            cls[pair] = "MISSED"

    n = len(R)
    sat = sum(v == "SATISFIED" for v in cls.values())
    coinc = sum(v == "COINCIDENT" for v in cls.values())
    reached = sat + coinc
    # gold-gold 연결의 정밀도(불필요한 gold쌍 연결 페널티) + 비-gold 확장(spurious) 수
    gg = [pr for pr in model_pairs if pr <= gold_arts]
    gg_required = [pr for pr in gg if pr in R]
    spurious = sum(1 for pr in model_pairs if not (pr <= gold_arts))
    return {
        "id": item["id"], "type": item["type"], "hop": item["hop"], "origin": item["origin"],
        "route": run["route"],
        "n_required": n,
        "satisfied": sat, "coincident": coinc, "missed": n - reached,
        "path_recall": (sat / n) if n else None,
        "coincidence": (coinc / reached) if reached else None,
        "conn_precision": (len(gg_required) / len(gg)) if gg else None,
        "spurious_links": spurious,
        "node_recall_full": all(a in present for a in gold_arts) if gold_arts else None,
        "sat_edge_types": dict(Counter(sat_edges)),
        "detail": {f"{'|'.join(sorted(k))}": v for k, v in cls.items()},
    }


def macro(rows, key):
    vals = [r[key] for r in rows if r[key] is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def cell_report(rows, label):
    if not rows:
        return f"  {label:<22} n=0"
    pr, co, cp = macro(rows, "path_recall"), macro(rows, "coincidence"), macro(rows, "conn_precision")
    f1 = None
    if pr is not None and cp is not None and (pr + cp) > 0:
        f1 = round(2 * pr * cp / (pr + cp), 3)
    return (f"  {label:<22} n={len(rows):<3} PathRecall={pr}  Coincidence={co}  "
            f"ConnPrec={cp}  F1={f1}")


def main():
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    items = data["items"]
    exp = expert_pairs()
    runs = run_retrieval(items)

    scored = [score_item(it, runs[it["id"]], exp) for it in items]
    rel = [r for r in scored if r["n_required"] > 0]   # 요구관계 있는 문항만

    print("\n================ Gold-Path 추론경로 메트릭 ================")
    print(f"전체 {len(items)}문항 중 요구관계(R_gold≠∅) 보유 = {len(rel)}문항\n")

    print("[전체]")
    print(cell_report(rel, "all"))
    print("\n[origin 분리 — 가이드 §6 by-construction 방어]")
    for o in ("generated", "held_out"):
        print(cell_report([r for r in rel if r["origin"] == o], o))
    print("\n[origin × hop]")
    for o in ("generated", "held_out"):
        for h in (2, 3):
            print(cell_report([r for r in rel if r["origin"] == o and r["hop"] == h], f"{o} hop{h}"))
    print("\n[유형별]")
    for t in sorted({r["type"] for r in rel}):
        print(cell_report([r for r in rel if r["type"] == t], t))

    # 충실도(맞는 이유로 맞았나): 노드 완전 도달 문항 중 Path Recall=1 비율
    reached_full = [r for r in rel if r["node_recall_full"]]
    faithful = [r for r in reached_full if r["path_recall"] == 1.0]
    print("\n[충실도 — 맞는 이유로 맞았나]")
    print(f"  노드 완전도달 {len(reached_full)}문항 중 경로완전(PathRecall=1) {len(faithful)}문항 "
          f"= {round(len(faithful)/len(reached_full),3) if reached_full else None}")
    coincident_only = [r for r in reached_full if r["path_recall"] < 1.0]
    print(f"  ┗ '맞는 자리·틀린 이유'(노드는 닿았으나 경로 불완전) = {len(coincident_only)}문항 "
          f"{[r['id'] for r in coincident_only]}")

    # satisfied 엣지유형 분포(어떤 길로 이었나)
    et = Counter()
    for r in rel:
        et.update(r["sat_edge_types"])
    print("\n[SATISFIED 엣지유형 분포 — 어떤 길로 이었나(동치류)]")
    for k, v in et.most_common():
        print(f"  {v:>3}  {k}")

    # Naive 바닥선(정의상): 경로 0 → PathRecall=0, Coincidence=1.0
    print("\n[Naive 바닥선] 그래프 traverse 없음 → PathRecall=0, Coincidence=1.0 (정의상)")

    OUT.write_text(json.dumps({
        "n_items": len(items), "n_with_required": len(rel),
        "overall": {"path_recall": macro(rel, "path_recall"),
                    "coincidence": macro(rel, "coincidence"),
                    "conn_precision": macro(rel, "conn_precision")},
        "by_origin": {o: {"n": len([r for r in rel if r["origin"] == o]),
                          "path_recall": macro([r for r in rel if r["origin"] == o], "path_recall"),
                          "coincidence": macro([r for r in rel if r["origin"] == o], "coincidence")}
                      for o in ("generated", "held_out")},
        "faithfulness": {"reached_full": len(reached_full), "fully_faithful": len(faithful),
                         "coincident_ids": [r["id"] for r in coincident_only]},
        "sat_edge_types": dict(et),
        "rows": scored,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
