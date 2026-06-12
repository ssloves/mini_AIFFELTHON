# G4 사람 검수 로그 (gold_set_v2 → gold_set_final)
- 입력 80문항 → 출력 78문항 (폐기 2).

## 폐기
- v2#13 [seed#7]: 메인공제 인원감소 추징(코퍼스 미근거) + seed#3와 사실상 중복. ④⑤ 추징은 별도 문항이 커버.
- v2#75 [track1_conflict#청년 연령 기준]: 인용 조문(조특령§81=주택청약저축, 고보령§17=고용창출 임금지원)이 청년연령과 무관 + gold_answer 내용 공백. 청년정의 비교는 #78이 정상 커버.

## 전 문항 판정
| final_id | provenance | 판정 | type | hop | ans | review | 비고 | 게이트플래그 |
|---|---|---|---|---|---|---|---|---|
| 1 | seed#5 | FIX | 단일정밀 | 1 | True | False | G4 정정 적용; hop 3->1 |  |
| 2 | seed#11 | FIX | 문서간충돌 | 2 | True | False | hop 3->2(조 기준 재계산) |  |
| 3 | seed#1 | OK | 문서간충돌 | 2 | True | False |  |  |
| 4 | seed#2 | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용 |  |
| 5 | seed#3 | FIX | 일반 | 1 | True | False | G4 정정 적용; hop 2->1 |  |
| 6 | seed#4 | FIX | 단일정밀 | 1 | True | False | hop 2->1(조 기준 재계산) |  |
| 7 | seed#8 | OK | 문서간충돌 | 2 | True | False |  |  |
| 8 | seed#10 | OK | 문서간충돌 | 2 | True | False |  |  |
| 9 | seed#13 | OK | 동명무충돌 | 2 | True | False |  |  |
| 10 | seed#16 | FIX | 일반 | 1 | True | False | G4 정정 적용; hop 2->1 |  |
| 11 | seed#17 | OK | 동명무충돌 | 2 | True | False |  |  |
| 12 | seed#6 | OK | 단일정밀 | 1 | True | False |  |  |
| 13 | seed#9 | OK | 단일정밀 | 1 | True | False |  |  |
| 14 | seed#12 | FIX | 단일정밀 | 1 | True | False | G4 정정 적용 |  |
| 15 | seed#14 | OK | 단일정밀 | 1 | True | False |  |  |
| 16 | seed#15 | OK | 단일정밀 | 1 | True | False |  |  |
| 17 | seed#18 | OK | 일반 | 1 | True | False |  |  |
| 18 | 팀원3.md#21 | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용; hop 3->2 |  |
| 19 | 팀원3.md#34 | FIX | 동명무충돌 | 3 | True | False | G4 정정 적용 |  |
| 20 | 팀원3.md#20 | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용 |  |
| 21 | 팀원3.md#36 | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용 |  |
| 22 | 팀원4.md#57 | FIX | 단일정밀 | 2 | True | False | G4 정정 적용 |  |
| 23 | 팀원3.md#19 | OK | 동명무충돌 | 2 | True | False |  |  |
| 24 | 팀원3.md#33 | OK | 동명무충돌 | 2 | True | False |  |  |
| 25 | 팀원3.md#22 | OK | 용도상이 | 2 | True | False |  |  |
| 26 | 팀원3.md#26 | FIX | 용도상이 | 2 | True | False | G4 정정 적용 |  |
| 27 | 팀원3.md#28 | OK | 용도상이 | 2 | True | False |  |  |
| 28 | 팀원3.md#24 | OK | 단일정밀 | 1 | True | False |  |  |
| 29 | 팀원3.md#25 | OK | 단일정밀 | 1 | True | False |  |  |
| 30 | 팀원3.md#27 | OK | 단일정밀 | 1 | True | False |  |  |
| 31 | 팀원3.md#29 | OK | 단일정밀 | 1 | True | False |  |  |
| 32 | 팀원3.md#30 | OK | 단일정밀 | 1 | True | False |  |  |
| 33 | 팀원3.md#37 | FIX | 단일정밀 | 1 | True | False | G4 정정 적용 |  |
| 34 | 팀원4.md#47 | OK | 단일정밀 | 1 | True | False |  |  |
| 35 | 팀원4.md#48 | OK | 단일정밀 | 1 | True | False |  |  |
| 36 | 팀원3.md#31 | OK | 무응답 | 1 | False | False |  |  |
| 37 | 팀원3.md#32 | OK | 무응답 | 1 | False | False |  |  |
| 38 | 팀원3.md#38 | OK | 무응답 | 1 | False | False |  |  |
| 39 | 팀원4.md#39 | OK | 무응답 | 1 | False | False |  |  |
| 40 | 팀원4.md#40 | OK | 무응답 | 1 | False | False |  |  |
| 41 | 팀원4.md#41 | OK | 무응답 | 1 | False | False |  |  |
| 42 | 팀원4.md#42 | OK | 무응답 | 1 | False | False |  |  |
| 43 | 팀원4.md#43 | OK | 무응답 | 1 | False | False |  |  |
| 44 | 팀원2#H20 | FIX | 문서간충돌 | 2 | True | False | hop 3->2(조 기준 재계산) |  |
| 45 | track1_hop3#1 | OK | 문서간충돌 | 3 | True | False |  |  |
| 46 | track1_hop3#3 | OK | 문서간충돌 | 3 | True | False |  |  |
| 47 | track1_hop3#4 | OK | 문서간충돌 | 3 | True | False |  |  |
| 48 | track1_hop3#6 | OK | 문서간충돌 | 3 | True | False |  |  |
| 49 | track1_hop3#7 | OK | 문서간충돌 | 3 | True | False |  |  |
| 50 | track1_hop3#8 | OK | 문서간충돌 | 3 | True | False |  |  |
| 51 | track1_hop3#9 | OK | 문서간충돌 | 3 | True | False |  |  |
| 52 | 팀원2#H4 | OK | 문서간충돌 | 2 | True | False |  |  |
| 53 | 팀원2#H8 | FIX | 문서간충돌 | 1 | True | False | hop 2->1(조 기준 재계산) |  |
| 54 | 팀원2#H19 | OK | 문서간충돌 | 2 | True | False |  |  |
| 55 | track1_conflict#상시근로자 수 산정 | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용 |  |
| 56 | track1_conflict#상시근로자 수 산정 | OK | 문서간충돌 | 2 | True | False |  |  |
| 57 | track1_conflict#단시간 근로자 제외 | OK | 문서간충돌 | 2 | True | False |  |  |
| 58 | 팀원2#H13 | OK | 동명무충돌 | 2 | True | False |  |  |
| 59 | track1_동명1 | OK | 동명무충돌 | 2 | True | False |  |  |
| 60 | track1_동명2 | OK | 동명무충돌 | 2 | True | False |  |  |
| 61 | track1_동명3 | OK | 동명무충돌 | 2 | True | False |  |  |
| 62 | track1_동명4 | OK | 동명무충돌 | 2 | True | False |  |  |
| 63 | track1_용도1 | OK | 용도상이 | 2 | True | False |  |  |
| 64 | track1_용도2 | OK | 용도상이 | 2 | True | False |  |  |
| 65 | track1_용도3 | OK | 용도상이 | 2 | True | False |  |  |
| 66 | 팀원2#H18 | FIX | 일반 | 1 | True | False | hop 2->1(조 기준 재계산) |  |
| 67 | 팀원2#H3 | OK | 일반 | 1 | True | False |  |  |
| 68 | 팀원2#H7 | OK | 일반 | 1 | True | False |  |  |
| 69 | track1_일반1 | OK | 일반 | 1 | True | False |  |  |
| 70 | track1_일반2 | OK | 일반 | 1 | True | False |  |  |
| 71 | track1_일반3 | OK | 일반 | 1 | True | False |  |  |
| 72 | track1_일반4 | OK | 일반 | 1 | True | False |  |  |
| 73 | track1_conflict#1년 미만 계약직  | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용 |  |
| 74 | track1_conflict#장려금 피보험자 수 | FIX | 문서간충돌 | 2 | True | False | G4 정정 적용 |  |
| 75 | track1_hop3#2 | OK | 문서간충돌 | 2 | True | False |  |  |
| 76 | track1_hop3#5 | OK | 문서간충돌 | 2 | True | False |  |  |
| 77 | 팀원4.md#49 | OK | 단일정밀 | 1 | True | False |  |  |
| 78 | 팀원4.md#51 | OK | 단일정밀 | 1 | True | False |  |  |
