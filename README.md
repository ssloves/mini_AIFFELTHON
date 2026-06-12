# Legal GraphRAG — 한국 노동·세법 연쇄 추론 시스템

> 법률·시행령·행정지침 5종 사이의 논리적 관계를 **지식 그래프**로 구조화하고, 다중 문서
> 연쇄 추론(multi-hop)과 **충돌 감지**를 통해 *근거가 추적 가능한* 법률 답변을 생성하는 GraphRAG 에이전트.

**핵심 주장 (실험으로 검증)**
> GraphRAG는 *더 똑똑한 답*이 아니라, **근거를 덜 놓치고 올바른 경로로 도달하는 더 신뢰할 수 있는 답**을 낸다.
> → 1순위 지표는 Answer Accuracy가 아니라 **Context Recall**과 **경로 충실도(Path Faithfulness)**.
> **그리고 그 우위의 원천은 그래프 일반확장이 아니라 *그래프 위에서 작동하는 개념-충돌 앵커*다.**

---

## 1. 한눈에 보는 결과 (78문항 최종셋 · `eval/gold_set_final.json`)

동일 청크·임베딩을 쓰는 **강화 Naive RAG**(예산 일치 + 조문 다양화)와 **사람 전수검수 78문항**으로 비교하고,
**5단 ablation 사다리(B0~B4)** 로 우위의 원인을 분해했다.
(상세·재현: [`docs/exp_plan.md`](docs/exp_plan.md), 원자료 [`eval/_exp_results.json`](eval/_exp_results.json) 외)

| 지표 | GraphRAG (Ours) | Naive (강화) | 차이 |
|------|:---:|:---:|:---:|
| **Context Recall (조 단위)** | **0.683** | 0.436 | **+0.247** |
| Context Recall (항/호 정밀, NEW) | **0.648** | 0.307 | **+0.341** |
| **Answer Accuracy** (LLM-judge) | **0.720** | 0.447 | **+0.273** |
| **Faithfulness** (LLM-judge) | **0.934** | 0.743 | **+0.191** |
| **Abstention (무응답률)** | **0.013** | 0.449 | **−0.436** |
| Conflict Detection P / R / F1 (충돌 26) | **0.641 / 0.962 / 0.769** | — | FP 14 · FN 1 |
| Conflict 과탐 flag (distractor 16) | 0.875 | 0.750 | — |
| Path Faithfulness (conn_precision / coincidence) | **0.984 / 0.031** | — | 우연 도달 3% |

- ✅ **검증됨 (H1·H5·H6)**: 근거 수집률(+0.247, 항/호 정밀 +0.341)·정확도·충실도가 오르고, Naive가 45% "모름"일 때
  Ours는 1%. 정답에 도달할 때 밟은 연결의 **98%가 전문가 정의 관계**(우연 아님).
- 🔑 **우위의 원천 = 앵커 (귀인 분해)**: 개념-충돌 앵커의 순기여 Recall **+0.321**인 반면, *앵커 없는 일반 그래프
  확장은 −0.088로 오히려 손해*. 즉 "GraphRAG가 좋다"가 아니라 **"이 도메인엔 개념-충돌 앵커 구조가 필요하다"**.
  (룩업 아님: 7쌍이 78문항·held_out에 일반화 + 추론 경로에 개념브리지 7회 실사용)
- ⚠️ **한계 (H4 실패)**: distractor **과탐 flag 0.875 (FP 14)** — 앵커가 유형을 안 가리고 발동. 최대 숙제.
  ("hop 심화 +격차"는 78셋에서 지지 방향이나 hop3 n=8, held_out 충돌 n=8로 **잠정**.)

> 모든 수치는 1회 실행 결과이며, 측정·데이터의 한계와 실패 사례는 [`docs/exp_plan.md`](docs/exp_plan.md) §8에 **전수 기록**.

---

## 2. 아키텍처

