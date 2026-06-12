"""로드맵 3번 — ablation 사다리 B0~B4.

목적: GraphRAG vs Naive의 격차를 *요인 하나씩* 더해가며 분해해, 어디서 차이가 나는지를 수치로 가른다.
원칙: **기존 코드/아키텍처(agent/, graph/) 무수정.** 모두 기존 함수/노드를 *조합*해 호출만 한다.
  - 앵커 OFF(B3)는 retrieval.py를 고치지 않고, `_conflict_by_concept`를 빈 결과로 덮는 **비침습 subclass**로 구현.

────────────────────────────────────────────────────────────────────────
사다리 설계 — 통제변인(고정)과 실험변수(유일 차이)

| 단계 | 컨텍스트(검색) | 생성 파이프라인 | 직전 대비 *유일* 실험변수 |
|------|----------------|------------------|----------------------------|
| B0  | Naive 벡터(예산 L)        | 단순 프롬프트, 단발            | (기준선)
| B1  | Naive 벡터(예산 L, 동일)  | Reasoner 프롬프트, 단발        | 생성 프롬프트 품질
| B2  | Naive 벡터(예산 L, 동일)  | Reasoner + Verifier + 재시도   | 자기수정 루프(Verifier)
| B3  | **그래프 확장(앵커 OFF)** | Reasoner + Verifier + 재시도(B2와 동일) | 컨텍스트 출처: 벡터 → 그래프 확장
| B4  | 그래프 확장(앵커 ON)=현행 | Reasoner + Verifier + 재시도(동일) | 개념-충돌 앵커 OFF→ON

전 단계 공통 통제변인: 동일 18문항/gold, 동일 생성모델(gpt-4o)·judge(gpt-4o-mini)·temperature=0,
  동일 채점 함수(조 단위 recall), **동일 컨텍스트 예산 L(=B4 그래프 컨텍스트 길이)** → 예산 비대칭 제거.
귀인:
  검색 순수기여 = Recall(B3,B4) − Recall(B0)      (생성과 무관)
  프롬프트 기여 = Acc(B1) − Acc(B0)               (컨텍스트 동일)
  재시도 기여   = Acc(B2) − Acc(B1)               (컨텍스트 동일)
  그래프 기여   = 지표(B3) − 지표(B2)             (파이프라인 동일, 컨텍스트만 그래프)
  충돌앵커 기여 = 지표(B4) − 지표(B3)             (앵커만 ON/OFF)
출력: eval/_ablation_results.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "graph"))
sys.stdout.reconfigure(encoding="utf-8")

import nodes  # noqa: E402  (Router/Reasoner/Verifier 노드 — 수정 안 함)
from retrieval import Retriever  # noqa: E402
from run_eval import baseline_context  # noqa: E402
from run_experiment import (  # noqa: E402  (조 단위 채점·Naive 생성·judge 재사용)
    CROSS_DOC_CONFLICT, DISTRACTOR, judge_accuracy, judge_conflict,
    load_items, naive_generate, parse_gold, recall,
)

OUT = ROOT / "eval" / "_ablation_results.json"
MAX_RETRY = nodes.MAX_RETRY  # 2 (production과 동일)


# ── 앵커 OFF 검색기: retrieval.py 무수정. 개념-충돌 앵커 주입만 비활성 ──
class NoAnchorRetriever(Retriever):
    def _conflict_by_concept(self, concepts):  # noqa: D401
        return []  # B3: 정준 충돌쌍 직접 주입(앵커) 제거 — 나머지(시드충돌·브리지·참조)는 동일


# ── 검색 fn 팩토리(production 재시도 폭 확대 규칙을 그대로 미러) ──
def make_graph_retrieve(retr, route_params):
    def fn(query, rc):
        params = dict(route_params)
        if rc:  # nodes.retriever와 동일: 재시도 시 폭만 확대
            params["seed_top_k"] = params.get("seed_top_k", 5) + 3
            params["max_context_chunks"] = params.get("max_context_chunks", 12) + 4
        return retr.retrieve(query, params)
    return fn


def make_naive_retrieve(budget_L):
    def fn(query, rc):
        ctx = baseline_context(query, budget=budget_L + rc * 4)  # 재시도 시 예산 확대(그래프와 대칭)
        return {"seeds": [], "context": ctx, "conflicts": [],
                "expanded_via": {}, "has_conflict_edge": False}
    return fn


# ── 생성 변형 ──
def gen_reasoner_once(query, ctx, conflicts):
    """B1: Reasoner 프롬프트 1회 생성(Verifier 없음)."""
    st = {"query": query, "context": ctx, "conflicts": conflicts, "trace": []}
    out = nodes.reasoner(st)
    return out.get("answer", ""), bool(out.get("conflict_flagged"))


def gen_full_pipeline(query, qa_id, retrieve_fn):
    """B2/B3/B4: Reasoner + Verifier + 재시도 루프(app.py 흐름 그대로 미러)."""
    state = {"query": query, "qa_id": qa_id, "retry_count": 0, "trace": []}
    while True:
        out = retrieve_fn(query, state["retry_count"])
        state.update({"seeds": out.get("seeds", []), "context": out["context"],
                      "conflicts": out["conflicts"], "expanded_via": out.get("expanded_via", {})})
        state.update(nodes.reasoner(state))
        state.update(nodes.verifier(state))  # retry_count를 REJECT 시 +1
        if state.get("verdict") == "ACCEPT" or state["retry_count"] > MAX_RETRY:
            break
    return state


def score(level, ans, ctx, flag, gold_tokens, gold_answer):
    return {
        "recall": round(recall(ctx, gold_tokens), 3),
        "acc": round(judge_accuracy(ans, gold_answer), 3),
        "flag": bool(flag),
        "jc": judge_conflict(ans),
        "ctx_n": len(ctx),
    }


def main():
    items = load_items()
    prod_retr = nodes.get_retriever()      # B4 = production 검색기(앵커 ON)
    noanchor_retr = NoAnchorRetriever()    # B3 = 앵커 OFF

    per = []
    for it in items:
        iid = it["id"]
        q = it["question"]
        gold_tokens = list(dict.fromkeys(parse_gold(s) for s in it["gold_articles"]))
        gold_answer = it["gold_answer"]

        # route 결정(B3·B4 공통 — 동일 route/params로 앵커만 가른다)
        r = nodes.router({"query": q, "trace": []})
        route, params = r["route"], r["params"]

        # B4: 그래프 앵커 ON 전체(=현행 시스템). 컨텍스트 길이 L을 통제 예산으로 사용
        s4 = gen_full_pipeline(q, iid, make_graph_retrieve(prod_retr, params))
        L = max(len(s4.get("context", [])), 8)

        # B0/B1/B2 공통: Naive 벡터 컨텍스트(예산 L 고정)
        naive_ctx = baseline_context(q, budget=L)

        # B0: 단순 프롬프트 단발
        b0_ans = naive_generate(q, naive_ctx)

        # B1: Reasoner 프롬프트 단발(컨텍스트 동일)
        b1_ans, b1_flag = gen_reasoner_once(q, naive_ctx, [])

        # B2: Reasoner + Verifier + 루프(컨텍스트=Naive)
        s2 = gen_full_pipeline(q, iid, make_naive_retrieve(L))

        # B3: 그래프 앵커 OFF + 동일 파이프라인
        s3 = gen_full_pipeline(q, iid, make_graph_retrieve(noanchor_retr, params))

        rec = {
            "id": iid, "type": it["type"], "hop": it["hop"], "route": route,
            "answerable": it.get("answerable", True),
            "gold_tokens": [f"{d} {a}" for d, a in gold_tokens],
            "B0": score("B0", b0_ans, naive_ctx, False, gold_tokens, gold_answer),
            "B1": score("B1", b1_ans, naive_ctx, b1_flag, gold_tokens, gold_answer),
            "B2": score("B2", s2.get("answer", ""), s2.get("context", []),
                        s2.get("conflict_flagged"), gold_tokens, gold_answer),
            "B3": score("B3", s3.get("answer", ""), s3.get("context", []),
                        s3.get("conflict_flagged"), gold_tokens, gold_answer),
            "B4": score("B4", s4.get("answer", ""), s4.get("context", []),
                        s4.get("conflict_flagged"), gold_tokens, gold_answer),
            "verdict_B4": s4.get("verdict"), "retries_B4": s4.get("retry_count", 0),
        }
        per.append(rec)
        print(f"[{iid:2}] {it['type']:10} hop{it['hop']} route={route:8} | "
              f"Recall B0={rec['B0']['recall']:.2f} B3={rec['B3']['recall']:.2f} B4={rec['B4']['recall']:.2f} | "
              f"Acc B0={rec['B0']['acc']:.2f} B1={rec['B1']['acc']:.2f} B2={rec['B2']['acc']:.2f} "
              f"B3={rec['B3']['acc']:.2f} B4={rec['B4']['acc']:.2f} | "
              f"flag B3={int(rec['B3']['flag'])} B4={int(rec['B4']['flag'])}", flush=True)

    agg = aggregate(per)
    OUT.write_text(json.dumps({"per_item": per, "aggregate": agg}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("\n=== AGGREGATE ===")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"\n저장: {OUT}")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def aggregate(per):
    LV = ("B0", "B1", "B2", "B3", "B4")
    cross = [p for p in per if p["id"] in CROSS_DOC_CONFLICT]
    dist = [p for p in per if p["id"] in DISTRACTOR]
    ans = [p for p in per if p.get("answerable", True)]  # recall/acc는 answerable만(공허 inflation 차단)

    def lv(metric, sub=None):
        return {b: _mean((p[b][metric] for p in (sub if sub is not None else ans))) for b in LV}

    recall_overall = lv("recall")
    acc_overall = lv("acc")
    # 충돌 감지(cross-doc 7): flag&judge / judge-only
    conf_flag = {b: _mean(1.0 if (p[b]["flag"] and p[b]["jc"]) else 0.0 for p in cross) for b in LV}
    conf_judge = {b: _mean(1.0 if p[b]["jc"] else 0.0 for p in cross) for b in LV}
    # distractor 오탐(6): flag
    false_alarm = {b: _mean(1.0 if p[b]["flag"] else 0.0 for p in dist) for b in LV}

    def by_hop(b, h):
        return _mean(p[b]["recall"] for p in ans if p["hop"] == h)

    return {
        "n_items": len(per),
        "recall_overall": recall_overall,
        "recall_by_hop": {h: {b: by_hop(b, h) for b in LV} for h in (1, 2, 3)},
        "accuracy_overall": acc_overall,
        "conflict_detect_crossdoc7_flag_and_judge": conf_flag,
        "conflict_detect_crossdoc7_judge_only": conf_judge,
        "false_alarm_distractor6_flag": false_alarm,
        "decomposition": {
            "검색_순수기여_RecallB4-RecallB0": _delta(recall_overall["B4"], recall_overall["B0"]),
            "검색_순수기여_RecallB3-RecallB0": _delta(recall_overall["B3"], recall_overall["B0"]),
            "프롬프트_기여_AccB1-AccB0": _delta(acc_overall["B1"], acc_overall["B0"]),
            "재시도_기여_AccB2-AccB1": _delta(acc_overall["B2"], acc_overall["B1"]),
            "그래프_기여_AccB3-AccB2": _delta(acc_overall["B3"], acc_overall["B2"]),
            "충돌앵커_기여_AccB4-AccB3": _delta(acc_overall["B4"], acc_overall["B3"]),
            "충돌앵커_기여_RecallB4-RecallB3": _delta(recall_overall["B4"], recall_overall["B3"]),
            "충돌앵커_기여_충돌감지B4-B3": _delta(conf_flag["B4"], conf_flag["B3"]),
        },
    }


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 3)


if __name__ == "__main__":
    main()
