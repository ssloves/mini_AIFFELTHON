# ⚖️ Legal GraphRAG — 법률 문서 연쇄 추론 시스템

> **GraphRAG 기반 법률 자문 시스템**: 법률·시행령·행정지침 간의 논리적 관계를 그래프로 구조화하고, 다중 문서 연쇄 추론(multi-hop reasoning)을 통해 **근거가 추적 가능한** 법률 답변을 생성합니다.

---

## 🎯 프로젝트 목표

**핵심 주장**: GraphRAG는 더 똑똑한 답이 아니라, 더 **신뢰할 수 있는** 답 — 근거가 추적되고, 연쇄가 깊어져도 누락되지 않는 답 — 을 낸다.

| 지표 | 일반 RAG | GraphRAG (Ours) |
|------|---------|----------------|
| 근거 추적 | ❌ 불가 | ✅ 탐색 경로 제공 |
| Multi-hop 추론 | ⚠️ 1-2 hop 한계 | ✅ 3+ hop 지원 |
| 충돌 감지 | ❌ 불가 | ✅ CONFLICTS_WITH 엣지 |
| 환각 검증 | ❌ 없음 | ✅ Verifier 노드 |

---

## 🏗️ 아키텍처

```
[사용자 질문]
     │
     ▼
┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Router  │────▶│  Retriever   │────▶│  Reasoner    │────▶│  Verifier    │
│ (분류)    │     │ (벡터+그래프) │     │ (추론)       │     │ (검증)       │
└──────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                        ▲                                         │
                        │          REJECT (리젝 시 회귀)           │
                        └─────────────────────────────────────────┘
                                                                  │ ACCEPT
                                                                  ▼
                                                          ┌──────────────┐
                                                          │   Output     │
                                                          │ (구조화 응답)  │
                                                          └──────────────┘
```

---

## 📂 레포지토리 구조

```
mini_AIFFELTHON/
│
├── raw_data/                      # 원본 PDF 법률 문서 5종
│
├── data/
│   ├── processed/                 # [Step 1] PDF → Markdown 정제본
│   ├── chunks/                    # [Step 2] 법률 구조 인식 청킹 결과
│   └── graph_schema.md            # [Step 2] 노드/엣지 스키마 정의서
│
├── graph/
│   ├── build_graph.py             # [Step 3] Neo4j 그래프 구축
│   ├── populate_embeddings.py     # [Step 3] 벡터 임베딩 적재
│   └── neo4j_snapshot/            # [Step 3] 그래프 DB 스냅샷
│
├── agent/
│   ├── graph_state.py             # [Step 4] LangGraph 상태 정의
│   ├── nodes/                     # Router, Retriever, Reasoner, Verifier
│   ├── prompts/                   # 시스템 프롬프트
│   └── app.py                     # LangGraph 앱 진입점
│
├── eval/
│   ├── qa.md                      # 평가용 QA 셋 (10개, gold answer + 근거 라벨링)
│   └── evaluate.py                # 자동 평가 스크립트
│
├── notebooks/                     # EDA 및 실험용 노트북
├── guide.md                       # 파이프라인 가이드라인
└── requirements.txt
```

---

## 📊 대상 문서셋

| # | 문서 | 약칭 |
|---|------|------|
| 1 | 2025년 청년일자리도약장려금 사업운영 지침 일부 개정안 | **장려금지침** |
| 2 | 고용보험법 시행령 (제36306호) | **고용보험법시행령** |
| 3 | 근로기준법 시행령 (제35436호) | **근로기준법시행령** |
| 4 | 조세특례제한법 (제21738호) | **조특법** |
| 5 | 조세특례제한법 시행령 (제36338호) | **조특법시행령** |

---

## 🔄 파이프라인 요약

| Step | 작업 | 결과물 |
|------|------|--------|
| **Step 1** | PDF → 구조화된 Markdown 변환 | `data/processed/*.md` |
| **Step 2** | EDA + 법률 구조 인식 청킹 + 그래프 스키마 설계 | `data/chunks/all_chunks.jsonl` + `data/graph_schema.md` |
| **Step 3** | Neo4j 그래프 구축 + 벡터 인덱스 | `graph/neo4j_snapshot/` |
| **Step 4** | LangGraph 에이전트 (Router→Retriever→Reasoner→Verifier→Output) | `agent/app.py` |

> 각 Step의 상세 가이드라인은 [guide.md](guide.md)를 참조하세요.

---

## 📈 평가

10개 QA 항목으로 평가하며, 각 항목에는 **정답(gold answer)**, **정답 근거(gold source)**, **hop 수**, **충돌 유형**이 라벨링되어 있습니다.

| hop 분포 | 충돌 유형 분포 |
|----------|-------------|
| hop 1: 1개 / hop 2: 5개 / hop 3: 4개 | 충돌있음: 6개 / 충돌없음(distractor): 4개 |

**핵심 평가 지표**: Context Recall · Answer Accuracy · Faithfulness · Conflict Detection

> QA 셋 상세: [eval/qa.md](eval/qa.md)

---

## 🛠️ 기술 스택

- **Graph DB**: Neo4j + Cypher
- **Agent Framework**: LangGraph
- **LLM**: OpenAI GPT-4o (추론/검증)
- **Embedding**: OpenAI text-embedding-3-small
- **PDF 파싱**: pdfplumber
- **언어**: Python 3.11+

---

## 🚀 실행 방법

```bash
# 1. 환경 설정
pip install -r requirements.txt

# 2. Neo4j 실행 (Docker)
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password neo4j:5

# 3. 그래프 구축
python graph/build_graph.py
python graph/populate_embeddings.py

# 4. 에이전트 실행
python agent/app.py

# 5. 평가
python eval/evaluate.py
```

---

## 👥 팀

Mini AIFFELTHON 프로젝트