```
[사용자 질문]
     │
     ▼
 ┌────────┐   ┌─────────────┐   ┌──────────┐   ┌──────────┐
 │ Router │──▶│  Retriever  │──▶│ Reasoner │──▶│ Verifier │──(ACCEPT)──▶ Output
 │ (분류) │   │ 벡터+그래프  │   │  (추론)  │   │  (검증)  │
 └────────┘   └─────────────┘   └──────────┘   └────┬─────┘
                    ▲                                │ REJECT (≤2회 회귀)
                    └────────────────────────────────┘
```

- **Router** — 질문을 `simple / multihop / conflict`로 분류해 검색 전략 결정.
- **Retriever** — 벡터 시드 + **개념 구동 충돌 앵커**(질문 개념과 일치하는 `CONFLICTS_WITH` 정준 조문쌍 주입)
  + 개념 브리지(`DEFINES`) + `REFERENCES` 확장 → 재랭킹(앵커 최우선).
- **Reasoner** — 수집 컨텍스트만 근거로 답 생성 + 조항 인용 + 충돌 표면화.
- **Verifier** — 인용 존재성(조문 단위 보정)·수치 충실성 검증, 실패 시 검색 폭을 넓혀 회귀.

지식 그래프: `Document·Article·Chunk·Concept·Condition` 노드와
`CONTAINS·REFERENCES·DEFINES·MENTIONS·CONFLICTS_WITH·DELEGATES·HAS_CONDITION·NEXT_PARAGRAPH` 엣지.
스키마 명세: [`data/graph_schema.md`](data/graph_schema.md).

---

## 3. 레포지토리 구조

```
mini_AIFFELTHON/
├── raw_data/              # 원본 PDF 5종
├── data/
│   ├── processed/         # [Step 1] PDF→정제 마크다운 5종
│   ├── chunks/            # [Step 2] 구조 인식 청킹(all_chunks.jsonl)
│   ├── embeddings/        # [Step 3] 임베딩 캐시(.npy는 gitignore)
│   ├── *.json(l)          # [Step 2] 개념·조건·충돌·EDA 통계 산출물
│   └── graph_schema.md    # [Step 2] 노드/엣지 스키마 정의서
├── notebooks/             # 파이프라인 스크립트(.py): 01_pdf_to_md, 02_eda, 02_chunking,
│                          #   02_concepts_conflicts, 02b_curate_conflicts, 02_conditions, legal_common
├── graph/                 # [Step 3] build_graph, populate_embeddings, build_embeddings,
│                          #   build_offline_graph, verify_retrieval, audit_references, final_check + 리포트
├── agent/                 # [Step 4] LangGraph: graph_state, nodes, retrieval, prompts, app, run_eval
├── eval/                  # 평가 하니스 + 데이터셋 + 측정 산출물
│   ├── gold_set_final.json        # 사람 G4 전수검수 78문항(최종 평가셋)
│   ├── gold_set_v2.json           # 검수 전 자동확장 중간본
│   ├── 평가메트릭_정의서.md        # 메트릭 정의서
│   ├── build_gold_v2/final.py     # 데이터셋 빌드, dataset_gates.py(G0~G6)
│   ├── run_experiment.py          # GraphRAG vs Naive 본 실험
│   ├── ablation_ladder.py         # B0~B4 ablation
│   ├── attribution_metrics.py     # 요인 귀인 분해
│   ├── score_precision.py         # 조 vs 항/호 정밀 채점
│   ├── score_factuality.py        # 사실성(수치근거·인용해소)
│   ├── path_metric.py             # 경로 충실도
│   ├── _*_results.json            # 각 측정 원자료
│   ├── g4_review_log.md           # G4 사람검수 로그
│   └── gold_authoring/            # 문항 작성 원자료(팀원1~4, heldout 사실카드)
├── docs/                  # 리포트: study(학습)·eda_report(EDA)·exp_report(1차)·exp_plan(최종)
│                          #   + 개선/개선_report(로드맵)·데이터셋_확장가이드
├── archive/               # 빈 스캐폴드·구버전 파일 모음
├── guide.md               # 전체 파이프라인 가이드라인
├── requirements.txt
└── README.md
```

---

