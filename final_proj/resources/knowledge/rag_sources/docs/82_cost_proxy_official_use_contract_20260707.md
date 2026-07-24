# 82. 비용 리스크 프록시 공식 사용 계약 검증

생성일: 2026-07-07T21:48:40

## 목적

RTMS, R-ONE, 중개업소 데이터가 비용 리스크 판단에 쓰일 수는 있지만 월세·권리금·수익성 직접 판단으로 오해되면 안 된다. 이번 검증은 비용 데이터를 공식 알고리즘에 넣을 수 있는 범위를 코드와 gold 파일 기준으로 고정한다.

## 결론

- decision: `COST_PROXY_OFFICIAL_USE_CONTRACT_PASS_SEPARATE_PROXY_SCORE`
- PASS: 12
- FAIL: 0
- 현재입지 공식축: `sales, competition, demand, accessibility`
- RTMS gold rows: 9,900
- RTMS quarter range: `20251~20262`
- R-ONE candidate rows: 77,061
- broker candidate rows: 25

## 판정

- RTMS는 자치구 단위 상업·업무용 매매가격 기반 비용 압력 프록시다.
- `cost_risk_score`는 별도 점수로만 출력한다.
- 현재입지 총점은 `sales`, `competition`, `demand`, `accessibility` 네 축만 사용한다.
- R-ONE은 권역/상가유형 기준선 또는 상권명 후보 evidence로만 유지한다.
- 중개업소 후보는 스냅샷 보조 신호이므로 공식 점수와 과거 백테스트에 넣지 않는다.
- 금지 표현: 월세 반영, 권리금 반영, 임대수익 확정, 개별 매장 수익성, 창업 성공확률.

## 검증 결과

| validation_id | validation_name | observed | expected | result | reason_ko |
| --- | --- | --- | --- | --- | --- |
| 82-V01 | 비용 지표 명세가 비용축·비용형·자치구 grain으로 고정 | axis=cost_risk; direction=cost; grain=district | axis=cost_risk; direction=cost; grain=district | PASS | RTMS 비용 프록시는 상권 직접값이 아니라 자치구 비용 압력 지표이므로 별도 비용축과 비용형 반전을 유지해야 한다. |
| 82-V02 | 현재입지 공식 4축에서 비용축 제외 | sales,competition,demand,accessibility | sales,competition,demand,accessibility only | PASS | 비용 리스크는 개별 점포 수익성 직접 판단이 아니므로 현재입지 WLC 점수에 섞지 않고 별도 출력한다. |
| 82-V03 | 현재입지 가중치 로더의 비용축 제외 | {'BASE': ['accessibility', 'competition', 'demand', 'sales'], 'CS1': ['accessibility', 'competition', 'demand', 'sales'], 'CS2': ['accessibility', 'competition', 'demand', 'sales'], 'CS3': ['accessibility', 'competition', 'demand', 'sales']} | 모든 weight_set에서 CURRENT_AXES만 사용 | PASS | 가중치 파일에 비용 관련 값이 있더라도 엔진은 현재입지 4축만 재정규화해야 한다. |
| 82-V04 | 현재입지 계산 함수가 CURRENT_AXES만 사용 | True | CURRENT_AXES 기반, cost_risk 직접 참조 없음 | PASS | 공식 현재입지 점수 함수 내부에서 비용축을 참조하지 않아야 향후 수정 때도 섞이지 않는다. |
| 82-V05 | RTMS gold 직접값 금지 플래그 | direct=['False']; proxy=['True'] | direct_score_allowed all False; proxy_score_allowed all True | PASS | RTMS는 매매가격 기반 비용 압력 프록시일 뿐 월세·권리금 직접값이 아니므로 직접점수 플래그는 false여야 한다. |
| 82-V06 | RTMS fan-out 구조 보존 | rows=9900; quarters=6; areas=1650; duplicate_keys=0 | quarter×trade_area unique, quarter×district 값 단일 | PASS | 같은 자치구·분기의 모든 상권이 같은 RTMS 값을 가져야 하며, 상권별 직접 월세처럼 변형되면 안 된다. |
| 82-V07 | RTMS 금지문구와 프록시 설명 보존 | {'forbidden_terms_ok': True, 'proxy_reason_has_rtms': np.True_} | 임대료/권리금 직접값 아님 + 프록시 사유 | PASS | AI 리포트가 비용 점수를 월세·권리금 반영 수익성으로 오해하지 않게 금지문구가 gold에 있어야 한다. |
| 82-V08 | R-ONE 후보 공식 승격 금지 | direct=['False']; engine=['False']; scopes=['rone_level3_name_match_candidate', 'seoul_baseline_reference'] | direct False, engine_promotion_ready False, 기준선/후보 범위 분리 | PASS | R-ONE은 권역·상가유형 집계 기준선이므로 상권 직접 비용점수로 자동 승격하지 않는다. |
| 82-V09 | 중개업소 후보 공식 점수/백테스트 투입 금지 | direct=['False']; engine=['False']; backtest=['False'] | direct False, engine_score_allowed False, valid_for_backtest False | PASS | 중개업소 수는 2026년 스냅샷이고 월세·권리금 직접값도 아니므로 과거 라벨 백테스트와 공식 점수에 넣지 않는다. |
| 82-V10 | 백테스트 문구 금지 감사 유지 | {'PASS': 2} | FAIL 없음 | PASS | 점수 등급과 리포트 계약에서 창업 성공확률, 매출 보장, 월세/권리금 반영 수익성 표현을 금지해야 한다. |
| 82-V11 | 비용축은 성능지표에 별도 component로만 존재 | [{'component': 'cost_risk', 'non_null_rows': 83445}] | cost_risk component exists, current_location_score와 별도 | PASS | 비용축 상관은 참고로 계산할 수 있지만 현재입지 공식 점수의 4축 가중합과는 분리되어야 한다. |
| 82-V12 | payload 점수 구조에서 비용은 별도 필드 | True | cost_risk_score 별도, axis_scores는 CURRENT_AXES | PASS | AI 리포트 입력 payload에서도 비용 리스크를 현재입지 축처럼 합산하지 않고 별도 점수로 넘겨야 한다. |

## 2보 전진 1보 후퇴

1. 전진: RTMS gold가 비용 리스크 별도 점수로는 사용 가능함을 코드와 플래그로 확인했다.
2. 전진: R-ONE과 중개업소 후보를 버리지 않고 evidence-only 후보 계층으로 보존했다.
3. 후퇴: 비용 축은 현재입지 총점에 합산하지 않는다. 월세·권리금·수익성 판단으로도 표현하지 않는다.

## 산출물

- `datacorpus\_rule_validation\82_cost_proxy_official_use_contract_validation.csv`
- `datacorpus\_rule_validation\82_cost_proxy_official_use_contract_summary.json`
- `research\rule_validation\82_cost_proxy_official_use_contract_20260707.md`
