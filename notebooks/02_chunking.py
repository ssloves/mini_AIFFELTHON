"""Step 2 — 법률 구조 인식 청킹 → data/chunks/all_chunks.jsonl

규칙(가이드 §3.2 + EDA 실측 반영):
- 법령: '항' 단위. <300토큰 항은 같은 조 내 인접 항과 병합, >1500토큰 항은 호 단위 분할.
  각 청크에 상위 조 제목을 컨텍스트로 첨부.
- 지침: 섹션 + 페이지 단위. 토큰 상한(1000) 초과 시 분할, 표는 개별 청크.
- 각 청크: 타입드 references, concept keywords, defines 포함.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

import legal_common as lc

OUT = lc.ROOT / "data" / "chunks" / "all_chunks.jsonl"
MERGE_MIN = 300
SPLIT_MAX = 1500
GUIDE_CAP = 1000

RE_HO_LINE = re.compile(r"(?m)^- \d+\. ")


def ref_dicts(source_doc, text):
    seen = set()
    out = []
    for r in lc.extract_references(source_doc, text):
        key = (r.target_doc, r.target_law_name, r.article, r.paragraph)
        if key in seen:
            continue
        seen.add(key)
        out.append({"raw": r.raw, "article": r.article, "paragraph": r.paragraph,
                    "target_doc": r.target_doc, "target_law": r.target_law_name,
                    "status": r.status})
    return out


def make_chunk(doc, abbrev, chapter, article, article_title, para_label, page, text, section):
    ctx = " > ".join(x for x in [lc.REGISTRY[doc].abbrev, chapter, article, article_title] if x)
    hp = " > ".join(x for x in [doc, chapter, article, para_label] if x)
    cid = "_".join(x for x in [abbrev, article, para_label] if x).replace(" ", "")
    if not cid or cid == abbrev:
        cid = f"{abbrev}_{section}_{page}"
    return {
        "chunk_id": cid,
        "source_doc": doc,
        "doc_type": lc.REGISTRY[doc].doc_type,
        "chapter": chapter,
        "article": article,
        "article_title": article_title,
        "paragraph": para_label,
        "hierarchy_path": hp,
        "page": page,
        "section": section,
        "context_header": ctx,
        "text": text.strip(),
        "token_count": lc.n_tokens(text),
        "references": ref_dicts(doc, text),
        "keywords": lc.concept_hits(text),
        "defines": lc.defined_concepts(text),
    }


def chunk_law(key):
    recs = lc.parse_law_md(key)
    abbrev = lc.REGISTRY[key].abbrev
    chunks = []
    # 조 단위로 그룹
    i = 0
    n = len(recs)
    while i < n:
        r = recs[i]
        toks = lc.n_tokens(r.text)
        # 큰 항 → 호 단위 분할
        if toks > SPLIT_MAX and RE_HO_LINE.search(r.text):
            chunks.extend(split_by_ho(key, abbrev, r))
            i += 1
            continue
        # 작은 항 → 같은 조 인접 항 병합
        if toks < MERGE_MIN:
            merged_text = r.text
            labels = [r.paragraph] if r.paragraph else []
            j = i + 1
            while (j < n and recs[j].article == r.article
                   and recs[j].section == r.section
                   and lc.n_tokens(merged_text) < MERGE_MIN):
                merged_text += "\n" + recs[j].text
                if recs[j].paragraph:
                    labels.append(recs[j].paragraph)
                j += 1
            label = merge_label(labels)
            chunks.append(make_chunk(key, abbrev, r.chapter, r.article, r.article_title,
                                     label, r.page, merged_text, r.section))
            i = j
            continue
        chunks.append(make_chunk(key, abbrev, r.chapter, r.article, r.article_title,
                                 r.paragraph, r.page, r.text, r.section))
        i += 1
    return chunks


def merge_label(labels):
    nums = [re.search(r"\d+", x).group() for x in labels if x and re.search(r"\d+", x)]
    if not nums:
        return None
    if len(nums) == 1:
        return f"제{nums[0]}항"
    return f"제{nums[0]}-{nums[-1]}항"


def split_by_ho(key, abbrev, r):
    """큰 항을 '항 머리말 + 호'들로 분할. 각 호 청크에 항 머리말 컨텍스트 첨부."""
    lines = r.text.split("\n")
    head = []
    blocks = []
    cur = None
    for ln in lines:
        if re.match(r"^- \d+\. ", ln):
            if cur is not None:
                blocks.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
        else:
            head.append(ln)
    if cur is not None:
        blocks.append(cur)
    head_txt = "\n".join(head).strip()
    out = []
    for bi, blk in enumerate(blocks, 1):
        body = (head_txt + "\n" + "\n".join(blk)).strip()
        label = (r.paragraph or "") + f" 호{bi}"
        out.append(make_chunk(key, abbrev, r.chapter, r.article, r.article_title,
                              label.strip(), r.page, body, r.section))
    return out


# ---------------------------------------------------------------------------
# 지침 청킹 (섹션 + 페이지 + 토큰상한, 표는 개별)
# ---------------------------------------------------------------------------
def chunk_guideline(key="장려금지침"):
    path = lc.PROCESSED / f"{key}.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        lines = lines[end + 1:]
    abbrev = lc.REGISTRY[key].abbrev
    chunks = []
    h2 = h3 = None
    page = None
    buf = []
    table_buf = []
    in_table = False
    seq = 0

    def sec_path():
        return " > ".join(x for x in [h2, h3] if x)

    def flush_text():
        nonlocal buf, seq
        txt = "\n".join(buf).strip()
        buf = []
        if not txt:
            return
        seq += 1
        c = make_chunk(key, abbrev, None, None, None, None, page, txt, "지침")
        c["chunk_id"] = f"{abbrev}_p{page}_s{seq}"
        c["section_path"] = sec_path()
        c["hierarchy_path"] = f"{key} > {sec_path()}"
        chunks.append(c)

    def flush_table():
        nonlocal table_buf, seq
        if not table_buf:
            return
        txt = "\n".join(table_buf).strip()
        table_buf = []
        seq += 1
        c = make_chunk(key, abbrev, None, None, None, None, page, txt, "지침표")
        c["chunk_id"] = f"{abbrev}_p{page}_t{seq}"
        c["section_path"] = sec_path()
        c["hierarchy_path"] = f"{key} > {sec_path()} > [표]"
        chunks.append(c)

    for ln in lines:
        m = lc.RE_PAGE.search(ln)
        if m:
            flush_text()
            page = int(m.group(1))
            continue
        s = ln.rstrip()
        if not s.strip():
            continue
        if s.startswith("# ") or s.startswith("> "):
            continue
        if s.startswith("**[표"):
            flush_text(); flush_table(); in_table = True; table_buf = [s]; continue
        if s.startswith("|"):
            in_table = True; table_buf.append(s); continue
        if in_table and not s.startswith("|"):
            flush_table(); in_table = False
        if s.startswith("## "):
            flush_text(); h2 = s[3:].strip(); h3 = None; continue
        if s.startswith("### "):
            flush_text(); h3 = s[4:].strip(); continue
        buf.append(s)
        if lc.n_tokens("\n".join(buf)) >= GUIDE_CAP:
            flush_text()
    flush_text(); flush_table()
    return chunks


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_chunks = []
    for key, info in lc.REGISTRY.items():
        if info.doc_type == "행정지침":
            cs = chunk_guideline(key)
        else:
            cs = chunk_law(key)
        all_chunks.extend(cs)
        print(f"{key}: {len(cs)} chunks")
    # chunk_id 충돌 방지(병합/분할로 중복 가능) — 접미사
    seen = {}
    for c in all_chunks:
        cid = c["chunk_id"]
        if cid in seen:
            seen[cid] += 1
            c["chunk_id"] = f"{cid}#{seen[cid]}"
        else:
            seen[cid] = 0
    with OUT.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\nTOTAL {len(all_chunks)} chunks -> {OUT}")
    # 요약 통계
    toks = [c["token_count"] for c in all_chunks]
    import statistics as st
    print(f"token median={int(st.median(toks))} mean={int(st.mean(toks))} max={max(toks)}")
    print(f"refs total={sum(len(c['references']) for c in all_chunks)}")


if __name__ == "__main__":
    main()
