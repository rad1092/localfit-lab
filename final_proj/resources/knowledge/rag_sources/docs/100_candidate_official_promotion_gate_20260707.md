# 100. 후보 신호 공식 승격 게이트 종합판정

## 목적

98번에서 알고리즘 근거 추적성을 확인했고, 99번에서 공식 gold 입력 준비도를 확인했다. 100번은 여러 후보 신호를 공식 점수에 바로 섞어도 되는지 다시 한 번 후퇴해서 판정한다.

## 결론

- validation version: `candidate_official_promotion_gate.v0.1-20260707`
- decision: `CANDIDATE_OFFICIAL_PROMOTION_GATE_PASS_NO_IMMEDIATE_PROMOTION`
- PASS: `10`
- FAIL: `0`
- candidate count: `6`
- official promote now count: `0`

현재 공식 4축에 즉시 승격할 후보는 없다. 가장 가까운 후보는 교통 접근성 250m 승하차량 후보지만 최신 공식분기 20261의 202601~202603 raw·피처 gap 때문에 보류한다.

## 후보별 판정

| candidate | target_axis | status | rows | action | reason | next |
| --- | --- | --- | ---: | --- | --- | --- |
| `transit_250m_passenger_accessibility` | accessibility | NOT_READY_LIVE_QUARTER_GAP | 100650 | 공식 접근성축 미반영, 후보 출력만 유지 | 2021~2025 holdout 성능은 통과했지만 최신 공식분기 20261의 202601~202603 교통 raw와 후보 피처가 없다. | 2026Q1 버스·지하철 승하차 raw 수집 후 58/31/59/60/63/80/81 재실행. |
| `bus_network_diversity` | accessibility | NOT_READY_SNAPSHOT_ONLY | 1650 | evidence-only | 2026-07-03 노선마스터 스냅샷 기반이라 2021~2025 백데이터에 fan-out하면 미래정보 누수다. | 동일 기간별 노선/정류장 이력 또는 공식 백테스트 가능 피처 확보. |
| `localdata_food_open_close` | competition/growth_evidence | NOT_READY_EVIDENCE_ONLY | 362713 | 후보 evidence만 유지 | join-safe 백테스트는 안정적이나 업태-서비스업종 수동검토와 원천 부분실패가 남아 공식 점수 직접 투입을 금지했다. | 업태 bridge 수동검토 확정 범위 확대, 실패 페이지 재수집, 공식축 영향 백테스트. |
| `cost_proxy_rtms_rone_broker` | cost_risk | SEPARATE_PROXY_SCORE_ONLY | 86986 | 현재입지 공식 4축에는 미반영, cost_risk_score 별도 출력 | 상권 직접 월세·권리금이 아니라 자치구/권역 비용 압력 프록시이므로 수익성 판단이나 현재입지 총점에 섞지 않는다. | 상권·업종·시점에 맞는 임대료/권리금 직접 원천이 확보되기 전까지 별도 프록시로 유지. |
| `admin_stats_sgis_kosis` | demand/growth_reference | NOT_READY_GRAIN_PENALTY_REFERENCE | 54756 | 기준선/evidence만 유지 | 행정동·자치구 기준선이며 상권 내부 직접값이 아니다. SGIS 상권 후보매칭도 2개 상권 미매칭을 audit으로 남겼다. | 상권 polygon과 행정통계 면적/인구 가중배분 검증 또는 직접 상권 단위 원천 확보. |
| `growth_rebound` | growth | SEPARATE_CANDIDATE_NOT_CURRENT_SCORE | 619546 | growth_rebound_candidate_score 별도 출력 | 성장·반등은 현재 입지 점수와 목적이 다르고, 이전 백테스트에서 성장 타깃 상관이 약해 공식 현재입지 합산에서 분리했다. | 성장 라벨 정의 재검토, 시간누수 없는 holdout, 업종별 안정성 검증. |

## 검증

