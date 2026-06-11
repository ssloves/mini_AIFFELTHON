"""REFERENCES 엣지 정합 검수 (결정론적 — 사람·API 불필요).

REFERENCES는 Chunk->Article(target_doc|article)로, 도착 Article이 코퍼스에 존재할 때만
엣지화된다(없으면 dangling으로 격리 → 그래프 오염 0). 본 스크립트는 다음을 수치화한다.

1) 코퍼스 내(internal+cross_corpus) 인용의 **해소율**(실제 Article 노드로 연결된 비율)
2) 미해소(dangling-in-corpus)의 **사유 분포**(target_doc별) + 예시
3) 해소된 엣지의 **항(項) 도달성**: 인용이 제N항을 지정했을 때, 그 항이 도착 조에 실제 존재하는 비율
   (정규식이 엉뚱한 조를 잡으면 항도 안 맞는 경향 → precision 보조 지표)

산출: graph/reference_audit.md (+ 콘솔 요약)
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def para_nums(label: str) -> set[int]:
    """'제1-2항'/'제3항'/'제1·3항' 등 항 라벨을 정수 집합으로 펼친다(범위 병합 대응)."""
    if not label:
        return set()
    out = set()
    for m in re.finditer(r"제(\d+)(?:\s*[-~]\s*(\d+))?", label):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.update(range(a, b + 1))
    return out
CHUNKS = ROOT / "data" / "chunks" / "all_chunks.jsonl"
OUT = ROOT / "graph" / "reference_audit.md"


def main():
    chunks = [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]

    # Article 노드 집합 + 조별 존재 항(項) 집합
    article_ids = set()
    paras_by_article = collections.defaultdict(set)
    for c in chunks:
        if c.get("article"):
            aid = f"{c['source_doc']}|{c['article']}"
            article_ids.add(aid)
            if c.get("paragraph"):
                paras_by_article[aid].update(para_nums(c["paragraph"]))

    raw_internal = raw_cross = 0
    resolved = dangling = 0
    dangling_by_doc = collections.Counter()
    dangling_examples = []
    para_specified = para_hit = 0

    for c in chunks:
        seen = set()
        for r in c.get("references", []):
            st = r.get("status")
            if st == "internal":
                raw_internal += 1
            elif st == "cross_corpus":
                raw_cross += 1
            if st not in ("internal", "cross_corpus"):
                continue
            tgt = f"{r['target_doc']}|{r['article']}"
            if tgt in seen:
                continue
            seen.add(tgt)
            if tgt in article_ids:
                resolved += 1
                # 항 도달성(인용이 항을 지정한 경우만)
                want = para_nums(r.get("paragraph", ""))
                if want:
                    para_specified += 1
                    if want & paras_by_article[tgt]:
                        para_hit += 1
            else:
                dangling += 1
                dangling_by_doc[r.get("target_doc")] += 1
                if len(dangling_examples) < 12:
                    dangling_examples.append((c["source_doc"], r.get("raw"), tgt))

    uniq = resolved + dangling
    res_rate = 100 * resolved / uniq if uniq else 0
    para_rate = 100 * para_hit / para_specified if para_specified else 0

    lines = []
    lines.append("# REFERENCES 엣지 정합 검수 (결정론적)\n")
    lines.append("> 인용은 정규식+문서 레지스트리로 해소하고, **도착 조문이 코퍼스에 실재할 때만**")
    lines.append("> 엣지화한다(없으면 dangling으로 격리 → 그래프 오염 0). 사람/LLM 없이 측정.\n")
    lines.append("## 1. 해소율 (코퍼스 내 인용)\n")
    lines.append(f"- 원시 인용: internal {raw_internal:,} + cross_corpus {raw_cross:,} = "
                 f"**{raw_internal + raw_cross:,}건**")
    lines.append(f"- 청크별 중복 도착 제거 후 고유 인용: **{uniq:,}건**")
    lines.append(f"- 실제 Article로 **해소(엣지화): {resolved:,}건 → 해소율 {res_rate:.1f}%**")
    lines.append(f"- 미해소(dangling, 코퍼스 내인데 도착 조문 없음): {dangling:,}건 "
                 f"({100 - res_rate:.1f}%) — 엣지로 만들지 않고 격리\n")
    lines.append("## 2. 해소 엣지의 항(項) 도달성 (precision 보조 지표)\n")
    lines.append(f"- 인용이 '제N항'까지 지정한 해소 엣지: {para_specified:,}건")
    lines.append(f"- 그중 해당 항이 도착 조에 실제 존재: **{para_hit:,}건 → {para_rate:.1f}%** "
                 f"(정규식이 엉뚱한 조를 잡았다면 항도 불일치하는 경향)\n")
    lines.append("## 3. 미해소(dangling) 사유 분포 — target_doc별\n")
    for doc, n in dangling_by_doc.most_common():
        lines.append(f"- {doc}: {n:,}건")
    lines.append("\n**미해소 예시(원문 인용 표기):**\n")
    for sd, raw, tgt in dangling_examples:
        lines.append(f"- [{sd}] `{raw}` → {tgt} (도착 조문 부재)")
    lines.append("\n## 4. 해석\n")
    lines.append(f"- 해소된 {resolved:,}개 엣지는 **모두 코퍼스에 실재하는 조문**을 가리킨다"
                 "(존재하지 않는 조문으로 가는 엣지는 0 → 그래프 오염 없음).")
    lines.append(f"- 항 도달성 {para_rate:.1f}%는 조 단위 해소가 항 수준에서도 대체로 정합함을 시사.")
    lines.append("- 남는 한계: '도착 조가 우연히 존재하나 문맥상 다른 법을 의도'한 오연결은 "
                 "결정론적으로 잡히지 않는다 → 발표 시 소표본 육안 점검으로 보완 권장.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[DONE] -> {OUT}")
    print(f"  해소율 {res_rate:.1f}% ({resolved:,}/{uniq:,}), dangling {dangling:,}")
    print(f"  항 도달성 {para_rate:.1f}% ({para_hit:,}/{para_specified:,})")


if __name__ == "__main__":
    main()
