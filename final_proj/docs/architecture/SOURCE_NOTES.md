# 시스템 구조·기술 스택 검증 메모

이 문서는 세 개의 독자용 문서를 만들 때 사용한 근거와 한계를 기록한다. 독자용 문서의 주장보다 이 메모와 실제 코드가 우선하며, 기준 시점 이후 코드가 바뀌면 다시 확인해야 한다.

## 1. 보고 작업 정의

- 질문: 현재 LocalFit Lab의 시스템 구조와 기술 스택은 실제로 어떻게 구성되어 있는가?
- 지원할 결정: 같은 팀원, 개발자, 비전문가, 다른 LLM에게 각자의 이해 수준에 맞는 구조 설명을 전달한다.
- 대상: 기술 문서 독자. 세 표현 버전만 독자별로 달라진다.
- 범위: `final_proj` 제품, 상위 canonical 데이터 파이프라인, GitHub Actions 검증 흐름.
- 제외: 아직 구현되지 않은 AWS·도메인 설계를 현재 구조처럼 추정하는 것, 비밀키 값, 개인 계정 정보.
- 성공 기준: 프런트→API→서비스→DB/AI/파일 흐름, Raw→Silver→Gold→점수→DB 흐름, 실제 사용 기술과 미사용/미구현 항목이 구분된다.

## 2. 근거 우선순위

1. 2026-07-21 현재 실행 중인 로컬 HTTP/OpenAPI와 읽기 전용 SQLite 점검
2. 현재 작업 트리의 실행 코드·설정·잠금 파일·테스트·CI
3. 현재 데이터 계약 문서
4. README와 과거 검증 문서는 교차 확인용으로만 사용

현재 작업 트리에는 다수의 미커밋 변경이 있다. 따라서 모든 문서는 **2026-07-21 현재 로컬 작업 트리 스냅샷**으로 표시했다.

## 3. 현재 런타임 점검값

| 항목 | 확인값 | 해석 |
| --- | ---: | --- |
| 프런트엔드 | `http://127.0.0.1:3000/` → 200 | Next.js 개발 서버 동작 |
| 백엔드 | `http://127.0.0.1:8000/` → 200 | FastAPI/Uvicorn 동작 |
| OpenAPI | 62 paths, 68 operations | 현재 조립된 실제 API 표면 |
| 제품 DB | 363,593,728 bytes | `runtime/db/commercial.db` |
| 제품 DB 테이블 | 34 | `sqlite_%` 제외, RTree 보조 테이블 포함 |
| SQLite 점검 | `quick_check=ok`, FK 위반 0 | 현재 파일의 구조적 무결성 점검 |
| Python | 3.12.12 | 현재 `.venv` 스냅샷 |
| Node.js / npm | 24.14.0 / 11.16.0 | 현재 로컬 실행값, 프로젝트에서 고정하지 않음 |

### OpenAPI 태그별 동작 수

| 태그 | 동작 수 | 태그 | 동작 수 |
| --- | ---: | --- | ---: |
| reports | 12 | admin | 11 |
| chatbot | 9 | admin-ops | 9 |
| areas | 7 | spatial | 4 |
| comments | 4 | auth | 4 |
| favorites | 3 | events | 1 |
| root | 1 | rankings | 1 |
| client-events | 1 | search | 1 |

집계 기준은 실행 중인 `http://127.0.0.1:8000/openapi.json`에서 HTTP 메서드 `GET`, `POST`, `PUT`, `PATCH`, `DELETE`를 센 것이다. 합계는 68이다.

## 4. 주요 소스 인벤토리

### 프런트엔드

- 앱 공통 셸과 전역 챗봇: `frontend/src/app/layout.tsx:17-32`
- 실제 사용자 메뉴: `frontend/src/components/navigation.ts:15-20`
- API 기본 주소와 인증 fetch: `frontend/src/lib/api.ts:1-109`
- 익명 제품 이벤트: `frontend/src/lib/api.ts:114-135`
- 상권 분석 작업공간: `frontend/src/app/trade/page.tsx:105-449`
- AI 리포트 생성·저장·PDF: `frontend/src/app/ai/page.tsx:554-695`
- 전역 챗봇 호출·렌더링: `frontend/src/components/Chatbot.tsx:208-278`, `:715-822`
- Kakao Maps SDK·그리기·장소 검색: `frontend/src/components/KakaoMap.tsx:18-100`, `:524-725`
- 공간 API 클라이언트: `frontend/src/lib/spatial.ts:19-53`
- 프런트 의존성: `frontend/package.json:11-32`, `frontend/package-lock.json`

