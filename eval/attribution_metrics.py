"""로드맵 4번 — via 귀인 + 충돌 혼동행렬(P/R/F1) + 과소응답률.

* 측정 도구만 신설. 모델/그래프 무수정.
* (1) via 귀인: gold 조문이 '벡터 시드'로 잡혔나, '그래프 전용 경로'로만 잡혔나 → 그래프의 한계기여.
*     (검색만 호출 — 생성 불필요. 컨텍스트의 via 라벨로 판정.)
* (2) 충돌 혼동행렬: cross-doc 7(양성) vs distractor 6(음성)에서 결정론 flag의 P/R/F1.
*     (저장된 _exp_results.json의 g_flag 재사용.)
* (3) 과소응답률: '모름/근거없음' 회피 비율(Graph vs Naive). (저장된 g_ans/n_ans 재사용.)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "graph"))
sys.stdout.reconfigure(encoding="utf-8")

import nodes  # noqa: E402
from run_experiment import (  # noqa: E402
    CROSS_DOC_CONFLICT, DISTRACTOR, covers, load_items, norm_art, parse_gold,
)

EXP = ROOT / "eval" / "_exp_results.json"
OUT = ROOT / "eval" / "_attribution_results.json"

ABSTAIN = re.compile(r"모릅니다|모름|알 수 없|확인할 수 없|찾을 수 없|근거가? ?없|정보가? ?없|판단(?:하기|할 수)? ?(?:어렵|불가)")


def main():
    items = {it["id"]: it for it in load_items()}
    exp = {p["id"]: p for p in json.loads(EXP.read_text(encoding="utf-8"))["per_item"]}
    retr = nodes.get_retriever()

    per = []
    for iid, it in items.items():
        q = it["question"]
        gold = list(dict.fromkeys(parse_gold(s) for s in it["gold_articles"]))
        r = nodes.router({"query": q, "trace": []})
        out = retr.retrieve(q, r["params"])
        ctx = out["context"]
        seed_ctx = [c for c in ctx if c.get("via") == "seed"]
        graph_ctx = [c for c in ctx if c.get("via") != "seed"]

        tok = []
        for (d, a) in gold:
            sh = covers(seed_ctx, d, a)
            gh = covers(graph_ctx, d, a)
            tok.append({"gold": f"{d} {a}", "seed_hit": sh, "graph_hit": gh,
                        "graph_only": gh and not sh, "missed": not (sh or gh)})
        ep = exp.get(iid, {})
        per.append({
            "id": iid, "type": it["type"], "hop": it["hop"], "route": r["route"],
            "n_seed_ctx": len(seed_ctx), "n_graph_ctx": len(graph_ctx),
            "tokens": tok,
            "n_paths": len(out.get("paths", [])),
            "g_flag": ep.get("g_flag"),
            "g_abstain": bool(ABSTAIN.search(ep.get("g_ans", ""))),
            "n_abstain": bool(ABSTAIN.search(ep.get("n_ans", ""))),
        })
        go = sum(1 for t in tok if t["graph_only"])
        print(f"[{iid:2}] {it['type']:10} route={r['route']:8} | "
              f"gold {len(tok)} | seed-hit {sum(t['seed_hit'] for t in tok)} "
              f"graph-only {go} miss {sum(t['missed'] for t in tok)} | paths={len(out.get('paths',[]))}",
              flush=True)

    agg = aggregate(per)
    OUT.write_text(json.dumps({"per_item": per, "aggregate": agg}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("\n=== AGGREGATE ===")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"\n저장: {OUT}")


def aggregate(per):
    all_tok = [t for p in per for t in p["tokens"]]
    n = len(all_tok)
    seed_only = sum(1 for t in all_tok if t["seed_hit"] and not t["graph_hit"])
    both = sum(1 for t in all_tok if t["seed_hit"] and t["graph_hit"])
    graph_only = sum(1 for t in all_tok if t["graph_only"])
    missed = sum(1 for t in all_tok if t["missed"])

    # 충돌 혼동행렬 (cross-doc 7 양성 / distractor 6 음성, pred=g_flag)
    pos = [p for p in per if p["id"] in CROSS_DOC_CONFLICT]
    neg = [p for p in per if p["id"] in DISTRACTOR]
    tp = sum(1 for p in pos if p["g_flag"])
    fn = sum(1 for p in pos if not p["g_flag"])
    fp = sum(1 for p in neg if p["g_flag"])
    tn = sum(1 for p in neg if not p["g_flag"])
    prec = round(tp / (tp + fp), 3) if (tp + fp) else None
    rec = round(tp / (tp + fn), 3) if (tp + fn) else None
    f1 = round(2 * prec * rec / (prec + rec), 3) if (prec and rec) else None

    def abst(key, subset=None):
        s = subset or per
        return round(sum(1 for p in s if p[key]) / len(s), 3) if s else None

    conf_items = [p for p in per if p["type"] == "문서간충돌"]
    return {
        "via_attribution_tokens": {
            "n_gold_tokens": n,
            "seed_only": seed_only, "both_seed_and_graph": both,
            "graph_only": graph_only, "missed": missed,
            "graph_only_rate": round(graph_only / n, 3) if n else None,
            "메모": "graph_only=벡터 시드로는 못 잡고 그래프 전용 경로로만 도달한 gold → 그래프의 한계기여.",
        },
        "conflict_confusion_crossdoc7_vs_distractor6": {
            "TP": tp, "FN": fn, "FP": fp, "TN": tn,
            "precision": prec, "recall": rec, "f1": f1,
        },
        "abstention_rate": {
            "graph_overall": abst("g_abstain"), "naive_overall": abst("n_abstain"),
            "graph_conflict": abst("g_abstain", conf_items),
            "naive_conflict": abst("n_abstain", conf_items),
        },
    }


if __name__ == "__main__":
    main()
