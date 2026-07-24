# LocalFit Lab

LocalFit Lab은 서울 상업용 부동산과 상권을 함께 살펴보는 입지분석 서비스입니다. 입지분석이 제품의 본체이며, AI 상세 리포트와 대화형 입지봇은 같은 상권 지표와 근거 자료를 사용합니다.

## 주요 기능

- Kakao 지도 기반 상권 탐색
- 수요·경쟁·매출·비용·접근성·업종 지표 분석
- 근거 기반 AI 입지 리포트와 PDF 생성
- NAVER 뉴스·서울시·자치구·정부 보도자료를 이용한 최근 이슈 근거
- 분석 결과를 이어서 질문할 수 있는 입지봇
- 관심 상권과 리포트 저장 및 비교

## 제품 폴더

```text
final_proj/
|- frontend/       Next.js 사용자 화면
|- backend/        FastAPI, 입지분석, 입지봇, 리포트 발행
|- resources/      서비스용으로 정제한 RAG 근거 자료
|- docs/           제품·데이터 계약·검증·디자인 문서
|- runtime/        로컬 DB·PDF·로그·임시파일
|- .gitignore
`- README.md
```

원천 수집과 전처리 계보는 상위 워크스페이스에 보존합니다.

```text
../datacorpus/             원천 -> processed -> silver -> gold -> 검증 -> final
../research/               논문과 공식 참고자료
../docs/90_private/key.md  Git에서 제외된 데이터 수집용 키
../scripts/                수집·전처리·생성·검증·감사 스크립트
```

`final_proj`는 이 정본 데이터를 다시 복제하지 않습니다. 보호 범위와 계보는 [docs/data-contracts/data-lineage.md](docs/data-contracts/data-lineage.md)에 정리되어 있습니다.

## 기술 구성

### Frontend

- Next.js 16, React 19
- Tailwind CSS 4
- Kakao Maps JavaScript SDK
- Recharts

### Backend

- FastAPI, SQLAlchemy
- SQLite 로컬 제품 DB
- Shapely, pyproj 기반 공간 교차·좌표 변환
- OpenAI, `langchain-openai`
- ReportLab, Matplotlib 기반 PDF·차트 생성

## 로컬 설정

1. `backend/.env.example`을 참고해 `backend/.env`에 백엔드 전용 키를 설정합니다.
2. `frontend/.env.example`을 참고해 `frontend/.env.local`에 Kakao JavaScript 키를 설정합니다.
3. 데이터 수집 키 원본은 `../docs/90_private/key.md`에만 두고 프론트엔드·로그·Git에는 넣지 않습니다.

기본 경로는 다음 환경변수로 변경할 수 있습니다.

```env
LOCALFIT_RUNTIME_ROOT=runtime
LOCALFIT_DATABASE_PATH=runtime/db/commercial.db
LOCALFIT_REPORTS_ROOT=runtime/reports
LOCALFIT_DATA_ROOT=../datacorpus
LOCALFIT_RESEARCH_ROOT=../research
LOCALFIT_KEY_FILE=../docs/90_private/key.md
LOCALFIT_KNOWLEDGE_ROOT=resources/knowledge/rag_sources
```

상대 경로는 모두 `final_proj/`를 기준으로 해석합니다.

## 실행

### Frontend + Backend

두 개발 서버를 reload 모드로 실행하며, 로그와 Python 캐시는 저장소가 아닌 Windows 임시 디렉터리에 기록합니다.

```powershell
Set-Location C:\final_map_project\final_proj
.\scripts\start-dev.ps1
```

### Backend

```powershell
Set-Location C:\final_map_project\final_proj\backend
C:\final_map_project\final_proj\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

- API: `http://127.0.0.1:8000`
- OpenAPI: `http://127.0.0.1:8000/docs`

### Frontend

```powershell
Set-Location C:\final_map_project\final_proj\frontend
npm run dev -- --hostname 127.0.0.1 --port 3000
```

- 서비스: `http://127.0.0.1:3000`

### 뉴스 근거 갱신

필요한 시점에 전체 원천을 한 번 수집합니다. 뉴스는 리포트의 정성 근거로만 사용되며 입지 점수에는 반영되지 않습니다.

```powershell
Set-Location C:\final_map_project
final_proj\.venv\Scripts\python.exe scripts\ingest_news_evidence.py --source all
```

원천·필터·보존 계약은 [뉴스·정책 근거 수집 파이프라인](../docs/01_data_sources_api/NEWS_EVIDENCE_PIPELINE.md)에 정리되어 있습니다.

### 지도 공간 인덱스 갱신

공식 상권 경계는 canonical gold를 직접 읽고, 점포·버스정류소·지하철 역사 좌표는 제품 DB의 SQLite RTree 인덱스로 갱신합니다.

```powershell
Set-Location C:\final_map_project
final_proj\.venv\Scripts\python.exe final_proj\backend\scripts\seed_spatial_index.py
```

좌표계와 집계 방식은 [공간 분석 영역 계약](docs/data-contracts/spatial-analysis-zone.md)에 정리되어 있습니다.

## 점검 명령

```powershell
# 현재 제품 DB 점검
C:\final_map_project\final_proj\.venv\Scripts\python.exe backend\scripts\db\check_db.py

# canonical gold·점수 산출물로 제품 DB 재구성
C:\final_map_project\final_proj\.venv\Scripts\python.exe backend\scripts\seed_rule_gold_db.py

# AI 리포트·PDF·입지봇 계약 검증
C:\final_map_project\final_proj\.venv\Scripts\python.exe backend\scripts\validate_ai_report_chain.py

# 프론트엔드 검증
Set-Location frontend
npx tsc --noEmit
npm run build
```

DB, 생성 리포트, PDF, 로그, 렌더링 미리보기는 `runtime/`에 저장되며 Git에서는 제외됩니다.
