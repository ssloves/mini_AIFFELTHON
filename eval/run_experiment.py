"""실험 하니스 — eval/평가메트릭_정의서.md 기준.

★ 기존 코드/아키텍처(agent/, graph/)는 일절 수정하지 않고 그대로 import해 호출만 한다.
   이 파일은 '측정 장치'일 뿐, 피험 시스템(GraphRAG)·데이터·스키마를 바꾸지 않는다.

비교:
 - GraphRAG(Ours)  = agent/app.py:run (Router→Retriever→Reasoner→Verifier)
 - Naive RAG(강화)  = agent/run_eval.py:baseline_context (예산 일치+조문 다양화) + 공정 생성(gpt-4o)

지표(조 단위):
 - Context Recall(전체/hop별), Conflict Detection(cross-doc 7), Conflict 오탐률(distractor 6),
   Answer Accuracy(judge), Faithfulness(judge).
출력: eval/_exp_results.json (보고서 작성용 원자료).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.stdout.reconfigure(encoding="utf-8")

import nodes  # noqa: E402  (기존 모듈 — 수정 안 함)
from app import run  # noqa: E402
from run_eval import baseline_context  # noqa: E402

GOLD = ROOT / "eval" / "gold_set_원본.jsonl"
OUT = ROOT / "eval" / "_exp_results.json"

CROSS_DOC_CONFLICT = {1, 2, 3, 7, 8, 10, 11}
SINGLE_DOC_CONFLICT = {4, 5, 6, 9, 12}
DISTRACTOR = {13, 14, 15, 16, 17, 18}

NAIVE_MODEL = "gpt-4o"        # 그래프 Reasoner와 동일 모델 → 공정
JUDGE_MODEL = "gpt-4o-mini"

NAIVE_SYS = (
    "당신은 법률 질의응답 어시스턴트입니다. **아래 제공된 법령 발췌만** 근거로 질문에 답하십시오.\n"
    "발췌에 근거가 없으면 모른다고 하십시오. 핵심 주장에는 근거 조항을 함께 제시하십시오."
)


def _cli():
    return nodes._client()


def _chat(model, sys_p, user_p, as_json=True):
    kw = {"model": model, "temperature": 0,
          "messages": [{"role": "system", "content": sys_p},
                       {"role": "user", "content": user_p}]}
    if as_json:
        kw["response_format"] = {"type": "json_object"}
    r = _cli().chat.completions.create(**kw)
    txt = r.choices[0].message.content
    return json.loads(txt) if as_json else txt


# ---------- gold 조 단위 정규화/매칭 ----------
def norm_art(a):
    if not a:
        return None
    m = re.search(r"제\d+조(?:의\d+)?", a)
    return m.group(0) if m else None


def parse_gold(s: str):
    """'고용보험법시행령 제3조제1항' -> ('고용보험법시행령','제3조'); 조 없으면 ('문서','*')."""
    s = s.strip()
    m = re.match(r"^(\S+?)\s*(제\d+조(?:의\d+)?)", s)
    if m:
        return (m.group(1), m.group(2))
    d = re.match(r"^([가-힣A-Za-z]+)", s)
    return (d.group(1) if d else s, "*")


def covers(ctx, doc, art):
    for c in ctx:
        if c.get("doc") != doc:
            continue
        if art == "*" or norm_art(c.get("article")) == art:
            return True
    return False


def recall(ctx, gold_tokens):
    if not gold_tokens:
        return 1.0
    hit = sum(1 for (d, a) in gold_tokens if covers(ctx, d, a))
    return hit / len(gold_tokens)


def ctx_articles(ctx):
    out = []
    for c in ctx:
        out.append(f"{c.get('doc')} {norm_art(c.get('article')) or '(문서단위)'}")
    return sorted(set(out))


# ---------- Naive 생성 ----------
def fmt_ctx(ctx):
    return "\n\n".join(f"[{c.get('doc')} {norm_art(c.get('article')) or ''}]\n{c.get('text','')[:800]}"
                       for c in ctx)


def naive_generate(question, ctx):
    user = f"질문: {question}\n\n[법령 발췌]\n{fmt_ctx(ctx)}"
    return _chat(NAIVE_MODEL, NAIVE_SYS, user, as_json=False)


# ---------- LLM judges ----------
def judge_accuracy(ans, gold):
    j = _chat(JUDGE_MODEL,
              "두 답변이 핵심 결론과 근거에서 일치하는 정도를 0~1로 채점. JSON {\"score\":0~1}",
              f"[모범답안]\n{gold}\n\n[시스템답변]\n{ans}")
    try:
        return float(j.get("score", 0))
    except Exception:
        return 0.0


def judge_conflict(ans):
    j = _chat(JUDGE_MODEL,
              "다음 답변이 '서로 다른 두 제도/법령의 기준이 서로 다르다(충돌/차이/별도 산정)'는 점을 "
              "명시적으로 서술하는가? JSON {\"yes\":true|false}",
              f"[답변]\n{ans}")
    return bool(j.get("yes", False))


def judge_faith(ans, ctx):
    j = _chat(JUDGE_MODEL,
              "답변의 주장들이 제공된 [발췌]로 뒷받침되는 정도(환각 없음=1, 발췌 밖 단정 많음=0)를 0~1로. "
              "JSON {\"score\":0~1}",
              f"[발췌]\n{fmt_ctx(ctx)[:6000]}\n\n[답변]\n{ans}")
    try:
        return float(j.get("score", 0))
    except Exception:
        return 0.0


def main():
    items = [json.loads(l) for l in GOLD.open(encoding="utf-8") if l.strip()]
    per = []
    for it in items:
        iid = it["id"]
        gold_tokens = list(dict.fromkeys(parse_gold(s) for s in it["gold_articles"]))

        st = run(it["question"], iid)
        g_ctx = st.get("context", [])
        g_ans = st.get("answer", "")
        g_flag = bool(st.get("conflict_flagged"))

        n_ctx = baseline_context(it["question"], budget=max(len(g_ctx), 8))
        n_ans = naive_generate(it["question"], n_ctx)

        rec = {
            "id": iid, "type": it["type"], "hop": it["hop"],
            "question": it["question"],
            "gold_tokens": [f"{d} {a}" for d, a in gold_tokens],
            "route": st.get("route"), "verdict": st.get("verdict"),
            "retries": st.get("retry_count", 0),
            "recall_g": round(recall(g_ctx, gold_tokens), 3),
            "recall_n": round(recall(n_ctx, gold_tokens), 3),
            "g_ctx": ctx_articles(g_ctx), "n_ctx": ctx_articles(n_ctx),
            "g_flag": g_flag,
            "jc_g": judge_conflict(g_ans), "jc_n": judge_conflict(n_ans),
            "acc_g": round(judge_accuracy(g_ans, it["gold_answer"]), 3),
            "acc_n": round(judge_accuracy(n_ans, it["gold_answer"]), 3),
            "faith_g": round(judge_faith(g_ans, g_ctx), 3),
            "faith_n": round(judge_faith(n_ans, n_ctx), 3),
            "g_ans": g_ans, "n_ans": n_ans,
        }
        per.append(rec)
        print(f"[{iid:2}] {it['type']:10} hop{it['hop']} route={rec['route']:8} "
              f"recall G={rec['recall_g']:.2f} N={rec['recall_n']:.2f} "
              f"flag={int(g_flag)} jc_g={int(rec['jc_g'])} jc_n={int(rec['jc_n'])} "
              f"acc G={rec['acc_g']:.2f} N={rec['acc_n']:.2f}", flush=True)

    agg = aggregate(per)
    OUT.write_text(json.dumps({"per_item": per, "aggregate": agg},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== AGGREGATE ===")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"\n저장: {OUT}")


def _mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 3) if xs else None


def aggregate(per):
    def by_hop(sys_key, h):
        return _mean(p[sys_key] for p in per if p["hop"] == h)

    cross = [p for p in per if p["id"] in CROSS_DOC_CONFLICT]
    dist = [p for p in per if p["id"] in DISTRACTOR]
    return {
        "n_items": len(per),
        "context_recall": {
            "graph_overall": _mean(p["recall_g"] for p in per),
            "naive_overall": _mean(p["recall_n"] for p in per),
            "graph_by_hop": {h: by_hop("recall_g", h) for h in (1, 2, 3)},
            "naive_by_hop": {h: by_hop("recall_n", h) for h in (1, 2, 3)},
        },
        "miss_rate_by_hop": {
            "graph": {h: (round(1 - by_hop("recall_g", h), 3) if by_hop("recall_g", h) is not None else None) for h in (1, 2, 3)},
            "naive": {h: (round(1 - by_hop("recall_n", h), 3) if by_hop("recall_n", h) is not None else None) for h in (1, 2, 3)},
        },
        "conflict_detection_crossdoc7": {
            "graph_flag_and_judge": _mean(1.0 if (p["g_flag"] and p["jc_g"]) else 0.0 for p in cross),
            "graph_judge_only": _mean(1.0 if p["jc_g"] else 0.0 for p in cross),
            "naive_judge_only": _mean(1.0 if p["jc_n"] else 0.0 for p in cross),
        },
        "conflict_false_alarm_distractor6": {
            "graph_flag": _mean(1.0 if p["g_flag"] else 0.0 for p in dist),
            "graph_judge": _mean(1.0 if p["jc_g"] else 0.0 for p in dist),
            "naive_judge": _mean(1.0 if p["jc_n"] else 0.0 for p in dist),
        },
        "answer_accuracy": {
            "graph": _mean(p["acc_g"] for p in per),
            "naive": _mean(p["acc_n"] for p in per),
        },
        "faithfulness": {
            "graph": _mean(p["faith_g"] for p in per),
            "naive": _mean(p["faith_n"] for p in per),
        },
        "verifier_accept_rate": _mean(1.0 if p["verdict"] == "ACCEPT" else 0.0 for p in per),
    }


if __name__ == "__main__":
    main()