## 4. 대상 문서셋

| # | 문서 | 약칭 |
|---|------|------|
| 1 | 2025년 청년일자리도약장려금 사업운영 지침(일부 개정안) | **장려금지침** |
| 2 | 고용보험법 시행령 (제36306호) | **고용보험법시행령** |
| 3 | 근로기준법 시행령 (제35436호) | **근로기준법시행령** |
| 4 | 조세특례제한법 (제21738호) | **조특법** |
| 5 | 조세특례제한법 시행령 (제36338호) | **조특법시행령** |

문서의 성격이 다르다(법률 ↔ 위임 시행령 ↔ 행정지침). 같은 용어(예: "상시근로자 수", "단시간근로자")가
문서마다 **다르게 정의·산정**되므로, 단순 벡터 검색으로는 "어느 문서 기준인지"를 구분하기 어렵다 →
그래프로 문서 간 관계를 명시하는 동기가 된다.

---

## 5. 파이프라인 (Step 1 → 4)

| Step | 작업 | 핵심 결과물 |
|------|------|------------|
| **Step 1** | PDF → 구조화 마크다운(조-항-호 위계 보존, 메타데이터) | `data/processed/*.md` |
| **Step 2** | EDA + 법률 구조 인식 청킹 + 개념/충돌/조건 추출 + 스키마 설계 | `data/chunks/`, `data/*.jsonl`, `data/graph_schema.md` |
| **Step 3** | Neo4j 그래프 구축 + 벡터 인덱스 + 참조 감사·오프라인 검증 | `graph/*`, `data/embeddings/` |
| **Step 4** | LangGraph 에이전트(Router→Retriever→Reasoner→Verifier→Output) | `agent/app.py` |

> 설계 근거와 의사결정의 정성적 설명은 [`docs/study.md`](docs/study.md),
> 단계별 상세 가이드는 [`guide.md`](guide.md)를 참조.

### 방법론적 특징
- **전문가 앵커 충돌셋(7건)**: LLM 자동 충돌 탐지가 미묘한 사례에서 불안정해, 전문가가 확정한 정준 충돌쌍을 사용.
  한국 노동·세법은 실제로 상당히 정렬돼 있어 *진짜 충돌은 희소* → "희소한 충돌을 정확히 짚는 것"이 가치.
- **개념(Concept) 레이어**: cross-corpus 참조가 조특법↔조특령에 편중돼 있어, 그 외 문서 간 연결은
  참조가 아니라 **개념 노드가 다리**를 놓는다(EDA로 입증, [`docs/eda_report.md`](docs/eda_report.md)).
- **순환검증 회피**: QA 셋을 그래프 구축·충돌 탐지와 분리해 평가 무결성 확보.

---

## 6. 평가 방법론

- **평가셋**: `eval/gold_set_final.json` — 사람 G4 전수검수 **78문항**, 6유형(문서간충돌 26 / 단일정밀 18 /
  일반 10 / 동명무충돌 10 / 무응답 8 / 용도상이 6), hop1~3 · 출처(held_out 45 / generated 33).
- **대조군**: 강화 Naive RAG — 청크 예산을 GraphRAG와 일치시키고 조문 중복 제거로 다양화, **동일 모델(gpt-4o)** 로 생성.
- **5단 ablation(B0~B4)**: 검색→프롬프트→재시도→그래프→앵커를 하나씩 더해 **우위의 원인을 요인별로 귀인**.
- **지표(7+)**: Context Recall(조 OLD / 항·호 NEW, hop별) · Conflict P/R/F1(결정론 flag) · 과탐율 ·
  Answer Accuracy · Faithfulness · Abstention · **Path Faithfulness**(경로 재현·우연일치·연결정밀) · 사실성.
- **데이터 게이트 G0~G6**(`eval/dataset_gates.py`)로 평가셋 무결성 검증, 작성 원자료는 `eval/gold_authoring/`.
- 정의·지침: [`eval/평가메트릭_정의서.md`](eval/평가메트릭_정의서.md).

