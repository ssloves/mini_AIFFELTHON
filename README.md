# Legal GraphRAG — 한국 노동·세법 연쇄 추론 시스템

> 법률·시행령·행정지침 5종 사이의 논리적 관계를 **지식 그래프**로 구조화하고, 다중 문서
> 연쇄 추론(multi-hop)과 **충돌 감지**를 통해 *근거가 추적 가능한* 법률 답변을 생성하는 GraphRAG 에이전트.

**핵심 주장 (실험으로 검증)**
> GraphRAG는 *더 똑똑한 답*이 아니라, **근거를 덜 놓치는 더 신뢰할 수 있는 답**을 낸다.
> → 1순위 지표는 Answer Accuracy가 아니라 **Context Recall**.

---

## 1. 한눈에 보는 결과

동일 청크·임베딩을 쓰는 **강화 Naive RAG**(예산 일치 + 조문 다양화)와 18문항으로 비교했다.
(상세·재현: [`docs/exp_report.md`](docs/exp_report.md), 원자료 [`eval/_exp_results.json`](eval/_exp_results.json))

| 지표 | GraphRAG (Ours) | Naive (강화) | 차이 |
|------|:---:|:---:|:---:|
| **Context Recall (전체, 조 단위)** | **0.815** | 0.574 | **+0.241** |
| **Answer Accuracy** (LLM-judge) | **0.806** | 0.450 | **+0.356** |
| **Faithfulness** (LLM-judge) | **0.933** | 0.717 | **+0.216** |
| Conflict Detection (cross-doc 7, flag+서술) | 0.857 | — | — |
| Conflict 오탐률 (distractor 6) | 0.500 | 0.500 | — |

- ✅ **검증됨**: 그래프 확장이 근거 수집률을 높이고(+0.241), 그것이 답변 정확도·충실성 향상으로 이어진다.
- ⚠️ **반증/한계**: "hop이 깊을수록 격차가 벌어진다"는 가설은 본 데이터로 **지지되지 않음**(hop3에서 격차 최소,
  표본 2건). distractor **과잉탐지(0.5)** 는 명목/실질 충돌 미구분에서 발생 — 개선 과제.

> 모든 수치는 1회 실행 결과이며, 측정·데이터의 한계와 실패 사례는 보고서에 **전수 기록**되어 있다.

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
├── eval/                  # gold_set_원본.jsonl(18), 평가메트릭_정의서, run_experiment, _exp_results
├── docs/                  # 리포트: study.md(학습), eda_report.md(EDA), exp_report.md(실험)
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

- **평가셋**: `eval/gold_set_원본.jsonl` 18문항(충돌 12 / distractor 6, hop1=4·hop2=12·hop3=2).
- **대조군**: 강화 Naive RAG — 청크 예산을 GraphRAG와 일치시키고 조문 중복 제거로 다양화, **동일 모델(gpt-4o)** 로 생성.
- **채점 단위**: 조(條) 단위 매칭(gold `제3조제1항제2호` → `(문서, 제3조)`로 정규화). 지침은 문서 단위.
- **지표**: Context Recall(전체/hop별) · Conflict Detection(cross-doc 7) · Conflict 오탐률(distractor 6)
  · Answer Accuracy(judge) · Faithfulness(judge).
- 정의·지침: [`eval/평가메트릭_정의서.md`](eval/평가메트릭_정의서.md).

**정직성 메모(요약)** — 조 단위·지침 문서 단위 매칭은 Recall을 과대평가할 수 있고(실패 사례 6번),
충돌 문항 Recall 우위 일부는 설계상 정준쌍 주입에 기인하며, LLM-judge는 도메인 사실의 진위를 검증하지 못한다.
전체 한계·실패 사례는 [`docs/exp_report.md`](docs/exp_report.md) §8~9에 기록.

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

# 6) 실험(GraphRAG vs Naive, 18문항)
python eval/run_experiment.py     # → eval/_exp_results.json
```

---

## 9. 문서 안내

| 문서 | 내용 |
|------|------|
| [`docs/study.md`](docs/study.md) | GraphRAG가 무엇이고 왜 이 데이터셋에 유리한지 — 개념 중심 학습 문서 |
| [`docs/eda_report.md`](docs/eda_report.md) | EDA 상세(문서 통계·참조 분포·충돌 희소성) |
| [`docs/exp_report.md`](docs/exp_report.md) | 실험 보고서(설계·결과·분석·한계·실패 사례) |
| [`data/graph_schema.md`](data/graph_schema.md) | 노드/엣지 스키마 명세 |
| [`guide.md`](guide.md) | 전체 파이프라인 가이드라인 |

---

## 팀

Mini AIFFELTHON 프로젝트.
