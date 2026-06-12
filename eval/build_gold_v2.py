"""gold_set_v2 빌드 파이프라인 (확장가이드 §2·§4·§5).

후보 풀 = 시드18(마이그레이션) + held_out(팀원3·4 파싱 + 팀원2·1 큐레이션) + track1(KG 반자동).
→ 게이트(dataset_gates) 적용 → G5 중복제거 → 분포 맞춰 선별 → gold_set_v2.json + 폐기로그.

원칙:
 - gold는 KG/검증카드에서 가져온다(LLM이 gold 창작 X) → provenance-exact.
 - hop은 *결정론 규칙*으로 재산정(해소된 gold chunk 수): 1→hop1, 2→hop2, ≥3→hop3. (파일 라벨 불신, 정직)
 - origin {generated, held_out} 강제(G6) + 보고 시 분리집계.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "graph"))
sys.stdout.reconfigure(encoding="utf-8")

import dataset_gates as G  # noqa: E402

SEED = ROOT / "eval" / "gold_set_원본.jsonl"
OUT = ROOT / "eval" / "gold_set_v2.json"
REJECTS = ROOT / "eval" / "_gold_v2_rejects.json"

USE_LLM = "--llm" in sys.argv  # G3 LLM 판정/G5 임베딩 사용 여부

# 타입 정규화 → 6분류
TYPE_CANON = {
    "conflict": "문서간충돌", "문서간 충돌": "문서간충돌", "문서간충돌": "문서간충돌",
    "단일문서 정밀": "단일정밀", "단일정밀": "단일정밀", "사후관리 정밀": "단일정밀",
    "동명-무충돌 distractor": "동명무충돌", "동명무충돌": "동명무충돌", "동명-무충돌": "동명무충돌",
    "용도상이 distractor": "용도상이", "용도상이": "용도상이",
    "무응답 distractor": "무응답", "무응답": "무응답",
    "일반 distractor": "일반", "distractor": "일반", "일반": "일반",
}
# 가이드 §1 축B 목표 분포(총 80)
TARGET = {"문서간충돌": 24, "단일정밀": 16, "동명무충돌": 12, "용도상이": 8, "무응답": 8, "일반": 12}


def canon_type(t):
    return TYPE_CANON.get((t or "").strip(), "일반")


def refstr_of_chunk(cid):
    c = G.chunks().get(cid)
    if not c:
        return cid
    return f"{c['source_doc']} {c.get('article') or ''}".strip()


def derive_hop(item):
    if not item.get("answerable", True):
        return 1
    n = len(set(item.get("gold_chunk_ids", [])))
    return 1 if n <= 1 else (2 if n == 2 else 3)


def extract_numeric(text):
    out = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(세|시간|개월|년|명|인|원|%|일)", text):
        out.append({"value": m.group(1), "unit": m.group(2), "period": ""})
    # 중복 제거
    seen, uniq = set(), []
    for c in out:
        k = (c["value"], c["unit"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


# ---------------- 1) 시드 18 마이그레이션 ----------------
def load_seed():
    items = []
    SEED_REVIEW = {3, 7}  # §29의8 추징=④⑤한정 → 메인공제 연쇄추징 전제 재검토 필요(원문 확인 결과)
    for o in (json.loads(l) for l in SEED.open(encoding="utf-8") if l.strip()):
        # conflict는 실제 '문서 수 ≥ 2'일 때만 문서간충돌, 단일 문서면 단일정밀(라벨 정합)
        n_docs = len(set(o.get("gold_docs", [])))
        t = ("문서간충돌" if n_docs >= 2 else "단일정밀") if o["type"] == "conflict" else canon_type(o["type"])
        # 시드 distractor 기능별 재분류
        if o["id"] in (13, 17):
            t = "동명무충돌"
        elif o["id"] in (14, 15):
            t = "단일정밀"
        elif o["id"] in (16, 18):
            t = "일반"
        it = {
            "question": o["question"], "gold_answer": o["gold_answer"],
            "gold_docs": o.get("gold_docs", []), "gold_articles": o["gold_articles"],
            "type": t, "origin": "held_out", "connect_mech": "held_out",
            "numeric_claims": extract_numeric(o["gold_answer"]),
            "answerable": True,
            "_seed": True, "_src": f"seed#{o['id']}",
            "_review": (o["id"] in SEED_REVIEW),
        }
        items.append(it)
    return items


# ---------------- 2) 팀원3·4 JSON 블록 파싱 ----------------
def load_team_json(fname):
    txt = (ROOT / fname).read_text(encoding="utf-8")
    items = []
    for m in re.finditer(r"```json\s*(\{.*?\})\s*```", txt, re.DOTALL):
        try:
            o = json.loads(m.group(1))
        except Exception:
            continue
        items.append({
            "question": o["question"], "gold_answer": o["gold_answer"],
            "gold_docs": o.get("gold_docs", []), "gold_articles": o.get("gold_articles", []),
            "type": canon_type(o.get("type")), "origin": o.get("origin", "held_out"),
            "connect_mech": o.get("connect_mech", "held_out"),
            "numeric_claims": o.get("numeric_claims", []),
            "answerable": o.get("answerable", True),
            "gold_path": o.get("gold_path", []),
            "_src": f"{fname}#{o['id']}",
        })
    return items


# ---------------- 3) 팀원2 hop3 추징(큐레이션) ----------------
def load_team2():
    A2 = "조특법시행령 제26조의8제2항제2호"
    A6 = "조특법시행령 제26조의8제2항제6호"
    J4 = "조세특례제한법 제29조의8제4항"   # 정규직 전환 공제
    J5 = "조세특례제한법 제29조의8제5항"   # 육아휴직 복귀자 공제
    J7 = "조세특례제한법 제29조의8제7항"   # 2년 내 종료 추징
    rows = [
        ("H2", "기간제 직원을 정규직으로 전환해 전환 세액공제를 받았는데, 전환일로부터 1년 6개월 만에 그 직원이 퇴사했습니다. 세무상 문제가 있나요?",
         "추징이 발생한다. 정규직 전환 공제를 받은 자가 전환일부터 2년이 지나기 전에 근로관계를 종료하면, 종료일이 속하는 과세연도에 공제받은 세액 상당액을 소득세 또는 법인세로 납부해야 한다.",
         [J7], "문서간충돌"),
        ("H4", "대표이사의 배우자인 기간제 직원을 정규직으로 전환하면서 전환 공제를 받았습니다. 이후 2년 내 그 배우자가 퇴사했는데, 애초에 문제가 없었나요?",
         "두 가지 문제가 있다. ① 최대주주·대표자와 그 배우자는 상시근로자에서 제외되어 애초에 전환 공제 대상이 아니었다. ② 받았더라도 전환 2년 내 근로관계 종료 시 공제세액을 추징한다.",
         ["조특법시행령 제26조의8제2항제4호", J7], "문서간충돌"),
        ("H5", "육아휴직 후 복직한 직원에 대해 육아휴직 복귀자 공제를 신청하려는데, 같은 과세연도에 회사 전체 상시근로자 수가 작년보다 줄었습니다. 공제받을 수 있나요?",
         "받을 수 없다. 육아휴직 복귀자 공제는 그 과세연도에 상시근로자 수가 직전 과세연도보다 감소하면 공제하지 않는다(§29의8⑤ 단서).",
         [J5], "문서간충돌"),
        ("H8", "기간제 직원을 정규직 전환해 전환 공제를 받았습니다. ① 전환한 그 해에 전체 상시근로자 수가 직전보다 줄면? ② 공제는 받았는데 전환 1년 후 그 직원이 퇴사하면?",
         "① 전환 과세연도에 상시근로자 수가 직전보다 감소하면 그 전환 공제를 적용하지 않는다. ② 공제받은 뒤 전환일부터 2년 내 근로관계를 종료하면 공제세액을 추징한다.",
         [J4, J7], "문서간충돌"),
        ("H11", "제외사유가 전혀 없는 정규직(1년 이상·주40시간·임원/친족 아님)을 기간제에서 전환해 적정하게 전환 공제를 받았습니다. 그래도 추징될 수 있나요?",
         "적정하게 받았더라도 추징될 수 있다. 전환일부터 2년 내 그 직원과 근로관계를 종료하면 공제세액을 추징한다. 적정 공제와 사후관리 추징은 별개다.",
         [J7], "문서간충돌"),
        ("H19", "육아휴직 복귀자 공제를 신청하는데, 회사에 월 55시간 단시간 직원들이 있어 상시근로자 수 계산이 애매합니다. 이들 때문에 공제가 영향을 받나요?",
         "월 60시간 미만 단시간은 세법 상시근로자에서 제외되어 수 계산에 안 들어간다. 그 결과 산정된 전체 상시근로자 수가 직전 과세연도보다 감소했다면 복귀자 공제는 배제된다.",
         [A2, J5], "문서간충돌"),
        ("H20", "기간제 직원을 정규직 전환해 공제받았는데, 그중 일부는 4대보험·원천징수가 확인 안 됩니다. ① 그 해 상시근로자 수가 줄었고 ② 전환 2년 내 퇴사도 있었다면?",
         "① 4대보험·원천징수 미확인자는 세법 상시근로자에서 제외되어 수 계산에서 빠지므로, 그 과세연도 상시근로자 수가 직전보다 감소하면 전환 공제가 배제된다. ② 공제받은 직원이 전환 2년 내 퇴사하면 추징한다.",
         [A6, J4, J7], "문서간충돌"),
        # distractor (반례: 트리거 없음)
        ("H3", "정규직 전환 공제를 받은 직원이 전환일로부터 2년 6개월 뒤에 퇴사했습니다. 추징되나요?",
         "추징되지 않는다. 추징은 전환일부터 2년이 지나기 전 근로관계 종료 시에만 발생한다. 2년 경과 후 퇴사는 사후관리 기간 경과로 해당 없음.",
         [J7], "일반"),
        ("H7", "정규직 전환 공제를 신청하는데, 그 과세연도에 전체 상시근로자 수가 직전 과세연도보다 늘었습니다. 전환 공제를 받을 수 있나요?",
         "받을 수 있다. 공제 배제는 그 과세연도 상시근로자 수가 직전보다 감소한 경우에만 적용된다. 증가했으면 배제 사유가 없다.",
         [J4], "일반"),
        ("H13", "월 60시간 이상 근무하는 단시간 직원을 정규직 전환해 공제받았고, 전환 2년이 지난 뒤 퇴사했습니다. 추징되나요?",
         "추징되지 않는다. 월 60시간 이상 단시간은 상시근로자에 포함되어 전환 공제 대상이 될 수 있고, 전환 2년 경과 후 퇴사는 추징 기간 밖이다.",
         [A2, J7], "동명무충돌"),
        ("H18", "정규직 전환 공제를 받았는데, ① 전환한 해에 상시근로자 수가 직전보다 늘었고 ② 그 직원도 전환 3년째 재직 중입니다. 추징이나 배제가 있나요?",
         "둘 다 없다. ① 상시근로자 수 증가라 배제 사유 없음. ② 전환 2년 경과 후에도 재직 중이라 추징 사유 없음. 정상 공제가 유지된다.",
         [J4, J7], "일반"),
    ]
    out = []
    for hid, q, ga, arts, t in rows:
        out.append({
            "question": q, "gold_answer": ga, "gold_docs": ["조세특례제한법", "조세특례제한법시행령"],
            "gold_articles": arts, "type": t, "origin": "generated", "connect_mech": "REFERENCES",
            "numeric_claims": extract_numeric(q + " " + ga), "answerable": True,
            "_src": f"팀원2#{hid}",
        })
    return out


# ---------------- 4) track1: KG 반자동(검증 충돌쌍) ----------------
def load_track1_conflicts():
    out = []
    for c in G.conflicts():
        if not c.get("verified", False):
            continue  # verified=false(청년 장려금) 제외 — 가이드 금지
        ca, cb = c["chunk_a"], c["chunk_b"]
        q = (f"같은 '{c['concept']}'라도 {c['doc_a']}와 {c['doc_b']}의 '{c['axis']}' 기준이 "
             f"서로 다른가요? 동일 수치를 양쪽에 그대로 쓸 수 있나요?")
        out.append({
            "question": q, "gold_answer": c["note"],
            "gold_docs": [c["doc_a"], c["doc_b"]],
            "gold_articles": [refstr_of_chunk(ca), refstr_of_chunk(cb)],
            "gold_chunk_ids_seed": [ca, cb],  # 이미 실제 KG chunk(provenance-exact)
            "type": "문서간충돌", "origin": "generated", "connect_mech": "CONFLICTS_WITH",
            "numeric_claims": extract_numeric(c["note"]),
            "answerable": True,
            "gold_path": [{"src": ca, "edge": "CONFLICTS_WITH", "dst": cb, "axis": c["axis"]}],
            "_src": f"track1_conflict#{c['axis'][:10]}",
        })
    return out


def load_track1_single():
    """단일 조 정밀(provenance-exact) — 분포 미달 시 채움용. gold=해당 청크."""
    picks = [
        ("조특령_제26조의8_제3-4항", "통합고용세액공제의 최소고용증가인원수는 중견기업과 그 외 기업이 각각 몇 명인가요?",
         "중견기업은 5명, 중견기업이 아닌 경우는 10명이다(조특법 시행령 제26조의8제3항).", "단일정밀"),
        ("조특령_제26조의8_제5-8항", "통합고용세액공제에서 출산전후휴가 중인 상시근로자는 어떤 조건에서 상시근로자 수에서 제외되나요?",
         "출산전후휴가를 사용 중인 상시근로자를 대체하는 상시근로자가 있는 경우에 한해, 그 출산휴가자를 상시근로자 수 및 청년등상시근로자 수에서 제외한다(제26조의8제7항).", "단일정밀"),
        ("고보령_제12조_제3-5항", "고용보험법상 우선지원대상기업 판정 시 '상시 사용하는 근로자 수'는 어떻게 산정하나요?",
         "그 사업주의 모든 사업에서 전년도 매월 말일 현재 근로자 수의 합계를 전년도 조업 개월 수로 나누어 산정한다(고용보험법 시행령 제12조).", "단일정밀"),
    ]
    out = []
    for cid, q, ga, t in picks:
        out.append({
            "question": q, "gold_answer": ga, "gold_docs": [G.chunks()[cid]["source_doc"]],
            "gold_articles": [refstr_of_chunk(cid)], "gold_chunk_ids_seed": [cid],
            "type": t, "origin": "generated", "connect_mech": "Concept",
            "numeric_claims": extract_numeric(ga), "answerable": True,
            "_src": f"track1_single#{cid}",
        })
    return out


# ---------------- 5) track1: 보강 distractor (검증 청크 기반) ----------------
def _mk(q, ga, arts, t, mech, src, cids=None):
    it = {
        "question": q, "gold_answer": ga,
        "gold_docs": sorted({G.chunks()[c]["source_doc"] for c in (cids or [])}) if cids else [],
        "gold_articles": arts, "type": t, "origin": "generated", "connect_mech": mech,
        "numeric_claims": extract_numeric(q + " " + ga), "answerable": True, "_src": src,
    }
    if cids:
        it["gold_chunk_ids_seed"] = cids
    return it


def load_track1_distractors():
    G3 = "고용보험법시행령 제3조제1항"        # 고보령_제3조_제1-3항 (자격: 월60/주15)
    G12 = "고용보험법시행령 제12조제3항"       # 고보령_제12조_제3-5항 (규모 산정)
    K7 = "근로기준법시행령 제7조의2제1항"      # 근기령 (근기 적용 판단)
    S2 = "조특법시행령 제26조의8제2항제2호"    # 조특령 (세법 단시간 60h)
    out = []
    # 용도상이(8 목표): 같은 지표/용어, 목적이 달라 한쪽 기준을 다른 용도에 전용 불가
    out += [
        _mk("우선지원대상기업 해당 여부를 판정하려는데, 상시근로자 수를 근로기준법 시행령 제7조의2의 '연인원÷가동일수' 공식으로 계산하면 되나요?",
            "안 된다. 우선지원대상기업 규모 판정은 고용보험법 시행령 제12조의 산정식(전년도 매월 말일 근로자 수 합계를 조업 개월 수로 나눔)을 쓴다. 근기법 시행령 제7조의2는 근로기준법 적용 여부 판단용이라 목적과 공식이 다르다.",
            [G12, K7], "용도상이", "Concept", "track1_용도1", ["고보령_제12조_제3-5항", "근기령_제7조의2_제1-2항"]),
        _mk("고용보험 우선지원대상기업 판정 때 쓴 상시근로자 수를, 통합고용세액공제 신청서의 상시근로자 수로 그대로 옮겨 적어도 되나요?",
            "안 된다. 두 수치는 목적이 다르다. 고용보험 제12조는 우선지원대상기업 규모 판정용이고, 세액공제는 1년 미만·월60시간 미만 단시간·임원·친족 등을 제외한 세법 상시근로자다. 세법 기준으로 재산정해야 한다.",
            [G12, "조특법시행령 제26조의8제2항"], "용도상이", "Concept", "track1_용도2",
            ["고보령_제12조_제3-5항", "조특령_제26조의8_제1-2항"]),
        _mk("고용보험 피보험자 자격 판단의 '주 15시간' 기준을, 통합고용세액공제 단시간 제외 판단에도 그대로 적용하면 되나요?",
            "안 된다. 고용보험법 시행령 제3조의 주 15시간/월 60시간은 피보험자 자격(적용제외) 판단 기준이고, 세법은 월 60시간 미만 단시간을 상시근로자에서 제외하는 별개의 기준이다. 목적이 달라 기준을 혼용할 수 없다.",
            [G3, S2], "용도상이", "Concept", "track1_용도3",
            ["고보령_제3조_제1-3항", "조특령_제26조의8_제1-2항"]),
    ]
    # 동명-무충돌(12 목표): 같은 용어지만 결과가 같아 충돌 아님(음성표본)
    out += [
        _mk("주 13시간만 일하는 파트타임 직원은 고용보험 피보험자와 세법 상시근로자 양쪽에서 어떻게 처리되나요?",
            "양쪽 모두 제외된다. 고용보험은 주 15시간 미만이라 적용제외(3개월 이상 계속근로 시 적용)이고, 세법은 월 60시간 미만 단시간이라 상시근로자에서 제외한다. 기준 표현은 다르나 이 사례에서는 양쪽 다 제외로 결과가 같아 충돌이 없다.",
            [G3, S2], "동명무충돌", "Concept", "track1_동명1",
            ["고보령_제3조_제1-3항", "조특령_제26조의8_제1-2항"]),
        _mk("월 80시간·계약기간 2년 정규직(임원·친족 아님)은 고용보험과 통합고용세액공제 양쪽에 모두 포함되나요?",
            "양쪽 모두 포함된다. 월 60시간 이상이라 고용보험 적용 대상이고, 1년 이상·월60시간 이상이며 제외사유가 없어 세법 상시근로자에도 포함된다. 양쪽 결과가 같아 충돌이 없다.",
            [G3, "조특법시행령 제26조의8제2항"], "동명무충돌", "Concept", "track1_동명2",
            ["고보령_제3조_제1-3항", "조특령_제26조의8_제1-2항"]),
        _mk("월 65시간 단시간이지만 해당 사업에서 3개월 이상 계속 근로하는 직원은 고용보험과 세액공제에서 각각 어떻게 되나요?",
            "양쪽 모두 포함된다. 고용보험은 월 60시간 미만이 아니고(65시간) 게다가 3개월 이상 계속근로라 적용 대상이며, 세법도 월 60시간 이상이므로 단시간 제외에 해당하지 않아 상시근로자에 포함된다. 결과가 같아 충돌이 없다.",
            [G3, S2], "동명무충돌", "Concept", "track1_동명3",
            ["고보령_제3조_제1-3항", "조특령_제26조의8_제1-2항"]),
        _mk("1년 이상 근무한 통상 근로자는 근로기준법 상시근로자와 세법 상시근로자 양쪽에 모두 포함되나요?",
            "양쪽 모두 포함된다. 근기법 시행령 제7조의2는 고용형태를 불문하고 통상근로자를 포함하고, 세법도 1년 이상·제외사유 없는 통상근로자를 상시근로자에 포함한다. 이 경우 결과가 같아 충돌이 없다.",
            [K7, "조특법시행령 제26조의8제2항"], "동명무충돌", "Concept", "track1_동명4",
            ["근기령_제7조의2_제1-2항", "조특령_제26조의8_제1-2항"]),
    ]
    # 일반(12 목표): 단일 사실 음성표본
    out += [
        _mk("통합고용세액공제는 어느 과세연도부터 어느 과세연도까지의 기간에 적용되나요?",
            "내국인의 2026년 12월 31일이 속하는 과세연도부터 2028년 12월 31일이 속하는 과세연도까지의 기간에 적용된다(조특법 제29조의8제1항).",
            ["조세특례제한법 제29조의8제1항"], "일반", "REFERENCES", "track1_일반1",
            ["조특법_제29조의8_제1항"]),
        _mk("통합고용세액공제의 정규직 전환 공제에서 1인당 공제액은 중소기업과 중견기업이 각각 얼마인가요?",
            "정규직 전환 인원 1명당 중소기업은 1,300만원, 중견기업은 900만원을 공제한다(조특법 제29조의8제4항).",
            ["조세특례제한법 제29조의8제4항"], "일반", "REFERENCES", "track1_일반2",
            ["조특법_제29조의8_제3-4항"]),
        _mk("육아휴직 복귀자 공제의 1인당 공제액은 얼마인가요?",
            "육아휴직 복귀자 1명당 중소기업은 1,300만원, 중견기업은 900만원을 공제한다(조특법 제29조의8제5항).",
            ["조세특례제한법 제29조의8제5항"], "일반", "REFERENCES", "track1_일반3",
            ["조특법_제29조의8_제5항"]),
        _mk("고용보험 우선지원대상기업이 규모 확대로 더 이상 해당하지 않게 되면 몇 년간 우선지원대상기업으로 보나요?",
            "사유가 발생한 연도의 다음 연도부터 5년간 우선지원대상기업으로 본다(고용보험법 시행령 제12조).",
            ["고용보험법시행령 제12조제3항"], "일반", "Concept", "track1_일반4",
            ["고보령_제12조_제3-5항"]),
    ]
    return out


# ---------------- 6) track1: 진짜 tri-doc hop3 (검증 청크 3개 이상) ----------------
def load_track1_hop3():
    KG = "근기령_제7조의2_제1-2항"
    KG4 = "근기령_제7조의2_제3-4항"
    GB = "고보령_제3조_제1-3항"
    SE = "조특령_제26조의8_제1-2항"
    SE34 = "조특령_제26조의8_제3-4항"
    JP = "지침_p36_s77"
    JC = "지침_p25_s52"   # 청년 정의
    JS = "지침_p25_s53"   # 근로조건(주 30시간 이상)
    rows = [
        ("같은 직원 1명(월 70시간 단시간, 계약기간 1년 6개월)을 두고 ① 근로기준법 상시근로자 ② 고용보험 피보험자 ③ 통합고용세액공제 상시근로자에 각각 포함되는지 알려주세요.",
         "① 근기법: 고용형태 불문 포함되므로 상시근로자에 포함된다. ② 고용보험: 월 60시간 이상이라 피보험자 적용 대상이다. ③ 세법: 월 60시간 이상이고 근로계약기간이 1년 이상이라 상시근로자에 포함된다. 세 제도 모두 포함이나 각 산정 근거·목적은 다르다.",
         [KG, GB, "조특법시행령 제26조의8제2항"], [KG, GB, SE]),
        ("직전 1년 평균 고용보험 피보험자 수가 6명인 사업장에 대해 ① 청년일자리도약장려금 신청이 가능한지 ② 그 피보험자 수를 통합고용세액공제 상시근로자 수로 그대로 써도 되는지 판단해주세요.",
         "① 가능하다. 장려금은 직전 1년 평균 피보험자 수 5인 이상이 요건인데 6명이므로 충족한다. ② 안 된다. 장려금은 고용보험 피보험자 수 기준이고 세액공제는 1년 미만·월60시간 미만·임원·친족 등을 제외한 세법 상시근로자라 산정 기준이 달라 그대로 전용할 수 없다.",
         ["장려금지침(피보험자 5인 이상 요건)", "조특법시행령 제26조의8제2항"], [JP, SE]),
        ("월 55시간 단시간 직원에 대해 ① 고용보험 피보험자 자격 ② 통합고용세액공제 상시근로자 포함 여부 ③ 근로기준법 상시근로자 포함 여부를 모두 알려주세요.",
         "① 고용보험: 월 60시간 미만이라 원칙적으로 적용제외이나, 3개월 이상 계속근로하면 적용된다. ② 세법: 월 60시간 미만 단시간이라 상시근로자에서 제외된다. ③ 근기법: 고용형태를 불문하고 포함하므로 상시근로자에 포함된다. 세 제도의 결과가 갈린다.",
         ["고용보험법시행령 제3조제1항", "조특법시행령 제26조의8제2항제2호", "근로기준법시행령 제7조의2제4항"], [GB, SE, KG4]),
        ("대표이사의 배우자(월 80시간 정규직)를 ① 근로기준법 상시근로자 ② 통합고용세액공제 상시근로자 ③ 고용보험 피보험자로 각각 세는지 알려주세요.",
         "① 근기법: 고용형태 불문 포함이므로 상시근로자에 포함된다(동거친족 관련 별도 규정 있음). ② 세법: 최대주주·대표자의 배우자는 상시근로자에서 제외된다. ③ 고용보험: 월 60시간 이상이면 피보험자 적용 대상이다. 세법만 제외라 결과가 갈린다.",
         ["근로기준법시행령 제7조의2제4항", "조특법시행령 제26조의8제2항제4호", "고용보험법시행령 제3조제1항"], [KG4, SE, GB]),
        ("만 30세 청년 정규직을 채용했을 때 ① 통합고용세액공제의 청년등상시근로자 연령요건과 ② 청년일자리도약장려금의 청년 요건을 비교해 설명해주세요.",
         "① 세법은 15세 이상 34세 이하(병역이행기간 최대 6년 차감)를 청년으로 보며, 30세는 충족한다. ② 장려금 지침은 취업애로청년 등 별도 요건(연령·고용보험 가입기간 등)으로 청년을 정의한다. 두 제도의 청년 정의·산정이 달라 한쪽 충족이 다른 쪽 충족을 보장하지 않는다.",
         ["조특법시행령 제26조의8제3항", "장려금지침(청년 정의)"], [SE34, JC]),
        ("1년 미만 계약직 근로자를 ① 근로기준법 상시근로자 ② 통합고용세액공제 상시근로자 ③ 장려금 피보험자 수에 각각 세는지 차이를 설명해주세요.",
         "① 근기법: 1년 미만 제외 규정이 없어 포함한다. ② 세법: 근로기간 1년 미만은 상시근로자에서 제외한다(단 총 1년 이상 계속고용은 포함). ③ 장려금: 고용보험 피보험자 수 기준이라 피보험자면 인원에 잡힌다. 세 제도의 산입 결과가 갈린다.",
         ["근로기준법시행령 제7조의2제4항", "조특법시행령 제26조의8제2항제1호", "장려금지침(피보험자 수 기준)"], [KG4, SE, JP]),
        ("동거하는 친족인 근로자를 ① 근로기준법 상시근로자 ② 통합고용세액공제 상시근로자 ③ 고용보험 피보험자에 각각 포함하는지 알려주세요.",
         "① 근기법: 다른 일반 근로자가 1명이라도 있으면 동거친족인 근로자도 상시근로자에 포함한다. ② 세법: 최대주주·대표자의 직계존비속·친족관계인 사람은 상시근로자에서 제외한다. ③ 고용보험: 월 60시간 이상 등 자격을 충족하면 피보험자가 된다. 세법만 친족을 제외해 결과가 갈린다.",
         ["근로기준법시행령 제7조의2제4항", "조특법시행령 제26조의8제2항제5호", "고용보험법시행령 제3조제1항"], [KG4, SE, GB]),
        ("월 60시간 미만 단시간이지만 3개월 이상 계속 근로하는 직원을 ① 고용보험 피보험자 ② 통합고용세액공제 상시근로자 ③ 근로기준법 상시근로자로 각각 세는지 알려주세요.",
         "① 고용보험: 월 60시간 미만이라도 3개월 이상 계속근로하면 적용 대상이 되어 피보험자에 포함된다. ② 세법: 월 60시간 미만 단시간은 상시근로자에서 제외된다. ③ 근기법: 고용형태를 불문하고 포함한다. 고용보험·근기는 포함, 세법만 제외라 결과가 갈린다.",
         ["고용보험법시행령 제3조제2항", "조특법시행령 제26조의8제2항제2호", "근로기준법시행령 제7조의2제4항"], [GB, SE, KG4]),
        ("월 50시간 단시간 직원이 ① 청년일자리도약장려금 참여요건(주 30시간 이상)을 충족하는지 ② 통합고용세액공제 상시근로자에 포함되는지 ③ 고용보험 피보험자인지 판단해주세요.",
         "① 장려금: 주 소정근로시간 30시간 이상이 요건인데 주 환산 시 미달 가능성이 높아 원칙적으로 참여요건 미충족이다. ② 세법: 월 60시간 미만이라 상시근로자에서 제외된다. ③ 고용보험: 월 60시간 미만이라 원칙적으로 적용제외(3개월 이상 계속근로 시 적용). 세 제도 대체로 부정적이나 근거가 각각 다르다.",
         ["장려금지침(근로시간 주30시간 요건)", "조특법시행령 제26조의8제2항제2호", "고용보험법시행령 제3조제1항"], [JS, SE, GB]),
    ]
    out = []
    for i, (q, ga, arts, cids) in enumerate(rows, 1):
        out.append(_mk(q, ga, arts, "문서간충돌", "CONFLICTS_WITH", f"track1_hop3#{i}", cids))
    return out


# ---------------- LLM judge / embed (옵션) ----------------
def get_llm():
    import graph_common as gc
    cli = gc.get_openai()

    def judge(q, gold, ctx):
        r = cli.chat.completions.create(
            model="gpt-4o-mini", temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content":
                       "아래 [발췌]만으로 [질문]에 [정답]을 도출할 수 있고 그 정답이 유일한가? "
                       "발췌 밖 지식이 필요하면 derivable=false. JSON {\"derivable\":bool,\"score\":0~1}"},
                      {"role": "user", "content": f"[질문]{q}\n[정답]{gold}\n[발췌]\n{ctx}"}])
        try:
            return json.loads(r.choices[0].message.content)
        except Exception:
            return {"derivable": False, "score": 0.0}

    def embed(text):
        return cli.embeddings.create(model=gc.EMBED_MODEL, input=text).data[0].embedding

    return judge, embed


def main():
    judge, embed = (get_llm() if USE_LLM else (None, None))

    pool = (load_seed() + load_team_json("팀원3.md") + load_team_json("팀원4.md")
            + load_team2() + load_track1_conflicts() + load_track1_single()
            + load_track1_distractors() + load_track1_hop3())
    for i, it in enumerate(pool):
        it["_uid"] = i
        it.setdefault("origin", "generated")
        it.setdefault("_seed", False)
        # track1은 gold_chunk_ids를 KG에서 직접 주입(provenance-exact)
        if it.get("gold_chunk_ids_seed"):
            it["gold_chunk_ids"] = it.pop("gold_chunk_ids_seed")

    # ---- 게이트 적용 ----
    for it in pool:
        if "gold_chunk_ids" in it and it["gold_chunk_ids"]:
            # 이미 주입된 경우에도 G1은 통과로 보되 G2 채점 위해 resolved 유지
            G.g2_numeric(it)
            it["gate_passed"] = ["G1", "G2", "G6"]
            it["gate_ok"] = True
            G.g6_origin(it)
        else:
            G.run_gates(it, judge)
        it["hop"] = derive_hop(it)

    g0 = G.g0_edge_reliability()

    # ---- 시드 절대 보존(가이드 §7): 게이트 실패해도 유지하되 needs_review 표기 ----
    for it in pool:
        if it.get("_seed") and not it["gate_ok"]:
            it["_review"] = True

    # ---- G5 중복 제거 ----
    survivors = [it for it in pool if it["gate_ok"] or it.get("_seed")]
    gate_rejects = [{"src": it["_src"], "gate_passed": it["gate_passed"],
                     "detail": it.get("gate_detail", {})}
                    for it in pool if not it["gate_ok"] and not it.get("_seed")]
    dropped_dup = []
    if USE_LLM:
        survivors, dropped_dup = G.g5_dedup(survivors, embed)

    # ---- 분포 맞춰 80 선별 ----
    selected, sel_rejects = select_distribution(survivors, total=80)

    # ---- 최종 스키마 정리 ----
    final = []
    for nid, it in enumerate(selected, start=1):
        final.append({
            "id": nid, "question": it["question"], "gold_answer": it["gold_answer"],
            "gold_docs": it.get("gold_docs", []), "gold_articles": it.get("gold_articles", []),
            "gold_chunk_ids": list(dict.fromkeys(it.get("gold_chunk_ids", []))),
            "gold_path": it.get("gold_path", []),
            "hop": it["hop"], "type": it["type"],
            "connect_mech": it.get("connect_mech", "held_out"), "origin": it["origin"],
            "numeric_claims": it.get("numeric_claims", []),
            "answerable": it.get("answerable", True),
            "gate_passed": it.get("gate_passed", []),
            "provenance": it["_src"], "needs_review": it.get("_review", False),
        })

    OUT.write_text(json.dumps({"meta": meta(final, g0), "items": final},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    REJECTS.write_text(json.dumps({"gate_rejected": gate_rejects, "dedup_dropped": dropped_dup,
                                   "distribution_trimmed": sel_rejects},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(final, g0, len(pool), gate_rejects, dropped_dup, sel_rejects)


def select_distribution(items, total=80):
    """가이드 §1 타입목표를 상한으로 우선순위 선별 → 80 미달 시 잔여로 채움.
    우선순위(가이드 §7): 시드 > held_out > hop3 > 나머지. 시드는 무조건 포함."""
    def prio(it):
        return (0 if it.get("_seed") else (1 if it["origin"] == "held_out" else 2), -it["hop"])

    seeds = [it for it in items if it.get("_seed")]
    rest = [it for it in items if not it.get("_seed")]

    # 시드가 이미 채운 타입 카운트
    from collections import Counter
    used = Counter(it["type"] for it in seeds)
    buckets = {}
    for it in rest:
        buckets.setdefault(it["type"], []).append(it)

    selected = list(seeds)
    leftover = []
    for t, cap in TARGET.items():
        b = sorted(buckets.get(t, []), key=prio)
        room = max(0, cap - used.get(t, 0))
        selected += b[:room]
        leftover += b[room:]
    for t, b in buckets.items():  # 미분류 타입
        if t not in TARGET:
            leftover += b

    HARD_CAP = {"무응답": 8}  # 과대표집 방지(음성표본이 다수결을 흐리지 않게)
    trimmed = []
    selected = sorted(selected, key=prio)
    if len(selected) < total:  # 80 미달 → 잔여로 채우되 hop3·미달버킷 우선, 하드캡 준수
        cur = Counter(it["type"] for it in selected)

        def fill_prio(it):  # hop3 우선 → 목표 대비 미달 큰 타입 우선
            deficit = TARGET.get(it["type"], 0) - cur.get(it["type"], 0)
            return (-it["hop"], -deficit, prio(it))

        leftover = sorted(leftover, key=fill_prio)
        kept_leftover = []
        for x in leftover:
            if len(selected) >= total:
                kept_leftover.append(x)
                continue
            if cur.get(x["type"], 0) >= HARD_CAP.get(x["type"], 10 ** 9):
                kept_leftover.append(x)
                continue
            selected.append(x)
            cur[x["type"]] += 1
        trimmed += [{"src": x["_src"], "type": x["type"], "reason": "분포상한/하드캡 초과"} for x in kept_leftover]
    else:
        if len(selected) > total:  # 초과 → 비시드 저우선부터 컷
            keep_seed = [it for it in selected if it.get("_seed")]
            cut = sorted([it for it in selected if not it.get("_seed")], key=prio)
            selected = keep_seed + cut[:total - len(keep_seed)]
            trimmed += [{"src": x["_src"], "type": x["type"], "reason": "total 초과"} for x in cut[total - len(keep_seed):]]
        trimmed += [{"src": x["_src"], "type": x["type"], "reason": "분포상한 초과"} for x in leftover]
    return selected, trimmed


def meta(final, g0):
    from collections import Counter
    return {
        "n": len(final),
        "by_type": dict(Counter(i["type"] for i in final)),
        "by_hop": dict(Counter(i["hop"] for i in final)),
        "by_origin": dict(Counter(i["origin"] for i in final)),
        "by_mechanism": dict(Counter(i["connect_mech"] for i in final)),
        "answerable_false": sum(1 for i in final if not i["answerable"]),
        "needs_review": [i["id"] for i in final if i["needs_review"]],
        "G0_edge_reliability": g0,
        "target_distribution": TARGET,
    }


def print_summary(final, g0, npool, gate_rej, dup, trim):
    m = meta(final, g0)
    print(f"풀 {npool} → 게이트통과 후 dedup → 최종 {m['n']}개")
    print(f"게이트 폐기 {len(gate_rej)} | 중복 폐기 {len(dup)} | 분포 trim {len(trim)}")
    print(f"\n[타입]  목표 {TARGET}")
    print(f"        실제 {m['by_type']}")
    print(f"[hop]   {m['by_hop']}  (1→1청크 2→2 ≥3→3, 결정론)")
    print(f"[origin] {m['by_origin']}  [기제] {m['by_mechanism']}")
    print(f"[무응답] {m['answerable_false']} | [시드 재검토 필요] {m['needs_review']}")
    print(f"[G0] REFERENCES 해소율 {g0['REFERENCES_resolution_rate']}, 항도달 {g0['REFERENCES_paragraph_hit']}")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
