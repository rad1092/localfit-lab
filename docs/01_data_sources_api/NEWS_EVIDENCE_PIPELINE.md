# 뉴스·정책 근거 수집과 두 단계 사용 파이프라인

## 목적

서울 상권 AI 상세리포트에 최근 정책·개발·교통·상권 이슈를 제공하되, 외부 기사와 보도자료의 직접성에 따라 다음 두 층으로 분리한다.

1. **판단 근거**: 선택 위치·업종·예산과 직접 연결되며 기사에 명시된 범위 안에서 서술 판단을 확인
2. **참고·모니터링**: 확인 가치는 있으나 직접성이 부족해 추가 원문·현장 확인이 필요한 자료

두 층 모두 기존 gold 입지점수, 4축 점수, 등급 계산을 변경하지 않는다. 2단계는 LLM 판단 입력과 추천 판단에서도 제외한다.

구현 버전: `condition-evidence.v9-two-tier-budget-scope-copy-bounded`

## 근거

두 단계 구조는 단일 논문의 직접 처방이 아니다. 정보검색의 precision–recall 상충, FEVER의 완전한 evidence set과 `NOT ENOUGH INFO` 구분, 5W1H·TimeML의 사건·위치·시간 범위, W3C PROV 계보, NIST의 출처·인용 검증 및 TEVV 기록 보존을 결합한 보수적 설계다.

실제 원문 PDF·HTML, URL, SHA-256, 정확한 페이지·절, 구현 대응과 한계:

`C:\final_map_project\final_proj\docs\evidence\two_tier_news_20260723\TWO_TIER_NEWS_EVIDENCE_RATIONALE_KO.md`

## 원천

| 묶음 | 원천 | 수집 방식 | 등급 |
|---|---|---|---|
| NAVER 뉴스 | NAVER API HUB 뉴스 검색 | Client ID/Secret 인증 검색 API | B |
| 서울 공식 | 서울시 보도자료 RSS | 공식 RSS | A |
| 서울 공식 | 서울시 25개 자치구 RSS 디렉터리 | 디렉터리에서 피드 동적 발견 | A |
| 정부 공식 | 국토교통부 보도자료 | 쿠키 유지형 공식 RSS | A |
| 정부 공식 | 중소벤처기업부 보도자료 | 공식 RSS | A |
| 공공기관 공식 | 소상공인시장진흥공단 보도·설명 | 공식 게시판 | A |
| 정부 공식 | 대한민국 정책브리핑 | 공식 보도자료 목록 | A |

국토교통부 RSS는 첫 요청에서 세션 쿠키를 발급한 뒤 같은 주소로 307 응답을 보낸다. 수집기는 쿠키를 보존해 두 번째 요청을 완료하며, 직접 피드 장애 때만 정책브리핑의 국토교통부 기관 필터를 보조 경로로 사용한다.

## 인증 정보

인증 정보는 Git에서 제외된 `docs/90_private/key.md`에만 둔다.

```text
naver_api_hub_endpoint: https://naverapihub.apigw.ntruss.com/search/v1/news
naver_api_hub_client_id:
naver_api_hub_client_secret:
```

## 공통 품질 필터

1. 제목·요약·발행일·HTTP(S) 원문 URL이 유효한지 검사한다.
2. 블로그·카페·SNS·영상 플랫폼과 광고·기고·부고 등 저가치 제목을 제외한다.
3. 검색어 자체를 태그 근거로 쓰지 않고 제목·요약·기관명에서 지역·업종·신호를 다시 추출한다.
4. 기사 URL 중복과 같은 날짜의 동일 제목 중복을 합친다. 공식 A급 원천을 NAVER B급 기사보다 우선한다.
5. 선거 공약·정치 기사, 비서울 지역 혼입, 서로 다른 역 이름, 제목에 없는 우연한 지역 언급을 제외한다.
6. 최근 180일을 리포트 후보로 사용하고 실버에는 최대 730일을 보존한다.