### 백엔드와 제품 서비스

- FastAPI·CORS·라우터 조립: `backend/main.py:22-57`
- 경로·환경 설정: `backend/app/core/settings.py:36-75`
- SQLAlchemy/SQLite: `backend/app/database.py:7-20`
- 인증·JWT·bcrypt: `backend/app/core/security.py:36-67`
- 환경별 관리자 접근: `backend/app/dependencies.py:31-87`
- 상권 repository/service: `backend/app/repositories/commercial_area.py:5-60`, `backend/app/services/commercial_area.py:59-180`
- 단일 리포트 데이터 조립: `backend/app/services/single_report.py:103-220`
- 지표·근거 팩: `backend/app/services/indicator_pack.py:1191-1280`
- 로컬 문서 어휘 검색: `backend/app/services/evidence_retriever.py:133-226`
- 뉴스 근거: `backend/app/services/news_evidence.py:262-365`
- AI 구조화 해석·critic·복구: `backend/app/services/interpretive_report.py:1817-2010`
- 결정론적 리포트 검사: `backend/app/services/report_critic.py:181-355`
- Markdown·차트·PDF 발행: `backend/app/services/report_publisher.py:212-620`
- 챗봇 상태/일반 대화/리포트 분기: `backend/app/routers/chatbot.py:422-950`
- 공간 분석·좌표 변환·RTree: `backend/app/services/spatial_analysis.py:11-700`
- 관리자 17단계 핵심 갱신: `backend/app/services/admin_pipeline.py:757-798`
- 제품 DB staging·검증·게시: `backend/scripts/seed_rule_gold_db.py:1163-1240`
- Python 의존성 선언: `backend/requirements.txt`

### 데이터·점수·운영

- canonical 데이터 계약: `docs/data-contracts/data-lineage.md:5-36`
- 원천 registry: `../datacorpus/_raw_ingest/source_registry.csv`
- 실제 점수 정본: `../../scripts/build_rule_based_location_scores.py`
- 일반 CI: `../../.github/workflows/ci.yml:18-92`
- 데이터 E2E 검증: `../../.github/workflows/data-pipeline.yml:33-286`
- 로컬 실행기: `scripts/start-dev.ps1:24-63`

## 5. 현재 활성 기술과 버전

프런트 버전은 `package-lock.json`으로 고정된다. 백엔드 `requirements.txt`에는 버전 범위가 거의 없으므로 Python 버전은 현재 `.venv` 관측값일 뿐 재설치 시 동일성을 보장하지 않는다.

| 계층 | 기술 | 기준 시점 버전/상태 |
| --- | --- | --- |
| 프런트 프레임워크 | Next.js App Router | 16.2.10 |
| UI | React / React DOM | 19.2.4 |
| 언어 | TypeScript | 5.9.3 |
| 스타일 | Tailwind CSS / PostCSS | 4.3.2 |
| 차트 | Recharts | 3.9.1 |
| 지도 | Kakao Maps JavaScript SDK | 외부 SDK, 키·허용 도메인 필요 |
| API | FastAPI / Uvicorn | 0.139.0 / 0.51.0 |
| 검증·ORM | Pydantic / SQLAlchemy | 2.13.4 / 2.0.51 |
| DB | SQLite + RTree | Python 내장, WAL 사용 |
| LLM | langchain-openai / OpenAI SDK | 1.3.4 / 2.45.0 |
| 기본 모델 별칭 | `gpt-5.4-mini` | 코드·환경 예시 기준, 관리자 reasoning override 가능 |
| 데이터 처리 | Pandas / NumPy | 3.0.3 / 2.5.1 |
| 공간 | Shapely / pyproj | 2.1.2 / 3.7.2 |
| 출력 | Matplotlib / ReportLab | 3.11.0 / 5.0.0 |
| 인증 | python-jose / Passlib·bcrypt | JWT Bearer / 비밀번호 해시 |
| CI | GitHub Actions | Windows, Python 3.12, Node 22 |

