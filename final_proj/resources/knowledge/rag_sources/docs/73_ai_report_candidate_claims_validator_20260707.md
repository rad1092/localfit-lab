# 73. AI 리포트 후보 evidence 금지표현 validator

작성일: 2026-07-07T21:43:37

## 목적

후보 evidence가 AI 리포트에서 성공확률, 매출 보장, 실제 이동시간, 월세/권리금 직접값처럼 과장되는 것을 막기 위해 registry 기반 Markdown validator를 만들었다.

## 요약

- validation version: `ai_report_candidate_claims_validator.v0.1-20260707`
- forbidden terms: 83
- safe sample violations: 0
- unsafe sample violations: 8
- external markdown checked: False
- PASS: 8
- FAIL: 0
- decision: `AI_REPORT_CANDIDATE_CLAIMS_VALIDATOR_PASS`

## 금지표현 샘플

| term | source | evidence_id |
| --- | --- | --- |
| 개별 매장 매출 보장 | base_contract | global |
| 개별 매장 매출 보장 | candidate_payload | localdata_food_license_open_close |
| 개별 매장 매출 보장 | candidate_registry | localdata_food_license_open_close |
| 개별 매장 생존율 | candidate_payload | admin_stats_kosis_sgg_reference |
| 개별 매장 생존율 | candidate_registry | admin_stats_kosis_sgg_reference |
| 개별 매출 | candidate_payload | admin_stats_sgis_emd_candidate |
| 개별 매출 | candidate_registry | admin_stats_sgis_emd_candidate |
| 개별 수익성 | candidate_payload | cost_risk_rtms_trade_candidate |
| 개별 수익성 | candidate_registry | cost_risk_rtms_trade_candidate |
| 개별 점포 월세 | candidate_payload | cost_risk_rone_region_candidate |
| 개별 점포 월세 | candidate_payload | cost_risk_rtms_trade_candidate |
| 개별 점포 월세 | candidate_registry | cost_risk_rone_region_candidate |
| 개별 점포 월세 | candidate_registry | cost_risk_rtms_trade_candidate |
| 개별 창업 성공확률 | candidate_payload | admin_stats_kosis_sgg_reference |
| 개별 창업 성공확률 | candidate_registry | admin_stats_kosis_sgg_reference |
| 공식 예산 점수 직접값 | candidate_payload | cost_risk_rone_region_candidate |
| 공식 예산 점수 직접값 | candidate_payload | cost_risk_rtms_trade_candidate |
| 공식 예산 점수 직접값 | candidate_registry | cost_risk_rone_region_candidate |
| 공식 예산 점수 직접값 | candidate_registry | cost_risk_rtms_trade_candidate |
| 공식 점수 근거 | candidate_payload | localdata_food_license_open_close |
| 공식 점수 근거 | candidate_registry | localdata_food_license_open_close |
| 권리금 직접값 | base_contract | global |
| 권리금 직접값 | candidate_payload | cost_risk_rtms_trade_candidate |
| 권리금 직접값 | candidate_registry | cost_risk_rtms_trade_candidate |
| 권리금 확정값 | candidate_payload | cost_risk_rone_region_candidate |
| 권리금 확정값 | candidate_registry | cost_risk_rone_region_candidate |
| 권장 | strict_single_word | global |
| 매출 보장 | base_contract | global |
| 매출 상승 보장 | base_contract | global |
| 매출 유입 | candidate_payload | bus_network_diversity_snapshot_candidate |
| 매출 유입 | candidate_registry | bus_network_diversity_snapshot_candidate |
| 상권 직접 사업체수 | base_contract | global |
| 상권 직접 사업체수 | candidate_payload | admin_stats_sgis_emd_candidate |
| 상권 직접 사업체수 | candidate_registry | admin_stats_sgis_emd_candidate |
| 상권 직접 인구 | base_contract | global |
| 상권 직접 인구 | candidate_payload | admin_stats_sgis_emd_candidate |
| 상권 직접 인구 | candidate_registry | admin_stats_sgis_emd_candidate |
| 상권 직접값 | candidate_payload | admin_stats_kosis_sgg_reference |
| 상권 직접값 | candidate_registry | admin_stats_kosis_sgg_reference |
| 생존확률 | base_contract | global |

## 검증 결과

| validation_id | validation_name | observed | expected | result | reason_ko |
| --- | --- | --- | --- | --- | --- |
| 73-V01 | 금지표현 registry 생성 | 83 | >=20 | PASS | 후보 evidence와 공통 계약에서 충분한 금지표현을 추출해야 한다. |
| 73-V02 | registry source 다양성 | ['base_contract', 'candidate_payload', 'candidate_registry', 'strict_single_word'] | base/registry/payload 포함 | PASS | 정적 금지어만으로는 후보 evidence 금지표현을 모두 덮지 못한다. |
| 73-V03 | 안전 샘플 통과 | 0 | 0 | PASS | 신중한 한계 표현은 validator가 통과시켜야 한다. |
| 73-V04 | 위반 샘플 탐지 | ['권리금 직접값', '성공확률', '수익성 보장', '실제 방문자', '월세 직접값', '적합', '창업 성공확률', '추천'] | 창업 성공확률 등 4개 이상 탐지 | PASS | validator가 과장 표현을 실제로 잡는지 확인한다. |
| 73-V05 | 단정 표현 탐지 | ['권리금 직접값', '성공확률', '수익성 보장', '실제 방문자', '월세 직접값', '적합', '창업 성공확률', '추천'] | 추천/적합 탐지 | PASS | 추천/적합 같은 단정형 표현은 후보 evidence 리포트에서 막아야 한다. |
| 73-V06 | 후보 payload 금지표현 반영 | 29 | >=7 | PASS | 72번 payload의 section별 금지표현이 validator에 반영되어야 한다. |
| 73-V07 | 외부 Markdown 선택 검증 | not_provided | 미제공이면 skip, 제공 시 위반 0 | PASS | 실제 생성 Markdown 파일을 넘기면 같은 validator로 검사할 수 있어야 한다. |
| 73-V08 | 비기계적 규칙 검증 5개 이상 | V02,V03,V04,V05,V06,V07 | source다양성/안전통과/위반탐지/단정탐지/payload반영/외부검증 | PASS | 단순 파일 존재가 아니라 리포트 과장 방지 규칙이 작동하는지 검증했다. |

## 2보 전진 1보 후퇴

전진:

1. 71번 registry와 72번 payload의 금지표현을 Markdown validator로 연결했다.
2. 안전 샘플은 통과하고 위반 샘플은 탐지되는지 검증했다.

후퇴:

1. 후보 evidence 문장을 추천/적합/성공확률 표현으로 쓰지 못하게 막았다.
2. 실제 Markdown 파일이 없으면 생성 성공으로 꾸미지 않고 선택 검증으로 남겼다.

## 결론

AI 리포트 생성 후 Markdown/PDF 변환 전에는 이 validator를 통과해야 한다. 후보 evidence는 설명 보조이며 공식 점수나 성공 보장 문구로 바뀌면 안 된다.
