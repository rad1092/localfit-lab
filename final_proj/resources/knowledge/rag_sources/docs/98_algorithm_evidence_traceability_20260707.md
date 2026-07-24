# 98. 알고리즘 근거 추적성 검증

## 목적

사용자가 지적한 핵심은 UI가 아니라 전처리와 알고리즘 본체다.  
이번 검증은 `research/`에 모은 논문·자료·원천 문서가 실제 점수 엔진 규칙에 어떻게 연결되는지 추적한다.

## 검증 대상

- 엔진: `scripts/build_rule_based_location_scores.py`
- 명세: `research/알고리즘_명세_v2_20260704.md`
- 자료 카탈로그: `research/algorithm_evidence_sources/수집자료_카탈로그_20260630.md`
- 상세 검증표: `research/algorithm_evidence_sources/수집자료_상세검증표_20260630.md`
- 백테스트: `datacorpus\_score_backtest_gold\gold_engine_backtest_summary.json`
- 가중치: `datacorpus/_score_backtest/location_score_backtest_recommended_weights.csv`

## 결과

- validation version: `algorithm_evidence_traceability.v0.2-20260707`
- decision: `ALGORITHM_EVIDENCE_TRACEABILITY_PASS`
- PASS: `10`
- FAIL: `0`
- trace rows: `28`
- indicator count: `19`
- catalog IDs: `65`
- backtest source: `datacorpus\_score_backtest_gold\gold_engine_backtest_summary.json`
- backtest rows: `427553`
- next sales percentile Spearman: `0.722295`
- top/bottom next sales ratio: `39.624847`
- sensitivity min rank corr: `0.994221`

## 핵심 판단

현재 엔진의 공식 4축, 별도 점수, 후보 신호, 금지표현은 research 자료와 rule_validation 산출물로 추적 가능하다.

## 상위 규칙 추적표

| rule_id | rule_group | engine_object | axis | evidence_tags | code_ref | rule_ko | resolved |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | 공식_현재입지 | current_location_score | sales,competition,demand,accessibility | BT,M08,M14,M15,RV88 | scripts/build_rule_based_location_scores.py:760 | 공식 현재입지 점수는 4개 축 WLC이며 성장/비용/신뢰도는 합산하지 않는다. | True |
| R02 | 가중치 | load_axis_weights | 공식 4축 | BT,M09,MV-SA1,MV-SA2,MV-SA3 | scripts/build_rule_based_location_scores.py:688 | 가중치는 코드 하드코딩이 아니라 백테스트 권장 가중치 CSV를 읽고 4축 부분합으로 재정규화한다. | True |
| R03 | 정규화 | percentile_scores | 전체 지표 | M08,M14,Q08,RV88 | scripts/build_rule_based_location_scores.py:645 | 서로 단위가 다른 지표는 비교군 백분위로 정규화하고 비용형은 100-백분위로 반전한다. | True |
| R04 | 결측_신뢰도 | _reliability | data_reliability | M18,Q01,Q02,Q06,Q08,Q13,RV03 | scripts/build_rule_based_location_scores.py:773 | 결측은 0점 대체하지 않고 축 제외/가중치 재정규화/신뢰도 감점으로 처리한다. | True |
| R05 | 성장_분리 | growth_potential_score,growth_rebound_candidate_score | growth | BT,D08,K02,K03,K06,RV37,RV38,RV88 | scripts/build_rule_based_location_scores.py:903 | 성장 관련 점수는 현재입지와 질문이 달라 공식 현재입지에 합산하지 않고 후보 신호로 분리한다. | True |
| R06 | 비용_분리 | cost_risk_score | cost_risk | D17,D18,M18,RV12,RV82 | scripts/build_rule_based_location_scores.py:919 | 실거래/R-ONE은 월세·권리금 직접값이 아니므로 지역 비용 압력 프록시로 분리한다. | True |
| R07 | 교통_후보 | transit_accessibility_250m_candidate | accessibility_candidate | D11,D12,M11,M12,RV80,RV81 | scripts/build_rule_based_location_scores.py:876 | 교통 승하차량 250m 후보는 holdout 개선이 있어도 공식 v2.4를 덮지 않고 병렬 후보로 둔다. | True |
| R08 | 객단가_제외 | TICKET_EVIDENCE_ONLY | sales_evidence_only | D02,K05,RV48,RV49,RV50,RV88 | scripts/build_rule_based_location_scores.py:232 | 객단가는 sales 축 직접 가점에서 제거하고 소비 단가 참고값으로만 보존한다. | True |
| R09 | 금지표현 | FORBIDDEN_CLAIMS,TEXT_MODEL_RULES | report_contract | BT,D02,D18,RV88,RV93,RV94 | scripts/build_rule_based_location_scores.py:89 | 성공확률·매출보장·성장보장·월세/권리금 수익성 단정은 출력 금지한다. | True |

## 검증 항목