## 6. 시각화 계약

아키텍처 다이어그램은 수치 차트가 아니라 관계·계층·흐름을 설명하는 Mermaid 도식이다. 모든 다이어그램은 특정 주장과 연결되며 장식용 그림을 넣지 않았다.

| 문서/시각화 | 질문 | 형식 | 지원하는 주장 |
| --- | --- | --- | --- |
| 상세 시스템 구조 | 전체 구성요소와 온라인·오프라인 경계는 무엇인가? | 상하 계층형 flowchart | 브라우저/API/규칙점수/LLM/DB/파일/관리자 경계 |
| 상세 기술 스택 | 각 계층에서 실제 어떤 기술이 동작하는가? | 계층형 flowchart + 표 | 활성 기술, 버전, 역할, 미사용 패키지 구분 |
| 간략 시스템 구조 | 다른 LLM이 가장 먼저 알아야 할 흐름은? | 좌우 6단계 flowchart | 입력→API→규칙 결과→AI 설명→출력 |
| 간략 기술 스택 | 최소 구성은 무엇인가? | 5계층 flowchart | Next/FastAPI/SQLite/OpenAI/CI 요약 |
| 쉬운 시스템 구조 | 비전문가가 서비스의 일을 어떻게 이해할까? | 순한글 사용자 여정 | 화면·처리실·자료창고·설명도우미 역할 |
| 쉬운 기술 스택 | 각 기술 묶음이 무슨 일을 하는가? | 순한글 역할 flowchart | 제작 도구를 역할 중심으로 이해 |

### 공유용 보고서 차트 계약

- 분석 질문: 현재 FastAPI 기능 표면은 어느 도메인에 집중되어 있는가?
- 한 줄 요약: 리포트·관리자·챗봇이 가장 많은 API 동작을 가지며, 상권·공간·인증 기능이 이를 받친다.
- family/variant: Comparison & Ranking / horizontal bar
- 데이터: 실행 중인 OpenAPI의 태그별 operation 수, 14행, 총 68 operations
- 색상: 단일 파란 계열, 범례 없음, 직접 축 라벨
- 대체 형식: 정확한 값은 위 표
- 주의: 동작 수는 코드 규모나 품질 점수가 아니라 API 표면의 분포다.

## 7. 반드시 유지할 해석 경계

- **점수 계산과 LLM 해석을 합치지 않는다.** 점수는 규칙 엔진, LLM은 근거 기반 설명이다.
- `quality_status=pass`는 report critic 계약 통과이지 현실 사업 성공률이나 전체 정확도 인증이 아니다.
- RAG를 임베딩/Vector DB라고 부르지 않는다. 현재는 로컬 문서 어휘 기반 검색이다.
- 현재 비교 리포트를 공식 업종별 4축 추천과 동일하게 설명하지 않는다.
- 점수 엔진 버전 `loc_score.v2.6-coverage-contract-rc1`은 release candidate다.
- Alembic은 없다. 앱 시작 시 `create_all()`과 수동 SQLite 업그레이드를 쓴다.
- `mapbox-gl`, `react-map-gl`, `tailwind-merge`는 설치되어 있지만 현재 프런트 `src`에서 사용되지 않는다.
- `shadcn/ui` 설정 파일은 있으나 실제 `src/components/ui` 구현은 없다.
- GitHub Actions는 검증 자동화이며 운영 배포 자동화가 아니다.
- AWS, 운영 도메인, TLS, RDS/S3, 리버스 프록시는 현재 저장소에서 확인되지 않았다.

## 8. 기술 보고서 구조 매핑

| 기술 보고서 역할 | 산출물 위치 |
| --- | --- |
| 제목 | 각 문서 첫 제목 |
| 기술 요약 | 각 문서의 `먼저 결론` 또는 `핵심 문맥` |
| 시각 근거 | 각 문서의 시스템 구조·기술 스택 Mermaid |
| 범위·정의 | README와 각 문서의 범위/용어 |
| 방법 | 이 파일의 근거 우선순위·소스 인벤토리 |
| 한계·불확실성 | 각 문서의 현재 한계, 이 파일 7절 |
| 다음 단계 | 상세 문서의 배포 전 결정사항, README 갱신 조건 |
| 추가 질문 | AWS·도메인 이후 갱신 항목 |