## 1단계 · 판단 근거

기본 자격:

- 상권·인접·자치구 자료는 제목에서 위치가 직접 확인되어야 한다.
- 상권·인접 자료는 선택 업종 직접 일치 또는 지속적인 개발·교통·상권 변화가 필요하다.
- 자치구 자료는 위치 제목 일치, 업종 직접 일치, 사업정책 문맥을 모두 요구한다.
- 서울·전국 자료는 공식성, 업종, 직접 예산·소상공인 정책 조건을 더 엄격히 적용한다.
- 기사의 직접 주장 범위를 넘는 인과·성과 해석을 만들지 않는다.

권한 계약:

```text
evidence_tier=decision_support
score_role=context_only
structured_score_impact=none
eligible_for_decision=true
source_assertion_status=supported
```

`eligible_for_decision=true`는 기사에 직접 명시된 범위 안에서 서술 판단을 확인할 수 있다는 뜻이다. 기사의 사실 진실성이나 입지점수 산식이 증명됐다는 뜻은 아니다.

## 2단계 · 참고·모니터링

허용 예:

- 제목 위치는 맞지만 업종·예산 직접성이 부족한 지역 변화
- 해당 자치구 공식 관할 자료이지만 선택 상권과 직접 연결되지 않은 자료
- 서울 범위의 업종 관련 정책
- 서울 범위의 업종 비특정 창업·소상공인 정책
- 선택 업종에 관한 전국 공식 정책

권한 계약:

```text
evidence_tier=reference_monitoring
score_role=reference_only
structured_score_impact=none
eligible_for_decision=false
source_assertion_status=not_enough_info
decision_use=
```

각 자료에는 부족한 연결 조건과 추가 확인할 항목을 `applicability_limit`, `reference_use`로 남긴다.

## 선택 수와 중복 제한

- 기본 1단계 최대 3건, 2단계 최대 3건
- 설정상 각 단계 최대 4건
- 같은 제공기관은 단계별 최대 1건
- 서울·전국 범위 자료는 단계별 최대 1건
- 1단계 자료를 2단계에 중복 배치하지 않음

## 저장과 사용

- 공식 원천 응답: `datacorpus/_raw_ingest/YYYYMMDD/news/`
- 정규화 근거: `datacorpus/_silver/silver_news_evidence.csv`
- 실행 요약: `datacorpus/_raw_ingest/run_logs/`
- NAVER 원응답: 영구 저장하지 않고 응답 해시와 정규화 메타데이터만 보존
- 공통 계보: provider, source group, source grade, original URL, published date, evidence ID, content SHA-256, retrieval version
- LLM: 1단계만 프롬프트에 전달
- PDF·화면: 두 단계 제목·표·사용 한계를 분리 표시

## 실행

```powershell
Set-Location C:\final_map_project
final_proj\.venv\Scripts\python.exe scripts\ingest_news_evidence.py --source all
```

수집기는 명령을 실행할 때 한 번만 동작한다. 별도의 상주 프로세스나 자동 갱신 스케줄러는 두지 않는다.

## 검증

2026-07-23 무작위 15건 상세리포트에서:

- PDF 15개, 90쪽
- 1단계 1건, 2단계 20건
- 사례 15 / 15 PASS
- 문항 840 / 840 PASS
- 기사 단계·권한 검증 Q041과 PDF 분리 검증 Q051 모두 15건 PASS

상세 결과와 재현 명령:

`C:\final_map_project\final_proj\runtime\evaluations\two-tier-news-random15-20260723\FINAL_BATCH_EVALUATION_KO.md`

이 검증은 15개 사례에서 구현·DB·PDF 계약이 일치했다는 뜻이다. 전체 한국어 상권 뉴스의 precision·recall이나 기사 내용의 사실 진실성을 증명한 것은 아니다.