| validation_id | validation_name | observed | expected | result | reason_ko |
| --- | --- | --- | --- | --- | --- |
| 98-V01 | 수집자료 카탈로그 파일 실재 | catalog_ids=65, missing_files=[] | missing_files=0 | PASS | 자료 ID가 실제 로컬 파일로 추적되지 않으면 근거 인용으로 볼 수 없다. |
| 98-V02 | 엔진 지표 명세 완전성 | indicator_count=19, incomplete=0 | incomplete=0 | PASS | 점수에 들어가는 모든 지표는 축, 방향, grain, 근거, 한글 이유를 가져야 한다. |
| 98-V03 | 근거 태그 해소 | trace_rows=28, unresolved=[] | unresolved=0 | PASS | 논문/자료를 썼다고 말하려면 모든 M/K/D/Q/RV/BT 태그가 실제 자료나 검증 산출물로 연결되어야 한다. |
| 98-V04 | 공식 현재입지 4축 제한 | ['accessibility', 'competition', 'demand', 'sales'] | ['accessibility', 'competition', 'demand', 'sales'] | PASS | 공식 현재입지 점수는 sales, competition, demand, accessibility 4축만 사용해야 한다. |
| 98-V05 | 공식 축별 지표 수 | {'demand': 5, 'accessibility': 4, 'competition': 3, 'sales': 2} | 각 공식축 >= 2 indicators | PASS | 단일 지표 축은 자료 오류나 결측에 취약하므로 공식 4축에는 최소 2개 이상의 근거 지표가 있어야 한다. |
| 98-V06 | 가중치 CSV 원천 사용 | {'weights_exists': True, 'weight_sets': ['BASE', 'CS1', 'CS2', 'CS3'], 'components': ['accessibility', 'budget_risk', 'competition', 'data_reliability', 'demand', 'growth_stability', 'sales'], 'source_refs': ['WEIGHTS_CSV', 'pd.read_csv(WEIGHTS_CSV)']} | CSV exists and code reads WEIGHTS_CSV | PASS | 가중치가 코드 상수가 아니라 백테스트 산출물에서 읽혀야 가중치 조작을 추적할 수 있다. |
| 98-V07 | 백테스트 근거 하한 | {'score_version': 'loc_score.v2.4-sales-ticket-removed-rc1', 'rows': 427553, 'quarters': 20, 'districts': 25, 'industries': 63, 'score_spearman_next_sales_log': 0.555968, 'score_spearman_next_sales_pct_same_industry': 0.722295, 'score_spearman_next_log_growth': -0.062424, 'score_spearman_excess_log_growth_vs_industry': -0.066043, 'growth_score_spearman_next_log_growth': -0.117196, 'growth_score_spearman_excess_log_growth': -0.132926, 'top_decile_rows': 42762, 'bottom_decile_rows': 42764, 'top_decile_avg_next_sales': 4096251422.912492, 'bottom_decile_avg_next_sales': 103375829.094425, 'top_vs_bottom_avg_next_sales_ratio': 39.624847, 'top_decile_next_sales_top_quartile_rate': 0.84077, 'bottom_decile_next_sales_top_quartile_rate': 0.003578, 'top_decile_positive_growth_rate': 0.456737, 'bottom_decile_positive_growth_rate': 0.523712, 'growth_nonnull_rate': 0.939579, 'reliability_min': 76.53, 'reliability_below_gate_rows': 0} | rows>=400000 and next_sales_pct_spearman>=0.7 | PASS | 현재입지 점수는 성장률 보장이 아니라 다음분기 동일업종 매출 수준 후보 선별력으로 검증한다. 최신 gold engine 백테스트를 우선 사용한다. |
| 98-V08 | 후보/보류 신호 공식점수 비활성 | {'growth_rebound_false': True, 'transit_false': True, 'ticket_excluded': True} | all true | PASS | 후보 신호와 evidence-only 값은 설명에는 남겨도 공식 현재입지 산식을 덮으면 안 된다. |
| 98-V09 | AHP 미사용 상태 명시 | True | True | PASS | 전문가 쌍대비교 입력 없이 AHP를 썼다고 주장하면 근거가 과장된다. |
| 98-V10 | 근거 수집 기준 문서 존재 | {'criteria': True, 'detail_table': True, 'catalog': True} | all true | PASS | 외부 자료 수집·검증 기준, 상세검증표, 카탈로그가 함께 있어야 자료 선별 이유를 설명할 수 있다. |

## 해석

- 공식 현재입지 점수는 `sales`, `competition`, `demand`, `accessibility` 4축만 사용한다.
- `growth_potential_score`, `growth_rebound_candidate_score`, `cost_risk_score`, `transit_accessibility_250m_candidate`는 버린 값이 아니라 별도 점수 또는 후보 신호다.
- 객단가는 전처리에서 보존하지만 sales 축 직접 가점에서 제외된 evidence-only 항목이다.
- AHP 논문은 보유하고 있지만 전문가 쌍대비교 입력이 없으므로 현재 구현에서는 사용하지 않는다.
- 다음 알고리즘 강화는 이 추적표의 후보 신호 중 공식 승격 게이트를 통과한 것만 대상으로 해야 한다.

## 산출물

- `datacorpus\_rule_validation\98_algorithm_evidence_traceability.csv`
- `datacorpus\_rule_validation\98_algorithm_evidence_traceability_validation.csv`
- `datacorpus\_rule_validation\98_algorithm_evidence_traceability_summary.json`