| id | result | observed | reason |
| --- | --- | --- | --- |
| 100-V01 | PASS | missing_docs=[] | 공식 승격 판정은 research/rule_validation 근거 문서가 있어야 한다. |
| 100-V02 | PASS | missing_files=[] | 후보를 보류하더라도 실제 candidate gold가 있어야 evidence로 재사용할 수 있다. |
| 100-V03 | PASS | fail_sum=0 | 선행 검증 실패가 있으면 공식 승격 논의가 아니라 전처리 보수부터 해야 한다. |
| 100-V04 | PASS | official_promote_now=0 | 현재 근거상 어떤 후보도 공식 4축에 즉시 넣으면 안 된다. |
| 100-V05 | PASS | NOT_READY_LIVE_QUARTER_GAP | holdout 성능이 있어도 최신분기 입력 피처가 없으면 운영 공식 산식에 넣을 수 없다. |
| 100-V06 | PASS | SEPARATE_PROXY_SCORE_ONLY | 월세·권리금 직접값이 아니므로 cost_risk는 현재입지 총점이 아니라 별도 출력이어야 한다. |
| 100-V07 | PASS | bus_network_diversity; localdata_food_open_close; admin_stats_sgis_kosis | 최신 스냅샷 후보를 과거 행에 붙이면 미래정보 누수나 grain 과장이 생긴다. |
| 100-V08 | PASS | CURRENT_AXES found | 후보 승격 검토표를 만든다고 공식 점수를 조용히 바꾸면 안 된다. |
| 100-V09 | PASS | FORBIDDEN_CLAIMS present | 후보 evidence가 늘수록 text model이 과장하지 않게 금지표현 계약이 엔진에 남아야 한다. |
| 100-V10 | PASS | V04,V05,V06,V07,V08,V09 | 파일 존재만 보지 않고 공식 산식에 넣으면 안 되는 이유를 규칙으로 검증했다. |

## 독립 검토 메모

자료·문서 관점 read-only 서브에이전트 검토에서도 즉시 공식 입지 점수축으로 승격 가능한 후보는 없다고 판정했다.

- `transit_accessibility_250m_candidate`만 성능·holdout 기준을 통과해 공식 패치 검토에 가장 가깝다.
- 다만 최신 공식분기 `20261`의 `202601~202603` 교통 raw/후보 피처가 없어 현재 승격은 보류해야 한다.
- `growth_rebound`, `cost_risk`, `localdata_food`, `admin_stats`, `bus_network_diversity`, `sales_ticket`은 각각 목적 차이, 프록시 한계, 수동검토/부분실패, grain mismatch, 스냅샷 누수, 백테스트 열위 때문에 공식 4축 승격 불가로 판정됐다.

데이터 관점 read-only 서브에이전트 검토도 같은 결론이다.

- 공식 4축 gold 입력은 최신 공통분기 `20261`까지 준비됐고, 99번 입력 계약은 PASS다.
- 최신 gold engine 백테스트는 `datacorpus/_score_backtest_gold/gold_engine_backtest_summary.json` 기준 rows `427,553`, next sales percentile Spearman `0.722295`, top/bottom 다음분기 평균 매출 비율 `39.624847`, 민감도 최소 rank corr `0.994221`이다.
- `gold_growth_label_candidates_q_industry.csv`는 미래 라벨 파일이므로 feature로 넣으면 명백한 future label leakage다.
- `gold_accessibility_transit_q_area_candidate.csv`는 holdout 개선이 있으나 최신분기 `20261` 필요 월 `202601~202603` raw가 없어 공식 승격 시점 gap이 있다.
- LocalData 후보는 join-safe 테이블은 duplicate 0이지만 `candidate_서비스_업종_코드`를 공식 `서비스_업종_코드`와 직접 동일시하면 안 된다.
- SBDC 202603, bus network, broker 등 최신 스냅샷 후보를 과거 백테스트에 fan-out하면 시간누수가 생긴다.
- 비용/R-ONE/중개업소와 SGIS/KOSIS는 자치구·권역·행정동 grain이라 상권 직접값처럼 합산하면 grain mismatch가 생긴다.

## 알고리즘 반영 원칙

- 후보 신호는 선행 검증이 PASS여도 최신분기 입력, 시간누수, grain mismatch, 금지표현 계약을 모두 통과해야 공식축으로 승격한다.
- 현재 공식 점수는 `sales`, `competition`, `demand`, `accessibility` 4축을 유지한다.
- 비용, 성장, 행정통계, LocalData, 버스 네트워크 다양성은 별도 점수 또는 evidence-only로 유지한다.
