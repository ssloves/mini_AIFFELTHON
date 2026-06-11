"""Step 1 — PDF → 구조화 Markdown 정제 파이프라인.

가이드 §2 요구사항 + 추가 정제 전략을 구현한다.

[가이드 요구사항]
- 조-항-호-목 위계를 마크다운 헤딩/리스트로 매핑 (#/##/###/####/-)
- YAML frontmatter 로 문서 메타데이터 보존(법령명/번호/시행일/개정구분)
- 개정일자 태그(<개정 ...>) 보존
- 부칙/별표를 별도 섹션으로 분리
- 행정지침(장려금지침)은 장/절/유형 + 요건블록 구조, 별첨 별도

[추가 정제 전략 — 가이드에 없던 개선점]
1. 엔진 선택: pdfplumber(본문, 「」/원문자 보존 우수) + 표는 extract_tables.
   장려금지침 표지(1p)는 CID 폰트라 추출 불가 → 스킵.
2. 머리글/바닥글 제거: 반복되는 문서 제목, '법제처/국가법령정보센터', 페이지번호.
3. 논리 줄 복원(de-wrapping): 물리적으로 줄바꿈된 한 항/호를 하나의 논리 단위로
   병합 → '제127조' 같은 참조나 수치가 줄경계에서 끊기지 않음(Step2 참조/청킹 보호).
4. 페이지 앵커(<!-- page: N -->) 보존: QA gold_source가 지침을 페이지(p.13 등)로
   인용 → eval 단계 근거 매칭에 직접 활용.
5. 개정 태그 + 삭제 조항('삭제<...>') 보존: 조 번호 누락 오인 방지 + 시간 유효성.
"""
from __future__ import annotations

import re
import sys
import io
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw_data"
OUT_DIR = ROOT / "data" / "processed"

# ---------------------------------------------------------------------------
# 마커 정의
# ---------------------------------------------------------------------------
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿"
CIRCLED_TO_NUM = {ch: i + 1 for i, ch in enumerate(CIRCLED)}

# 목(目) 한글 순서표 (가, 나, 다 ...)
MOK_CHARS = list("가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허")
MOK_SET = set(MOK_CHARS)

RE_ARTICLE = re.compile(r"^(제\d+조(?:의\d+)?)(?:\(([^)]*)\))?\s*(.*)$")
RE_CHAPTER = re.compile(r"^(제\d+(?:편|장|절|관))\s+(.+)$")
RE_HO = re.compile(r"^(\d{1,3})\.\s*(?=[^\d])(.*)$")  # 호: 1. ~ (4자리 연도 제외)
RE_YEAR_CONT = re.compile(r"^\d{4}\.")  # 줄바꿈된 개정 날짜 목록(연속행)
RE_REV_TAG = re.compile(r"<(?:개정|신설|본조신설|전문개정|제목개정)[^>]*>")
RE_REF_ARTICLE = re.compile(r"제\d+조(?:의\d+)?(?:제\d+항)?")
RE_REF_LAW = re.compile(r"[「『]([^」』]+)[」』]")


def is_circled(line: str) -> bool:
    return bool(line) and line[0] in CIRCLED_TO_NUM


def is_mok(line: str) -> bool:
    # '가. ' / '가) ' 형태, 첫 글자가 목 순서표에 있어야 함
    return len(line) >= 2 and line[0] in MOK_SET and line[1] in ".)"


# ---------------------------------------------------------------------------
# 페이지 텍스트 정제 (머리글/바닥글 제거)
# ---------------------------------------------------------------------------
def clean_lines(raw_text: str, doc_title: str) -> list[str]:
    out = []
    for ln in raw_text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s == doc_title:  # 반복 머리글
            continue
        if "법제처" in s and "국가법령정보센터" in s:  # 바닥글
            continue
        if re.fullmatch(r"-?\s*\d+\s*-?", s):  # 페이지 번호 단독
            continue
        out.append(s)
    return out


