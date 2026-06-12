"""로드맵 1번 — 채점 정밀화(조 -> 항 + 지침 page/section), 6번 가짜 hit 제거.

* 측정 도구(metric)만 신설한다. 모델/그래프(agent, graph)는 일절 수정하지 않는다.
* OLD 매칭: (문서, 조)  /  지침은 문서단위(*)  -> run_experiment.py의 기존 채점과 동일.
* NEW 매칭: (문서, 조, 항범위 교집합)  /  지침은 정밀 라벨(page/section anchor) 집합.
* 컨텍스트 청크의 항/페이지/섹션은 chunk_id 접미사에서 파싱한다(시스템 RETURN 변경 불필요).

실행: 18문항을 기존 에이전트(app.run)로 그대로 돌려 '최종 컨텍스트 chunk_id'를 모은 뒤,
OLD/NEW recall을 각각 계산해 비교한다. (생성/judge는 호출하지 않음 -> Context Recall만 측정)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.stdout.reconfigure(encoding="utf-8")

from app import run  # noqa: E402  (기존 에이전트 — 수정 안 함)
from run_eval import baseline_context  # noqa: E402

GOLD = ROOT / "eval" / "gold_set_final.json"

# final은 gold_chunk_ids에 정밀(항 범위/지침 섹션) 청크가 이미 해소돼 있어, 하드코딩
# 정밀 라벨 없이 gold_chunk_ids 자체를 진실값으로 OLD(조)/NEW(항·섹션) recall을 잰다.
# ★ 그래프 Chunk.doc의 실제 값과 정확히 일치해야 함(불일치 시 OLD recall 과소집계 — 일원화 버그 수정).
#   실제 distinct doc: 조특법시행령 / 조특법 / 장려금지침 / 고용보험법시행령 / 근로기준법시행령
PREFIX_DOC = {
    "고보령": "고용보험법시행령", "근기령": "근로기준법시행령",
    "조특령": "조특법시행령", "조특법": "조특법", "지침": "장려금지침",
}


def load_items():
    txt = GOLD.read_text(encoding="utf-8")
    raw = json.loads(txt) if txt.lstrip().startswith("{") else None
    return raw.get("items", []) if isinstance(raw, dict) else [json.loads(l) for l in txt.splitlines() if l.strip()]


def chunk_paras(cid: str) -> set | None:
    """chunk_id 접미사에서 항 범위 파싱. '고보령_제3조_제1-3항' -> {1,2,3}; 접미사 없으면 None."""
    parts = cid.split("_")
    if len(parts) < 3:
        return None
    nums = re.findall(r"\d+", parts[2])
    if not nums:
        return None
    if len(nums) >= 2:
        return set(range(int(nums[0]), int(nums[1]) + 1))
    return {int(nums[0])}


def gold_token(cid: str) -> dict:
    """gold chunk_id -> 채점 토큰. 지침: 정밀 섹션 청크(exact). 법령: (doc, 조, 항범위)."""
    pre = cid.split("_")[0]
    doc = PREFIX_DOC.get(pre, pre)
    if pre == "지침":
        return {"kind": "jishim", "doc": "장려금지침", "anchor": cid}
    parts = cid.split("_")
    jo = parts[1] if len(parts) > 1 else None
    return {"kind": "law", "doc": doc, "jo": jo, "paras": chunk_paras(cid)}


def covers_old(ctx, g) -> bool:
    """OLD = 조 단위(항 무시). 지침은 문서단위(*)."""
    if g["kind"] == "jishim":
        return any(c.get("doc") == "장려금지침" for c in ctx)
    for c in ctx:
        if c.get("doc") == g["doc"] and c.get("article") == g["jo"]:
            return True
    return False


def covers_new(ctx, g) -> bool:
    """NEW = 항/섹션 정밀. 지침은 정확한 섹션 청크, 법령은 같은 조+항범위 교집합."""
    if g["kind"] == "jishim":
        return any(c.get("id") == g["anchor"] for c in ctx)
    for c in ctx:
        if c.get("doc") != g["doc"] or c.get("article") != g["jo"]:
            continue
        cp = chunk_paras(c.get("id", ""))
        if g["paras"] is None or cp is None:      # 한쪽 항 미상 -> 조 단위 인정(한계 명시)
            return True
        if g["paras"] & cp:
            return True
    return False


def recall(ctx, golds, mode) -> float:
    if not golds:
        return 1.0
    fn = covers_old if mode == "old" else covers_new
    return sum(1 for g in golds if fn(ctx, g)) / len(golds)


def main():
    items = load_items()
    rows = []
    for it in items:
        iid = it["id"]
        if not it.get("answerable", True):        # 무응답: gold 없음 -> 공허 1.0 제외
            continue
        golds, seen = [], set()
        for cid in it.get("gold_chunk_ids", []):
            g = gold_token(cid)
            key = (g["kind"], g.get("doc"), g.get("jo"),
                   tuple(sorted(g["paras"])) if g.get("paras") else g.get("anchor"))
            if key in seen:
                continue
            seen.add(key)
            golds.append(g)
        if not golds:
            continue
        st = run(it["question"], iid)
        g_ctx = st.get("context", [])
        n_ctx = baseline_context(it["question"], budget=max(len(g_ctx), 8))
        row = {
            "id": iid, "type": it["type"], "hop": it["hop"],
            "g_old": recall(g_ctx, golds, "old"),
            "g_new": recall(g_ctx, golds, "new"),
            "n_old": recall(n_ctx, golds, "old"),
            "n_new": recall(n_ctx, golds, "new"),
            "has_jishim": any(g["kind"] == "jishim" for g in golds),
        }
        rows.append(row)
        print(f"[{iid:2}] {it['type']:10} hop{it['hop']} "
              f"G_old={row['g_old']:.2f} G_new={row['g_new']:.2f} | "
              f"N_old={row['n_old']:.2f} N_new={row['n_new']:.2f}", flush=True)

    def avg(k):
        return sum(r[k] for r in rows) / len(rows)

    print("\n=== 집계(Context Recall, answerable {}문항) ===".format(len(rows)))
    print(f"GraphRAG  OLD {avg('g_old'):.3f} -> NEW {avg('g_new'):.3f}  (Δ {avg('g_new')-avg('g_old'):+.3f})")
    print(f"Naive     OLD {avg('n_old'):.3f} -> NEW {avg('n_new'):.3f}  (Δ {avg('n_new')-avg('n_old'):+.3f})")
    changed = [r for r in rows if r["g_old"] != r["g_new"] or r["n_old"] != r["n_new"]]
    print(f"OLD≠NEW(항 단위에서 갈린) 문항: {[r['id'] for r in changed]}")
    (ROOT / "eval" / "_precision_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
