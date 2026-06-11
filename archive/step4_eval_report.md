# Step 4 — 에이전트 평가 리포트

GraphRAG 에이전트(Router→Retriever→Reasoner→Verifier)와 Naive RAG(벡터 top-k) 비교.
지표 정의는 `guide.md` §6.2. 평가셋: `eval/gold_set.jsonl`(충돌 4 + distractor 2).

## 문항별 결과

| id | 유형 | route | verdict | 재시도 | Recall(Graph) | Recall(Naive) | Conflict | Hop |
|----|------|-------|---------|------|------|------|------|-----|
| Q2 | conflict | conflict | ACCEPT | 0 | 1.00 | 0.50 | OK | OK |
| Q1 | conflict | conflict | ACCEPT | 0 | 1.00 | 0.00 | OK | OK |
| Q10 | conflict | conflict | ACCEPT | 0 | 1.00 | 0.00 | OK | OK |
| Q11 | conflict | multihop | ACCEPT | 0 | 1.00 | 0.00 | OK | OK |
| Q15 | distractor | simple | ACCEPT | 0 | 1.00 | 1.00 | OK | - |
| Q18 | distractor | simple | ACCEPT | 0 | 1.00 | 1.00 | OK | - |

## 집계

- **평균 Context Recall**: GraphRAG **1.00** vs Naive **0.42** (Δ **+0.58**)
- **Conflict Detection**(distractor 과잉탐지 미발생 포함): **1.00**
- **Hop Coverage**(hop≥2 문항 다문서 수집): **1.00**
- **Verifier ACCEPT 비율**: **1.00**

## 해석
- Naive RAG는 Q1/Q10/Q11에서 정준 충돌 조문(고보 §3·지침·근기 §7의2·조특령 §26의8)을 벡터 유사도만으로 못 잡아 Recall 0 — 벡터 top-1이 부수 조문(예: 조특령 §136의2)에 끌린다.
- GraphRAG는 **개념 구동 충돌 앵커**(질문 개념→CONFLICTS_WITH 정준 쌍 직접 주입)로 두 충돌 조문을 항상 컨텍스트 상단에 올려 Recall 1.0·충돌 100% 감지.
- distractor(Q15/Q18)는 simple 라우트로 충돌 앵커가 발동하지 않아 과잉탐지 0.
- 인용 보정(조문 단위 정규화)으로 `chunk_id` 표기 차이에 의한 거짓 환각 판정을 제거.