def parse_front_meta(first_page_text: str) -> dict:
    """1페이지의 [시행 ...] [법률/대통령령 제N호, 날짜, 구분] 파싱."""
    meta = {}
    m = re.search(r"\[시행\s*([0-9.\s]+?)\]", first_page_text)
    if m:
        meta["enforcement_date"] = m.group(1).strip().rstrip(".")
    m = re.search(r"\[((?:법률|대통령령))\s*(제\d+호)[,，]\s*([0-9.\s]+?)[,，]\s*([^\]]+)\]", first_page_text)
    if m:
        meta["law_kind"] = m.group(1)
        meta["law_number"] = f"{m.group(1)} {m.group(2)}"
        meta["promulgation_date"] = m.group(3).strip().rstrip(".")
        meta["revision_type"] = m.group(4).strip()
    return meta


# ---------------------------------------------------------------------------
# 논리 단위 (de-wrapping 결과)
# ---------------------------------------------------------------------------
@dataclass
class Unit:
    kind: str          # chapter/article/para/ho/mok/note/buchik/byeolpyo/text
    text: str
    page: int
    num: int | None = None     # 항/호 번호
    art_no: str | None = None  # 조 번호
    art_title: str | None = None
    mok_char: str | None = None


def classify(line: str):
    """라인의 선두 마커로 새 논리 단위 종류 판별. 연속행이면 None."""
    if line.startswith("부칙") or re.match(r"^부\s*칙", line):
        return ("buchik", None)
    if re.match(r"^\[?별표|^\[?별지|^\[?서식", line):
        return ("byeolpyo", None)
    mc = RE_CHAPTER.match(line)
    if mc:
        return ("chapter", mc)
    ma = RE_ARTICLE.match(line)
    if ma and not RE_YEAR_CONT.match(line):
        return ("article", ma)
    if is_circled(line):
        return ("para", None)
    if RE_YEAR_CONT.match(line):
        return (None, None)  # 줄바꿈된 개정 날짜 → 연속행
    mh = RE_HO.match(line)
    if mh:
        return ("ho", mh)
    if is_mok(line):
        return ("mok", None)
    if re.match(r"^[\[<]", line):  # [본조신설 ...], [제목개정 ...] 등 단독 주석
        return ("note", None)
    return (None, None)


def build_units(lines: list[tuple[int, str]]) -> list[Unit]:
    """(page, line) 리스트 → 논리 단위 리스트 (연속행 병합)."""
    units: list[Unit] = []
    cur: Unit | None = None
    cur_art: str | None = None
    cur_art_title: str | None = None

    def flush():
        nonlocal cur
        if cur is not None:
            cur.text = re.sub(r"\s+", " ", cur.text).strip()
            units.append(cur)
            cur = None

    for page, line in lines:
        # 닫히지 않은 <개정 ...> 태그가 줄바꿈된 경우: 마커처럼 보여도 연속행
        if cur is not None and cur.text.count("<") > cur.text.count(">"):
            cur.text += " " + line
            continue
        kind, m = classify(line)
        if kind is None:
            # 연속행 → 직전 단위에 병합
            if cur is not None:
                cur.text += " " + line
            else:
                cur = Unit("text", line, page)
            continue

        flush()
        if kind == "chapter":
            cur = Unit("chapter", m.group(2), page, art_no=m.group(1))
        elif kind == "article":
            art_no, art_title, rest = m.group(1), m.group(2), m.group(3)
            cur_art, cur_art_title = art_no, art_title
            # 조 제목줄 뒤에 본문/첫 항이 붙어있는 경우
            if rest and is_circled(rest):
                # 조 헤딩만 단위로, 첫 항은 rest 를 재투입
                units.append(Unit("article", "", page, art_no=art_no, art_title=art_title))
                n = CIRCLED_TO_NUM[rest[0]]
                cur = Unit("para", rest[1:].strip(), page, num=n, art_no=art_no)
            else:
                cur = Unit("article", rest, page, art_no=art_no, art_title=art_title)
        elif kind == "para":
            n = CIRCLED_TO_NUM[line[0]]
            cur = Unit("para", line[1:].strip(), page, num=n, art_no=cur_art)
        elif kind == "ho":
            cur = Unit("ho", m.group(2).strip(), page, num=int(m.group(1)), art_no=cur_art)
        elif kind == "mok":
            cur = Unit("mok", line[2:].strip(), page, mok_char=line[0], art_no=cur_art)
        elif kind == "note":
            cur = Unit("note", line, page, art_no=cur_art)
        elif kind == "buchik":
            cur = Unit("buchik", line, page)
        elif kind == "byeolpyo":
            cur = Unit("byeolpyo", line, page)
    flush()
    return units


