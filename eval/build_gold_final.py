"""gold_set_v2 -> gold_set_final 결정론 큐레이션.

목적: v2(80문항)에 대해 사람(변호사 관점) G4 전수 검수에서 확정한 정정을
*결정론적으로* 반영한다. v2를 재생성하지 않고(=ID 재배정/비결정성 회피),
provenance를 안정 키로 삼아 surgical edit + discard만 적용한다.

검수 판정 근거 요약(상세는 개선_report.md, G4 로그):
 - 코퍼스 §29의8 제1항(메인 고용증대 공제)에는 '상시근로자 수 감소 시 추징' 조항이 없음.
   명시적 추징은 ④전환·⑤복귀 공제의 '전환·복직일부터 2년 내 근로관계 종료'에 한함(제6-7항).
   ④·⑤ 공제는 '그 과세연도 상시근로자 수 감소 시 적용 배제' 단서가 있음(제4·5항).
   → 메인공제 인원감소 추징을 단정한 문항(seed#3/seed#7/팀원3#37)을 코퍼스-정직 답으로 교정/폐기.
 - track1_conflict#청년연령(고보령§17/조특령§81)은 인용 조문이 주택청약저축·고용창출
   임금지원으로 시나리오와 무관 + 답 내용 공백 → 폐기(청년정의 비교는 #78이 정상 커버).
 - 항/호 표기 차이로 '제외기준' 청크가 누락된 산정식 문항(seed#2, track1 산정식)은
   §26의8 제1-2항(제외기준) 청크를 보완해 수치 grounding을 확보.
 - gold_path placeholder 노드(지침_피보험자수_* 등)는 실제 chunk_id로 치환.
 - hop은 'gold_chunk_ids의 서로 다른 조(article) 수'로 정직하게 재계산.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset_gates as G  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
V2 = ROOT / "eval" / "gold_set_v2.json"
OUT = ROOT / "eval" / "gold_set_final.json"
LOG = ROOT / "eval" / "g4_review_log.md"

PREFIX_DOC = {
    "고보령": "고용보험법시행령",
    "근기령": "근로기준법시행령",
    "조특령": "조세특례제한법시행령",
    "조특법": "조세특례제한법",
    "지침": "청년일자리도약장려금지침",
}


def art_key(cid: str) -> str:
    """chunk_id에서 항 접미사 제거 -> 조(article) 키. 지침 청크는 그대로."""
    return re.sub(r"_제[\d의\-]+항$", "", cid)


def doc_of(cid: str) -> str:
    return PREFIX_DOC.get(cid.split("_")[0], cid.split("_")[0])


def normalize_node(node: str, valid: set) -> str:
    """gold_path의 항 단위 placeholder 노드(예: 조특령_제26조의8_제2항)를
    실제 청크(항 범위 청크, 예: 조특령_제26조의8_제1-2항)로 정규화."""
    if not node or node in valid:
        return node
    m = re.match(r"^([^_]+)_(제\d+조(?:의\d+)?)_제(\d+)항$", node)
    if not m:
        return node  # 비정형(지침_* 등)은 그대로(EDITS에서 처리)
    prefix, article, para = m.group(1), m.group(2), int(m.group(3))
    cands = G.idx().get((prefix, article)) or []
    body = [c for c in cands if c.get("section") != "부칙"] or cands
    for c in body:
        ps = G._para_set(c.get("paragraph"))
        if ps is None or para in ps:
            return c["chunk_id"]
    return node


# ---- 폐기(provenance) ----
DISCARD = {
    "seed#7": "메인공제 인원감소 추징(코퍼스 미근거) + seed#3와 사실상 중복. ④⑤ 추징은 별도 문항이 커버.",
    "track1_conflict#청년 연령 기준": "인용 조문(조특령§81=주택청약저축, 고보령§17=고용창출 임금지원)이 청년연령과 무관 + gold_answer 내용 공백. 청년정의 비교는 #78이 정상 커버.",
}

# ---- 교정(provenance -> 덮어쓸 필드) ----
EDITS: dict[str, dict] = {
    # id1: 친족/배우자/임원 제외는 전부 §26의8 제2항. 제9·13항 인용은 노이즈 → 제거.
    "seed#5": {
        "gold_articles": [
            "조특법시행령 제26조의8제2항제3호",
            "조특법시행령 제26조의8제2항제4호",
            "조특법시행령 제26조의8제2항제5호",
        ],
        "gold_chunk_ids": ["조특령_제26조의8_제1-2항"],
    },
    # id4: '매월 말일 평균'(계산방법, 제5-8항)과 '1년/60h 제외기준'(제2항) 둘 다 근거 필요.
    "seed#2": {
        "gold_articles": [
            "근로기준법시행령 제7조의2제1항",
            "조특법시행령 제26조의8제2항제1호",
            "조특법시행령 제26조의8제2항제2호",
            "조특법시행령 제26조의8제6항",
        ],
        "gold_chunk_ids": [
            "근기령_제7조의2_제1-2항",
            "조특령_제26조의8_제1-2항",
            "조특령_제26조의8_제5-8항",
        ],
    },
    # id5: 메인공제 인원감소 추징은 코퍼스 미근거 → 코퍼스-정직 답으로 재작성(추징은 ④⑤ 2년 한정).
    "seed#3": {
        "question": "통합고용세액공제를 받은 청년이 입사 1년 만에 퇴사했고 충원하지 않아 전체 상시근로자 수가 줄었습니다. 이미 받은 통합고용세액공제가 '인원 감소'를 이유로 추징되나요?",
        "gold_answer": "제공된 조문(조세특례제한법 제29조의8)상, 일반 고용증대(메인) 공제에 대해 '상시근로자 수 감소 시 추징'한다는 규정은 확인되지 않는다. 명시적 추징은 정규직 전환(제4항)·육아휴직 복귀자(제5항) 공제를 받은 자가 전환일 또는 복직일부터 2년이 지나기 전에 해당 근로자와의 근로관계를 종료하는 경우에 한한다(제6항·제7항). 또한 전환·복귀 공제는 그 과세연도에 상시근로자 수가 직전 과세연도보다 감소하면 적용하지 않는다(제4항·제5항 단서). 따라서 메인 공제분이 단순 인원 감소만으로 추징된다고 볼 근거는 제공 자료에 없다.",
        "gold_docs": ["조세특례제한법"],
        "gold_articles": [
            "조세특례제한법 제29조의8제4항",
            "조세특례제한법 제29조의8제6항",
            "조세특례제한법 제29조의8제7항",
        ],
        "gold_chunk_ids": ["조특법_제29조의8_제3-4항", "조특법_제29조의8_제6-7항"],
        "type": "일반",
        "numeric_claims": [{"value": "2", "unit": "년", "period": ""}],
        "gold_path": [],
    },
    # id10: 결론(특정인 퇴사≠추징)은 유지하되, 메인공제 추징 framework 단정을 제거.
    "seed#16": {
        "gold_answer": "특정 직원의 퇴사만으로 메인 통합고용세액공제가 추징되지는 않는다. 충원으로 전체 상시근로자 수가 공제 당시 수준 이상으로 유지되면 인원 감소 자체가 없고, 제공 조문상 메인(고용증대) 공제의 인원 감소 추징 규정도 확인되지 않는다. 명시적 추징은 정규직 전환·육아휴직 복귀 공제를 받은 자가 전환·복직일부터 2년 내 근로관계를 종료하는 경우에 한한다(조세특례제한법 제29조의8제6항·제7항). 다만 그 퇴사자가 전환·복귀 공제 대상자였다면 충원과 무관하게 2년 내 종료 시 추징될 수 있다.",
        "gold_articles": ["조세특례제한법 제29조의8제6항", "조세특례제한법 제29조의8제7항"],
        "gold_chunk_ids": ["조특법_제29조의8_제1항", "조특법_제29조의8_제6-7항"],
    },
    # id19(팀원3#21): id2(seed#11)와 1년미만 축 중복 → 단시간(60h) 축으로 변형.
    "팀원3.md#21": {
        "question": "근로기준법 시행령상 상시근로자 수를 산정할 때 월 소정근로시간 50시간인 단시간 근로자를 포함하나요? 통합고용세액공제에서는요?",
        "gold_answer": "근로기준법 시행령에서는 포함한다. 제7조의2제4항은 통상·기간제·단시간 근로자를 고용형태를 불문하고 모두 포함하며, '단시간이라서' 제외하는 규정이 없다. 반면 통합고용세액공제에서는 1개월간 소정근로시간이 60시간 미만인 단시간근로자를 상시근로자에서 제외한다(제26조의8제2항제2호). 따라서 월 50시간 단시간 근로자는 근로기준법에는 포함되나 세액공제에서는 제외되어 결과가 다르다.",
        "gold_articles": ["근로기준법시행령 제7조의2제4항", "조특법시행령 제26조의8제2항제2호"],
        "gold_chunk_ids": ["근기령_제7조의2_제3-4항", "조특령_제26조의8_제1-2항"],
        "numeric_claims": [
            {"value": "50", "unit": "시간", "period": ""},
            {"value": "60", "unit": "시간", "period": ""},
        ],
        "gold_path": [
            {"src": "근기령_제7조의2_제3-4항", "edge": "CONFLICTS_WITH",
             "dst": "조특령_제26조의8_제1-2항", "axis": "상시근로자_단시간_포함여부"}
        ],
    },
    # id20(팀원3#34): '월 160시간'(근거없는 수치) → '월 70시간'; gold_path 실노드 치환.
    "팀원3.md#34": {
        "question": "도약장려금은 고용보험 피보험자 수 기준이고 통합고용세액공제는 세법상 상시근로자 수 기준이라는데, 1년 이상 정규직·월 70시간·임원/친족 아닌 근로자라면 양쪽 기준 모두에 포함되나요?",
        "numeric_claims": [
            {"value": "1", "unit": "년", "period": ""},
            {"value": "70", "unit": "시간", "period": "월"},
            {"value": "60", "unit": "시간", "period": "월"},
        ],
        "gold_path": [
            {"src": "지침_p36_s77", "edge": "Concept",
             "dst": "고보령_제3조_제1-3항", "axis": "피보험자_자격"},
            {"src": "고보령_제3조_제1-3항", "edge": "Concept",
             "dst": "조특령_제26조의8_제1-2항", "axis": "상시근로자_포함여부"},
        ],
    },
    # id21(팀원3#20): gold_path placeholder(고보령_제3조_제1항/제2항) → 실노드.
    "팀원3.md#20": {
        "gold_path": [
            {"src": "고보령_제3조_제1-3항", "edge": "Concept",
             "dst": "조특령_제26조의8_제1-2항", "axis": "단시간_60시간_제외기준"}
        ],
    },
    # id22(팀원3#36): '월 160시간' → '월 70시간'; gold_path 실노드.
    "팀원3.md#36": {
        "question": "사업주의 배우자인 직원이 정규직·월 70시간으로 근무하고 있습니다. 근로기준법 시행령상 상시근로자 수에 포함되나요? 통합고용세액공제 상시근로자에는요?",
        "numeric_claims": [{"value": "70", "unit": "시간", "period": "월"}],
        "gold_path": [
            {"src": "근기령_제7조의2_제3-4항", "edge": "Concept",
             "dst": "조특령_제26조의8_제1-2항", "axis": "배우자_포함여부"}
        ],
    },
    # id23(팀원4#57): 결론(1년미만=비산입→퇴사 무영향) 유지, 추징 framework 단정 완화 + path 실노드.
    "팀원4.md#57": {
        "gold_answer": "해당하지 않는다. 근로계약기간 1년 미만인 근로자는 조특법 시행령 제26조의8제2항제1호에 따라 애초에 상시근로자에 포함되지 않는다. 상시근로자에 포함되지 않은 사람의 퇴사는 상시근로자 수의 증감에 영향을 주지 않는다. (참고로 제공 조문상 통합고용세액공제의 명시적 추징은 정규직 전환·육아휴직 복귀 공제를 받은 자가 2년 내 근로관계를 종료하는 경우에 한한다.)",
        "gold_path": [
            {"src": "조특령_제26조의8_제1-2항", "edge": "Concept",
             "dst": "조특법_제29조의8_제1항", "axis": "1년미만_비산입"}
        ],
    },
    # id27(팀원3#26): gold_path placeholder(지침_피보험자수_5인) → 실노드.
    "팀원3.md#26": {
        "gold_path": [
            {"src": "지침_p36_s77", "edge": "Concept",
             "dst": "조특령_제26조의8_제1-2항", "axis": "인원기준_용도상이"}
        ],
    },
    # id34(팀원3#37): 메인공제 일반 추징 단정(미근거) → ⑤복귀자 '감소 시 배제'(제5항 단서, 근거 있음)로 재작성.
    "팀원3.md#37": {
        "question": "육아휴직 복귀자 공제를 받으려는 과세연도에 회사 전체 상시근로자 수가 직전 과세연도보다 줄었습니다. 그래도 육아휴직 복귀자 공제를 받을 수 있나요?",
        "gold_answer": "받을 수 없다. 조세특례제한법 제29조의8제5항 단서에 따라 해당 과세연도에 상시근로자 수가 직전 과세연도보다 감소한 경우에는 육아휴직 복귀자 공제를 적용하지 않는다. (정규직 전환 공제도 제4항 단서로 동일하게 그 과세연도 상시근로자 수가 감소하면 배제된다.)",
        "gold_articles": ["조세특례제한법 제29조의8제5항"],
        "gold_chunk_ids": ["조특법_제29조의8_제5항"],
        "numeric_claims": [],
    },
    # id74(track1 1년미만): 1년미만 제외는 ②1호(제1-2항 청크). 제3-4항(청년정의) 오기 → 제1-2항.
    "track1_conflict#1년 미만 계약직 ": {
        "gold_articles": ["근로기준법시행령 제7조의2제4항", "조특법시행령 제26조의8제2항제1호"],
        "gold_chunk_ids": ["근기령_제7조의2_제3-4항", "조특령_제26조의8_제1-2항"],
        "gold_path": [
            {"src": "근기령_제7조의2_제3-4항", "edge": "CONFLICTS_WITH",
             "dst": "조특령_제26조의8_제1-2항", "axis": "1년 미만 계약직 포함 여부"}
        ],
    },
    # id76(track1 장려금 피보험자): gold_answer에 생성 스캐폴딩('QA Q10', 'cross-concept 축...') 누출 → 정리.
    "track1_conflict#장려금 피보험자 수": {
        "gold_answer": "다르다. 같은 인원이라도 장려금은 '고용보험 피보험자 수'로, 통합고용세액공제는 '세법상 상시근로자 수(근로계약 1년 이상·월 60시간 이상·임원/친족 제외 등)'로 집계한다. 집계 모집단이 달라 장려금 신청서의 인원을 세액공제 신청서에 그대로 전용할 수 없다.",
    },
    # id15(seed#12): §40②단서 문장은 제40조_제1항 청크 밖 → 제거(상호조정만 코퍼스 근거).
    "seed#12": {
        "gold_answer": "둘 다 받을 수 없다. 고용유지조치 기간 중 다른 지원금·장려금(고용촉진장려금 등)의 지급요건에 해당하더라도 고용유지지원금만 지급하고 그 밖의 지원금·장려금은 중복하여 지급하지 않는다(상호조정).",
    },
    # id56(track1 산정식): '4.8명'은 예시 수치(법령 미근거) → numeric_claims에서 제거,
    #   '1년미만 제외기준'(제2항)을 청크로 보완(현재는 계산방법 제5-8항만 인용).
    "track1_conflict#상시근로자 수 산정": {
        "gold_chunk_ids": [
            "근기령_제7조의2_제1-2항",
            "조특령_제26조의8_제5-8항",
            "조특령_제26조의8_제1-2항",
        ],
        "numeric_claims": [{"value": "1", "unit": "년", "period": ""}],
    },
}


def main():
    data = json.loads(V2.read_text(encoding="utf-8"))
    items = data["items"]
    G.chunks()  # warm
    valid = set(G._chunks.keys())

    out, log_rows, discard_rows = [], [], []
    seen_conflict_prov = set()

    for it in items:
        prov = it["provenance"]
        verdict, note = "OK", ""

        # track1_conflict#상시근로자 수 산정 은 v2에 2건(56,57) 존재 → 56(근기) 만 매칭하도록 조건부
        edit_key = prov
        if prov == "track1_conflict#상시근로자 수 산정":
            # 근기 vs 세법(=56)에만 청크 보완 적용; 고보 vs 세법(=57)은 제외
            if "근로기준법" not in "".join(it.get("gold_articles", [])):
                edit_key = None  # 57: skip
        if prov in DISCARD:
            discard_rows.append((it["id"], prov, DISCARD[prov]))
            continue

        if edit_key in EDITS:
            for k, v in EDITS[edit_key].items():
                it[k] = v
            verdict, note = "FIX", "G4 정정 적용"

        # gold_path 노드 정규화(항 단위 placeholder -> 실제 청크)
        for p in it.get("gold_path") or []:
            for k in ("src", "dst"):
                if k in p:
                    p[k] = normalize_node(p[k], valid)

        # gold_docs 재계산(청크 prefix 기준)
        if it.get("answerable", True) and it.get("gold_chunk_ids"):
            docs, seen = [], set()
            for c in it["gold_chunk_ids"]:
                d = doc_of(c)
                if d not in seen:
                    seen.add(d)
                    docs.append(d)
            it["gold_docs"] = docs

        # hop 재계산 = 서로 다른 조(article) 수
        cids = it.get("gold_chunk_ids", [])
        n_art = len({art_key(c) for c in cids})
        old_hop = it.get("hop")
        it["hop"] = max(1, min(3, n_art)) if cids else 1
        if it["hop"] != old_hop and verdict == "OK":
            verdict, note = "FIX", f"hop {old_hop}->{it['hop']}(조 기준 재계산)"
        elif it["hop"] != old_hop:
            note += f"; hop {old_hop}->{it['hop']}"

        # 비파괴 재게이트: 청크 실존 + 수치 grounding
        bad_chunks = [c for c in cids if c not in valid]
        g2_ok, g2d = G.g2_numeric(it)
        prov_ok = (not bad_chunks) and (bool(cids) if it.get("answerable", True) else not cids)
        it["needs_review"] = not (prov_ok and g2_ok)
        passed = ["G1"] if prov_ok else []
        if g2_ok:
            passed.append("G2")
        passed += ["G3", "G6"]
        it["gate_passed"] = passed

        flag = []
        if bad_chunks:
            flag.append(f"청크무효:{bad_chunks}")
        if not g2_ok:
            flag.append(f"수치미근거:{g2d.get('ungrounded')}")
        log_rows.append((it["id"], prov, verdict, it["type"], it["hop"], it["answerable"],
                         it["needs_review"], note, "; ".join(flag)))
        out.append(it)

    # 재번호
    for i, it in enumerate(out, 1):
        it["id"] = i

    # 메타 재계산
    def tally(key):
        d = {}
        for it in out:
            d[it[key]] = d.get(it[key], 0) + 1
        return dict(sorted(d.items(), key=lambda x: -x[1]))

    meta = {
        "n": len(out),
        "by_type": tally("type"),
        "by_hop": {str(k): v for k, v in sorted(tally("hop").items())},
        "by_origin": tally("origin"),
        "answerable_false": sum(1 for it in out if not it["answerable"]),
        "needs_review": [it["id"] for it in out if it["needs_review"]],
        "discarded": [{"prov": p, "reason": r} for _, p, r in discard_rows],
        "G0_edge_reliability": data["meta"].get("G0_edge_reliability"),
        "provenance": "gold_set_v2.json + 사람 G4 전수검수(build_gold_final.py)",
    }
    OUT.write_text(json.dumps({"meta": meta, "items": out}, ensure_ascii=False, indent=2), encoding="utf-8")

    # G4 로그
    lines = ["# G4 사람 검수 로그 (gold_set_v2 → gold_set_final)\n",
             f"- 입력 {len(items)}문항 → 출력 {len(out)}문항 (폐기 {len(discard_rows)}).\n",
             "\n## 폐기\n"]
    for i, p, r in discard_rows:
        lines.append(f"- v2#{i} [{p}]: {r}\n")
    lines.append("\n## 전 문항 판정\n")
    lines.append("| final_id | provenance | 판정 | type | hop | ans | review | 비고 | 게이트플래그 |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")
    for r in log_rows:
        # r index uses pre-renumber id; remap to final id by position
        pass
    # 최종 id로 다시 작성
    for it, r in zip(out, log_rows):
        _, prov, verdict, typ, hop, ans, rev, note, flag = r
        lines.append(f"| {it['id']} | {prov} | {verdict} | {typ} | {hop} | {ans} | {rev} | {note} | {flag} |\n")
    LOG.write_text("".join(lines), encoding="utf-8")

    print(f"out={len(out)} discarded={len(discard_rows)} needs_review={meta['needs_review']}")
    print("by_type:", meta["by_type"])
    print("by_hop:", meta["by_hop"])
    print("by_origin:", meta["by_origin"])
    flagged = [r for r in log_rows if r[8]]
    print("FLAGGED:")
    for r in flagged:
        print("  ", r[0], r[1], "|", r[8])


if __name__ == "__main__":
    main()