**정직성 메모(요약)** — (1) 조 단위 매칭은 Recall을 과대평가할 수 있어 **항/호 정밀(NEW)** 을 병기한다(두 채점기
문서명 매핑 일원화 완료). (2) 충돌 문항 Recall 우위 일부는 설계상 정준쌍 주입에 기인 → **held_out 출처로 통제**
(단 충돌 held_out n=8 잠정). (3) **앵커 없는 일반 그래프 확장은 net 손해**이며 우위는 앵커에 귀속된다(억지로
"그래프 만능"으로 포장하지 않음). (4) distractor **과탐(FP 14)** 이 최대 한계. (5) LLM-judge는 도메인 진위를
검증하지 못한다. 전체 한계·실패 사례는 [`docs/exp_plan.md`](docs/exp_plan.md) §8에 전수 기록.

---

## 7. 기술 스택

- **Graph DB**: Neo4j 5 + Cypher (벡터 인덱스)
- **Agent**: LangGraph
- **LLM**: OpenAI GPT-4o(추론·Naive 생성), GPT-4o-mini(라우팅·검증·judge)
- **Embedding**: OpenAI `text-embedding-3-small`
- **PDF 파싱**: pdfplumber
- **Python**: 3.11+

---

## 8. 실행 방법

```bash
# 1) 환경
pip install -r requirements.txt
#   .env 에 OPENAI_API_KEY, NEO4J_URI/USER/PASSWORD 설정

# 2) Neo4j 실행 (Docker)
docker compose -f graph/docker-compose.yml up -d

# 3) (Step 1~2) 파싱·청킹·개념/충돌/조건 — 필요 시 재생성
python notebooks/01_pdf_to_md.py
python notebooks/02_eda.py
python notebooks/02_chunking.py
python notebooks/02_concepts_conflicts.py
python notebooks/02b_curate_conflicts.py
python notebooks/02_conditions.py

# 4) (Step 3) 임베딩·그래프 적재
python graph/build_embeddings.py
python graph/build_graph.py
python graph/populate_embeddings.py

# 5) (Step 4) 에이전트 실행
python agent/app.py

# 6) 실험 · 평가 (78문항 최종셋)
python eval/run_experiment.py     # GraphRAG vs Naive   → eval/_exp_results.json
python eval/ablation_ladder.py    # B0~B4 ablation       → eval/_ablation_results.json
python eval/attribution_metrics.py# 요인 귀인 분해        → eval/_attribution_results.json
python eval/score_precision.py    # 조 vs 항/호 정밀 채점 → eval/_precision_results.json
python eval/path_metric.py        # 경로 충실도          → eval/_path_metric_results.json
python eval/score_factuality.py   # 사실성               → eval/_factuality_results.json
```

---

## 9. 문서 안내

| 문서 | 내용 |
|------|------|
| [`docs/exp_plan.md`](docs/exp_plan.md) | **최종 실험계획서/보고서**(78문항·ablation·7메트릭·결과분석·15분 발표 가이드) |
| [`docs/exp_report.md`](docs/exp_report.md) | 1차 실험 보고서(18문항, 설계·결과·한계) |
| [`docs/study.md`](docs/study.md) | GraphRAG가 무엇이고 왜 이 데이터셋에 유리한지 — 개념 중심 학습 문서 |
| [`docs/eda_report.md`](docs/eda_report.md) | EDA 상세(문서 통계·참조 분포·충돌 희소성) |
| [`docs/개선.md`](docs/개선.md) · [`docs/개선_report.md`](docs/개선_report.md) | 개선 로드맵(메트릭 정밀화·데이터 강화) 계획·진행 기록 |
| [`docs/데이터셋_확장가이드.md`](docs/데이터셋_확장가이드.md) | 데이터셋 확장(78문항) 작성 가이드 |
| [`data/graph_schema.md`](data/graph_schema.md) | 노드/엣지 스키마 명세 |
| [`guide.md`](guide.md) | 전체 파이프라인 가이드라인 |

---

## 팀

Mini AIFFELTHON 프로젝트.