# ---------------------------------------------------------------------------
# 마크다운 렌더링 (법령)
# ---------------------------------------------------------------------------
def render_law_md(units: list[Unit], meta: dict) -> str:
    lines = [frontmatter(meta), ""]
    lines.append(f"# {meta['doc_name']}")
    lines.append("")

    section = "body"  # body / buchik / byeolpyo
    last_page = None

    def page_anchor(p):
        nonlocal last_page
        if p != last_page:
            lines.append(f"<!-- page: {p} -->")
            last_page = p

    for u in units:
        if u.kind == "buchik" and section != "buchik":
            section = "buchik"
            lines.append("\n## 부칙\n")
        if u.kind == "byeolpyo" and section != "byeolpyo":
            section = "byeolpyo"
            lines.append("\n## 별표·별지·서식\n")

        if u.kind == "chapter":
            page_anchor(u.page)
            lines.append(f"\n## {u.art_no} {u.text}\n")
        elif u.kind == "article":
            page_anchor(u.page)
            title = f"({u.art_title})" if u.art_title else ""
            lines.append(f"\n### {u.art_no}{title}\n")
            if u.text:
                lines.append(u.text)
        elif u.kind == "para":
            lines.append(f"\n#### 제{u.num}항")
            lines.append(u.text)
        elif u.kind == "ho":
            lines.append(f"- {u.num}. {u.text}")
        elif u.kind == "mok":
            lines.append(f"  - {u.mok_char}. {u.text}")
        elif u.kind in ("note", "buchik", "byeolpyo"):
            lines.append(u.text)
        else:  # text
            lines.append(u.text)
    return "\n".join(lines) + "\n"


def frontmatter(meta: dict) -> str:
    keys = ["doc_name", "doc_type", "law_kind", "law_number", "enforcement_date",
            "promulgation_date", "revision_type", "source_pdf", "parsed_with", "page_count"]
    out = ["---"]
    for k in keys:
        if k in meta and meta[k] is not None:
            out.append(f"{k}: {meta[k]}")
    out.append("---")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 법령 문서 파이프라인
# ---------------------------------------------------------------------------
def process_law(pdf_path: Path, doc_name: str, doc_title: str, doc_type: str) -> str:
    page_lines: list[tuple[int, str]] = []
    first_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            if i == 0:
                first_text = txt
            for ln in clean_lines(txt, doc_title):
                page_lines.append((i + 1, ln))

    meta = {"doc_name": doc_name, "doc_type": doc_type,
            "source_pdf": pdf_path.name, "parsed_with": "pdfplumber",
            "page_count": n}
    meta.update(parse_front_meta(first_text))

    # 1페이지 메타데이터 라인/연락처 라인 제거 (본문 시작 전까지)
    page_lines = drop_preamble(page_lines)

    units = build_units(page_lines)
    return render_law_md(units, meta)


def drop_preamble(page_lines):
    """첫 '제1조' 이전의 시행정보/부처 연락처 라인 제거."""
    for idx, (_, ln) in enumerate(page_lines):
        if RE_ARTICLE.match(ln) or RE_CHAPTER.match(ln):
            return page_lines[idx:]
    return page_lines


# ---------------------------------------------------------------------------
# 행정지침(장려금지침) 파이프라인 — 장/절/요건블록 + 표 + 페이지 앵커
# ---------------------------------------------------------------------------
RE_G_TYPE = re.compile(r"^유형\s*[ⅠⅡⅢ]")
RE_G_CHAP = re.compile(r"^(\d+)\s*장[\s.]")
RE_G_SEC = re.compile(r"^(\d+-\d+)\s+(.+)$")  # 1-4 근로조건 등
RE_G_REQ = re.compile(r"^[➀-➓❶-❿①-⑮]")
RE_BYEOLCHEOM = re.compile(r"\[?별첨\s*\d+\]?")


