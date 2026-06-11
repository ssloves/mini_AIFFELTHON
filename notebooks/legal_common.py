"""Step 2 공용 모듈.

- 문서 레지스트리 (5개 문서 + 상위법/시행령 관계)
- 정제 마크다운(data/processed/*.md) 파서 → 항(paragraph) 단위 레코드
- 타입드 참조 해소기 (internal / cross-corpus / external-dangling)
- 토크나이저 (tiktoken cl100k_base)
- Concept 인벤토리 + 정의(DEFINES) 패턴 탐지
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"

ENC = tiktoken.get_encoding("cl100k_base")


def n_tokens(text: str) -> int:
    return len(ENC.encode(text))


# ---------------------------------------------------------------------------
# 문서 레지스트리
# ---------------------------------------------------------------------------
@dataclass
class DocInfo:
    key: str          # 파일명(=processed md stem)
    abbrev: str       # 짧은 약칭(chunk_id용)
    doc_type: str     # 법률/시행령/행정지침
    parent_law: str | None   # 시행령의 상위 법률명(코퍼스에 있을 수도/없을 수도)
    parent_in_corpus: str | None  # 상위법이 코퍼스에 있으면 그 key


REGISTRY: dict[str, DocInfo] = {
    "고용보험법시행령": DocInfo("고용보험법시행령", "고보령", "시행령", "고용보험법", None),
    "근로기준법시행령": DocInfo("근로기준법시행령", "근기령", "시행령", "근로기준법", None),
    "조특법시행령": DocInfo("조특법시행령", "조특령", "시행령", "조세특례제한법", "조특법"),
    "조특법": DocInfo("조특법", "조특법", "법률", None, None),
    "장려금지침": DocInfo("장려금지침", "지침", "행정지침", None, None),
}

# 「법령명」 → 코퍼스 문서 key 매핑 (외부면 None)
NAME_TO_KEY = {
    "조세특례제한법 시행령": "조특법시행령",
    "조세특례제한법시행령": "조특법시행령",
    "조세특례제한법": "조특법",
    "고용보험법 시행령": "고용보험법시행령",
    "고용보험법시행령": "고용보험법시행령",
    "근로기준법 시행령": "근로기준법시행령",
    "근로기준법시행령": "근로기준법시행령",
}


# ---------------------------------------------------------------------------
# 정제 MD 파서 (법령)
# ---------------------------------------------------------------------------
@dataclass
class ParaRecord:
    source_doc: str
    chapter: str | None
    article: str | None
    article_title: str | None
    paragraph: str | None       # '제N항' or None(단일 항/조문 본문)
    page: int | None
    section: str                # body/부칙/별표
    text: str
    ho_count: int = 0


RE_PAGE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
RE_H2_CHAP = re.compile(r"^##\s+(제\d+(?:편|장|절|관)\s+.+)$")
RE_H3_ART = re.compile(r"^###\s+(제\d+조(?:의\d+)?)(?:\(([^)]*)\))?\s*$")
RE_H4_PARA = re.compile(r"^####\s+(제\d+항)\s*$")


def parse_law_md(key: str) -> list[ParaRecord]:
    path = PROCESSED / f"{key}.md"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # frontmatter 제거
    if lines and lines[0].strip() == "---":
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        lines = lines[end + 1:]

    records: list[ParaRecord] = []
    chapter = article = article_title = None
    page = None
    section = "body"
    cur: ParaRecord | None = None

    def flush():
        nonlocal cur
        if cur is not None:
            cur.text = cur.text.strip()
            cur.ho_count = len(re.findall(r"(?m)^- \d+\. ", cur.text))
            if cur.text:
                records.append(cur)
        cur = None

    for ln in lines:
        m = RE_PAGE.search(ln)
        if m:
            page = int(m.group(1))
            continue
        s = ln.rstrip()
        if not s.strip():
            continue
        if s.startswith("## 부칙"):
            flush(); section = "부칙"; chapter = article = None; continue
        if s.startswith("## 별표"):
            flush(); section = "별표"; chapter = article = None; continue
        mc = RE_H2_CHAP.match(s)
        if mc:
            flush(); chapter = mc.group(1); continue
        ma = RE_H3_ART.match(s)
        if ma:
            flush()
            article, article_title = ma.group(1), ma.group(2)
            cur = ParaRecord(key, chapter, article, article_title, None, page, section, "")
            continue
        mp = RE_H4_PARA.match(s)
        if mp:
            flush()
            cur = ParaRecord(key, chapter, article, article_title, mp.group(1), page, section, "")
            continue
        # 본문/호/목/주석 라인
        if cur is None:
            cur = ParaRecord(key, chapter, article, article_title, None, page, section, "")
        cur.text += ("\n" if cur.text else "") + s
    flush()
    return records


# ---------------------------------------------------------------------------
# 지침 파서 (섹션 단위)
# ---------------------------------------------------------------------------
@dataclass
class SectionRecord:
    source_doc: str
    section_path: str           # 누적 헤딩 경로
    page: int | None
    text: str
    has_table: bool = False


def parse_guideline_md(key: str = "장려금지침") -> list[SectionRecord]:
    path = PROCESSED / f"{key}.md"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        lines = lines[end + 1:]

    records: list[SectionRecord] = []
    h2 = h3 = None
    page = None
    cur: SectionRecord | None = None

    def flush():
        nonlocal cur
        if cur is not None and cur.text.strip():
            records.append(cur)
        cur = None

    def path_str():
        return " > ".join(x for x in [h2, h3] if x)

    for ln in lines:
        m = RE_PAGE.search(ln)
        if m:
            page = int(m.group(1)); continue
        s = ln.rstrip()
        if not s.strip():
            continue
        if s.startswith("# "):
            continue
        if s.startswith("> "):
            continue
        if s.startswith("## "):
            flush(); h2 = s[3:].strip(); h3 = None
            cur = SectionRecord(key, path_str(), page, "")
            continue
        if s.startswith("### "):
            flush(); h3 = s[4:].strip()
            cur = SectionRecord(key, path_str(), page, "")
            continue
        if cur is None:
            cur = SectionRecord(key, path_str(), page, "")
        if s.startswith("|") or s.startswith("**[표"):
            cur.has_table = True
        cur.text += ("\n" if cur.text else "") + s
    flush()
    return records


# ---------------------------------------------------------------------------
# 타입드 참조 해소
# ---------------------------------------------------------------------------
RE_REF = re.compile(r"제(\d+)조(?:의(\d+))?(?:제(\d+)항)?")
RE_LAW_BRACKET = re.compile(r"[「『]([^」』]+)[」』]")


@dataclass
class Reference:
    raw: str                # 제29조의8제6항
    article: str            # 제29조의8
    paragraph: str | None   # 제6항
    target_doc: str | None  # 코퍼스 내 key or None
    target_law_name: str | None  # external 법령명 or None
    status: str             # internal / cross_corpus / external


def _resolve_named(name: str) -> tuple[str | None, str | None]:
    name = name.strip()
    for k, v in NAME_TO_KEY.items():
        if k in name:
            return v, None
    return None, name  # external


def extract_references(source_doc: str, text: str) -> list[Reference]:
    refs: list[Reference] = []
    info = REGISTRY[source_doc]
    for m in RE_REF.finditer(text):
        art = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        para = f"제{m.group(3)}항" if m.group(3) else None
        raw = m.group(0)
        start = m.start()
        pre = text[max(0, start - 25):start]

        target_doc = None
        target_law = None
        status = "internal"

        bracket = list(RE_LAW_BRACKET.finditer(pre))
        if bracket and (start - bracket[-1].end()) <= 12 and "조" not in pre[bracket[-1].end():]:
            target_doc, target_law = _resolve_named(bracket[-1].group(1))
            status = "cross_corpus" if target_doc else "external"
        elif re.search(r"(?:동법|같은\s*법)\s*시행령\s*$", pre):
            # 동법 시행령 → 상위 법률의 시행령
            if source_doc == "조특법":
                target_doc, status = "조특법시행령", "cross_corpus"
            else:
                target_law, status = (info.parent_law or "") + " 시행령", "external"
        elif re.search(r"이\s*영\s*$|같은\s*영\s*$", pre):
            target_doc, status = source_doc, "internal"
        elif re.search(r"(?:^|[^시행])법\s*$|이\s*법\s*$|같은\s*법\s*$|동법\s*$", pre):
            # 맨 '법' → 상위 법률
            if info.parent_in_corpus:
                target_doc, status = info.parent_in_corpus, "cross_corpus"
            elif info.parent_law:
                target_law, status = info.parent_law, "external"
            else:  # 법률 자신
                target_doc, status = source_doc, "internal"
        else:
            target_doc, status = source_doc, "internal"

        refs.append(Reference(raw, art, para, target_doc, target_law, status))
    return refs


# ---------------------------------------------------------------------------
# Concept 인벤토리 + 정의 탐지
# ---------------------------------------------------------------------------
# (정식명, [별칭...])
CONCEPTS: dict[str, list[str]] = {
    "상시근로자": ["상시근로자", "상시 근로자", "상시 사용하는 근로자"],
    "청년등상시근로자": ["청년등상시근로자", "청년 상시근로자"],
    "청년": ["청년"],
    "단시간근로자": ["단시간근로자", "단시간 근로자", "초단시간"],
    "피보험자": ["피보험자"],
    "일용근로자": ["일용근로자", "일용근로"],
    "기간제근로자": ["기간제근로자", "기간제 근로자", "기간을 정한 근로계약", "기간의 정함이 없는 근로계약"],
    "소정근로시간": ["소정근로시간", "소정 근로시간"],
    "통합고용세액공제": ["통합고용세액공제"],
    "도약장려금": ["도약장려금", "청년일자리도약장려금"],
    "고용유지지원금": ["고용유지지원금", "고용유지조치"],
    "중소기업": ["중소기업", "중견기업"],
    "우선지원대상기업": ["우선지원대상기업"],
    "임원": ["임원"],
    "친족": ["친족", "직계존비속", "최대주주", "최대출자자", "배우자"],
    "출산전후휴가": ["출산전후휴가", "출산휴가"],
    "사후관리추징": ["추징", "사후관리", "납부하여야"],
    "평균임금": ["평균임금"],
    "통상임금": ["통상임금"],
    "가동일수": ["가동일수", "연인원"],
}

# 정의문 패턴: X"란 … / X의 범위 / X … 말한다·본다·로 한다
# (닫는 따옴표 케이스 `상시근로자"란` 포함, 재현율 우선 — 오탐은 격리 LLM에서 필터)
DEF_PATTERNS = [
    r'{C}["“”』」]?\s*(?:이란|란|이라\s*한다)',
    r'{C}\s*(?:의 범위)',
    r'{C}["“”』」]?[^.\n]{{0,55}}(?:말한다|로 본다|으로 본다|로 한다)',
]


def concept_hits(text: str) -> list[str]:
    hits = []
    for canon, aliases in CONCEPTS.items():
        if any(a in text for a in aliases):
            hits.append(canon)
    return hits


def defined_concepts(text: str) -> list[str]:
    """이 텍스트가 '정의'하는 개념 목록(DEFINES 후보)."""
    out = []
    for canon, aliases in CONCEPTS.items():
        for a in aliases:
            ca = re.escape(a)
            for pat in DEF_PATTERNS:
                if re.search(pat.replace("{C}", ca), text):
                    out.append(canon)
                    break
            if canon in out:
                break
    return out


# 수치/단위 추출 (충돌 스코어용)
RE_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(시간|개월|개월간|세|명|년|일|퍼센트|%|배|시간 이상|시간 미만)")


def numeric_signals(text: str) -> set[str]:
    return {f"{m.group(1)}{m.group(2)}" for m in RE_NUM_UNIT.finditer(text)}


if __name__ == "__main__":
    # 간단 자가검증
    for k in REGISTRY:
        if REGISTRY[k].doc_type == "행정지침":
            recs = parse_guideline_md(k)
        else:
            recs = parse_law_md(k)
        print(f"{k}: {len(recs)} records")
