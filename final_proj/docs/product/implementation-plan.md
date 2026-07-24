# Implementation Plan: Data Processing & Database Population

기존의 목데이터(Mock Data)를 제거하고, 실제 서울시 상권분석서비스 API 및 로컬 `@data` 폴더의 CSV 파일들을 파싱하여 SQLite 데이터베이스에 적재(ETL)하는 스크립트를 구현합니다.

## User Review Required

> [!WARNING]
> 현재 설계된 `상주인구-상권` 등 일부 데이터는 OpenAPI를 통해 동적으로 가져올 수 있으나, 로컬 `data` 폴더(약 150MB+ 상당의 `clean_store_data.csv` 등)에 이미 전처리가 완료된 방대한 정형 데이터가 있습니다.
> 1. 매번 백엔드가 API를 호출해서 데이터를 조회하도록 만들까요? (실시간 조회)
> 2. 아니면 로컬 `data` 폴더의 CSV와 API 데이터를 한 번 조회하여 데이터베이스(SQLite)에 영구 저장(Seed)한 뒤, 백엔드는 DB만 바라보게 할까요?
> **성능상 2번 방식(DB에 적재)을 추천합니다.** 아래 계획은 2번 방식을 기준으로 작성되었습니다.

## Proposed Changes

### 1. Data ETL Script (`backend/scripts/seed_data.py`)
> [!TIP]
> 상위 정본 `../datacorpus`의 CSV와 서울시 OpenAPI 데이터를 파싱하여 `runtime/db/commercial.db`에 적재하는 파이썬 스크립트를 작성합니다.

#### [NEW] backend/scripts/seed_data.py
- **CSV Data Load**: `pandas`를 이용하여 로컬 `datacorpus/` 폴더의 다음 파일들을 읽어들입니다.
  - `서울시_상권분석서비스_영역상권_통합.csv` -> `commercial_area` 테이블
  - `clean_store_data.csv` 등 연관 파일 -> `store`, `sales` 등
- **OpenAPI 연동**: `requests` 라이브러리를 통해 다음 데이터를 추가로 적재/갱신합니다.
  - 점포-상권 (`VwsmTrdarStorQq`)
  - 추정매출-상권 (`VwsmTrdarSelngQq`)
  - 길단위인구-상권 (`VwsmMegaFlpopW`)
  - 상주인구-상권 (`VwsmTrdarRepopQq`)
  - 직장인구-상권 (`VwsmTrdarWrcPopltnQq`)
  - 집객시설-상권 (`VwsmTrdarFcltyQq`)
  - 상권변화지표 (`VwsmTrdarIxQq`)
  - 소비 (`trdarNcmCnsmp`)

### 2. Backend Services Update
> [!IMPORTANT]
> 목데이터 반환 로직을 제거하고, 실제 DB(`commercial_area`, `store`, `population`, `real_estate`, `sales`)에서 데이터를 집계(Aggregate)하여 반환하도록 쿼리를 수정합니다.

#### [MODIFY] backend/app/services/commercial_area.py
- `get_dashboard_summary()` 및 `get_recommendations()` 함수에서 DB Query를 수행하도록 업데이트.

## Verification Plan

### Automated Tests
- `seed_data.py` 실행 후, DB 뷰어 도구나 `sqlite3` CLI를 통해 테이블에 수천 건 이상의 row가 정상 삽입되었는지 검증.
- `api/areas/summary` 엔드포인트 호출 시 목데이터가 아닌 실제 집계된 숫자가 반환되는지 확인.

### Manual Verification
- 프론트엔드 대시보드를 새로고침하여 실제 서울시 상권 데이터(강남역, 홍대입구 등)가 카드 및 차트에 정확히 뜨는지 육안 확인.