def process_guideline(pdf_path: Path, doc_name: str) -> str:
    meta = {"doc_name": doc_name, "doc_type": "행정지침",
            "source_pdf": pdf_path.name, "parsed_with": "pdfplumber(text)+extract_tables",
            "page_count": None}
    body = []
    with pdfplumber.open(pdf_path) as pdf:
        meta["page_count"] = len(pdf.pages)
        body.append(frontmatter(meta))
        body.append("")
        body.append(f"# {doc_name}")
        body.append("")
        body.append("> 행정지침: 조-항-호 위계 대신 유형/장/절 + 요건블록(➀❶) 구조. "
                     "표는 마크다운 표로, 별첨은 참조([별첨N])로 보존. 페이지 앵커 유지.")
        for i, page in enumerate(pdf.pages):
            if i == 0:
                continue  # 표지(CID 폰트, 추출 불가)
            pageno = i + 1
            text = page.extract_text() or ""
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            # 페이지 번호 단독/머리글 제거
            lines = [l for l in lines
                     if not re.fullmatch(r"-?\s*\d+\s*-?", l)
                     and l != doc_name]
            if not lines and not page.find_tables():
                continue
            body.append(f"\n<!-- page: {pageno} -->")
            body.extend(render_guideline_line(lines))
            # 표 추출
            for ti, tbl in enumerate(page.extract_tables()):
                md = table_to_md(tbl)
                if md:
                    body.append(f"\n**[표 p{pageno}-{ti+1}]**\n")
                    body.append(md)
    return "\n".join(body) + "\n"


def render_guideline_line(lines):
    out = []
    for l in lines:
        if RE_G_TYPE.match(l):
            out.append(f"\n## {l}\n")
        elif RE_G_CHAP.match(l):
            out.append(f"\n## {l}\n")
        elif RE_G_SEC.match(l):
            out.append(f"\n### {l}\n")
        elif RE_G_REQ.match(l):
            out.append(f"- {l}")
        elif l.startswith("○"):
            out.append(f"- {l.lstrip('○').strip()}")
        elif l.startswith("√") or l.startswith("*"):
            out.append(f"  - {l.lstrip('√*').strip()}")
        else:
            out.append(l)
    return out


def table_to_md(tbl: list[list]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in tbl if any(row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    md = ["| " + " | ".join(header) + " |",
          "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def find(*kw):
    for p in RAW.glob("*.pdf"):
        if all(k in p.name for k in kw):
            return p
    return None


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("law", find("고용보험법", "시행령"), "고용보험법 시행령", "고용보험법 시행령", "시행령", "고용보험법시행령"),
        ("law", find("근로기준법", "시행령"), "근로기준법 시행령", "근로기준법 시행령", "시행령", "근로기준법시행령"),
        ("law", find("조세특례제한법", "시행령"), "조세특례제한법 시행령", "조세특례제한법 시행령", "시행령", "조특법시행령"),
        ("law", find("조세특례제한법", "법률"), "조세특례제한법", "조세특례제한법", "법률", "조특법"),
        ("guide", find("도약장려금"), "2025년 청년일자리도약장려금 사업운영 지침", None, "행정지침", "장려금지침"),
    ]
    for kind, path, doc_name, title, doc_type, out_name in jobs:
        if path is None:
            print(f"SKIP (not found): {out_name}")
            continue
        print(f"Processing [{kind}] {out_name} <- {path.name}")
        if kind == "law":
            md = process_law(path, doc_name, title, doc_type)
        else:
            md = process_guideline(path, doc_name)
        out_path = OUT_DIR / f"{out_name}.md"
        out_path.write_text(md, encoding="utf-8")
        print(f"  -> {out_path}  ({len(md):,} chars)")


if __name__ == "__main__":
    main()
