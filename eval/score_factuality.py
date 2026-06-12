"""로드맵 2번 — judge 사실성 체크(근거 귀속) + '오염' 가설 검증.

* 측정 도구만 신설. 모델/그래프 무수정. 저장된 답변(eval/_exp_results.json)을 재채점한다.
* 사실성 = '답변의 수치/주장이 실제로 인용·검색된 청크 text에 근거하는가'(환각=근거 없음).
  - (A) 수치 근거율(결정론): 답변의 의미있는 수치(시간/세/년/명/원/%)가 근거 텍스트에 실존하는가.
  - (B) 엄격 LLM 지지도: '제공된 발췌에만' 근거해 핵심 주장 지지 여부(모수지식 사용 금지).
  - (C) 인용 존재성: 답변이 [chunk_id]로 인용한 청크가 실제 KG에 존재하는가.
* '17번 오염(군필 39세)' 가설은 인용 청크 text로 사실 확인하여 참/거짓을 *증거로* 판정한다.
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

import graph_common as gc  # noqa: E402
from run_eval import baseline_context  # noqa: E402

CHUNKS = ROOT / "data" / "chunks" / "all_chunks.jsonl"
EXP = ROOT / "eval" / "_exp_results.json"
JUDGE_MODEL = "gpt-4o-mini"

CITE_RE = re.compile(r"\[([가-힣A-Za-z0-9_·\-]+)\]")
ART_RE = re.compile(r"제\s*\d+\s*(?:조(?:의\d+)?|항|호|목|장|절)")
MEANINGFUL_NUM = re.compile(r"\d+(?:\.\d+)?(?:\s*~\s*\d+)?\s*(?:세|시간|년|개월|일|명|인|원|만원|%|퍼센트|배)")
# 인용ID -> 조(article) 키: 말미 '_제N항/호' 단락 꼬리를 떼어 조단위로 정규화
ART_KEY_RE = re.compile(r"^(.*_제\d+조(?:의\d+)?)(?:_.*)?$")


def art_key(cid: str) -> str:
    m = ART_KEY_RE.match(cid)
    return m.group(1) if m else cid


def resolve_evidence(cited: list[str], ctext: dict, question: str):
    """인용 청크 텍스트 + 질문문을 근거로 묶는다.
    인용ID가 정규 chunk_id와 정확히 안 맞으면 조단위로 폴백(프로젝트 정규화와 동일).
    조단위로도 못 찾은 인용만 '미해소(환각 의심)'로 분류한다."""
    parts = [question]
    unresolved = []
    for c in cited:
        if c in ctext:
            parts.append(ctext[c])
            continue
        key = art_key(c)
        hits = [t for cid, t in ctext.items() if cid == key or cid.startswith(key + "_")]
        if hits:
            parts.extend(hits)
        else:
            unresolved.append(c)
    return "\n".join(parts), unresolved

_cli = None


def cli():
    global _cli
    if _cli is None:
        _cli = gc.get_openai()
    return _cli


def load_chunk_text() -> dict:
    m = {}
    for line in CHUNKS.open(encoding="utf-8"):
        if not line.strip():
            continue
        o = json.loads(line)
        m[o["chunk_id"]] = o.get("text", "")
    return m


def clean_for_numbers(ans: str) -> str:
    a = CITE_RE.sub(" ", ans)             # [chunk 인용] 제거
    a = ART_RE.sub(" ", a)                # 제N조/제N항 등 조문 번호 제거(주장이 아님)
    a = re.sub(r"20\d\d\s*년?", " ", a)   # 연도(2024/2025/2026년) 제거
    return a


def numbers(ans: str) -> list[str]:
    out = []
    for m in MEANINGFUL_NUM.finditer(clean_for_numbers(ans)):
        out.append(m.group(0).strip())
    return out


def digits_of(tok: str) -> list[str]:
    return re.findall(r"\d+", tok)


def grounded(tok: str, text: str) -> bool:
    return all(d in text for d in digits_of(tok))


def numeric_grounding(ans: str, evidence: str):
    toks = numbers(ans)
    if not toks:
        return 1.0, []
    bad = [t for t in toks if not grounded(t, evidence)]
    return (len(toks) - len(bad)) / len(toks), bad


def strict_support(ans: str, evidence: str) -> float:
    j = _chat(JUDGE_MODEL,
              "아래 [발췌]에 **있는 내용만** 근거로, [답변]의 핵심 주장들이 뒷받침되는 정도를 0~1로 채점. "
              "발췌에 없는 주장(외부지식/추정)은 점수에서 깎는다. JSON {\"score\":0~1}",
              f"[발췌]\n{evidence[:6000]}\n\n[답변]\n{ans}")
    try:
        return float(j.get("score", 0))
    except Exception:
        return 0.0


def _chat(model, sysp, userp):
    r = cli().chat.completions.create(
        model=model, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": sysp}, {"role": "user", "content": userp}])
    return json.loads(r.choices[0].message.content)


def main():
    ctext = load_chunk_text()
    exp = json.loads(EXP.read_text(encoding="utf-8"))["per_item"]
    rows = []
    for it in exp:
        iid = it["id"]
        g_ans, n_ans = it.get("g_ans", ""), it.get("n_ans", "")

        q = it["question"]
        # --- Graph: 인용 청크(조단위 폴백) + 질문문 근거 ---
        cited = CITE_RE.findall(g_ans)
        g_evidence, unresolved = resolve_evidence(cited, ctext, q)
        g_numr, g_bad = numeric_grounding(g_ans, g_evidence)
        g_sup = strict_support(g_ans, g_evidence) if g_evidence.strip() else 0.0

        # --- Naive: 컨텍스트 재수집(예산 8 근사) + 질문문 근거 ---
        n_ctx = baseline_context(q, budget=8)
        n_evidence = q + "\n" + "\n".join(c.get("text", "") for c in n_ctx)
        n_numr, n_bad = numeric_grounding(n_ans, n_evidence)
        n_sup = strict_support(n_ans, n_evidence) if n_evidence.strip() else 0.0

        rows.append({
            "id": iid, "type": it["type"],
            "cited": cited, "bad_cites": unresolved,
            "g_numgrounding": round(g_numr, 3), "g_ungrounded_nums": g_bad,
            "g_strict_support": round(g_sup, 3),
            "n_numgrounding": round(n_numr, 3), "n_ungrounded_nums": n_bad,
            "n_strict_support": round(n_sup, 3),
            "old_faith_g": it.get("faith_g"), "old_faith_n": it.get("faith_n"),
        })
        print(f"[{iid:2}] {it['type']:10} | G numGrnd={g_numr:.2f} sup={g_sup:.2f} "
              f"unresolved={unresolved} ungrG={g_bad} | N numGrnd={n_numr:.2f} sup={n_sup:.2f}",
              flush=True)

    def avg(k):
        v = [r[k] for r in rows if r[k] is not None]
        return round(sum(v) / len(v), 3) if v else None

    print("\n=== 집계 ===")
    print(f"Graph 수치근거율 {avg('g_numgrounding'):.3f} | 엄격지지도 {avg('g_strict_support'):.3f} "
          f"(기존 faith {avg('old_faith_g'):.3f})")
    print(f"Naive 수치근거율 {avg('n_numgrounding'):.3f} | 엄격지지도 {avg('n_strict_support'):.3f} "
          f"(기존 faith {avg('old_faith_n'):.3f})")
    bad = [r["id"] for r in rows if r["bad_cites"] or r["g_ungrounded_nums"]]
    print(f"근거 미달(환각 의심) 문항: {bad}")
    # 17번 오염 가설 검증
    r17 = next(r for r in rows if r["id"] == 17)
    print(f"\n[17번 오염 검증] 인용={r17['cited']} 미존재인용={r17['bad_cites']} "
          f"미근거수치={r17['g_ungrounded_nums']} 수치근거율={r17['g_numgrounding']}")
    (ROOT / "eval" / "_factuality_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
