from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATACORPUS = ROOT / "datacorpus"
RAW_ROOT = DATACORPUS / "_raw_ingest"
RUN_DATE = "20260703"


SOURCE_FIELDS = [
    "source_id",
    "priority",
    "provider",
    "dataset_name",
    "method_axis",
    "score_axis",
    "spatial_unit",
    "time_unit",
    "collection_method",
    "credential_ref",
    "source_url",
    "local_doc",
    "current_status",
    "duplicate_policy",
    "reason_ko",
    "notes_ko",
]

MANIFEST_FIELDS = [
    "run_id",
    "source_id",
    "snapshot_date",
    "provider",
    "dataset_name",
    "raw_path",
    "bytes",
    "sha256",
    "collection_status",
    "request_url_redacted",
    "request_params_json",
    "http_status",
    "provider_result_code",
    "provider_result_message",
    "spatial_unit",
    "time_unit",
    "source_period",
    "boundary_version",
    "area_code_type",
    "quality_notes_ko",
    "collected_at",
]

FAILED_FIELDS = [
    "run_id",
    "source_id",
    "provider",
    "dataset_name",
    "attempted_at",
    "failure_type",
    "failure_reason_ko",
    "next_action_ko",
    "request_url_redacted",
]

DUP_FIELDS = [
    "candidate_path",
    "existing_path",
    "match_type",
    "sha256",
    "bytes",
    "notes_ko",
]


SOURCES = [
    {
        "source_id": "seoul_trade_area_boundary",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 영역-상권",
        "method_axis": "상권 폴리곤 조인, GIS suitability",
        "score_axis": "공간기준, 데이터신뢰도",
        "spatial_unit": "상권 폴리곤",
        "time_unit": "기준연도/버전",
        "collection_method": "서울 OpenAPI 또는 기존 파일 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15560/A/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_trade_area_boundary.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "상권코드+경계버전+해시 기준",
        "reason_ko": "후보지 좌표를 공식 상권코드에 연결하는 핵심 조인 기준이다.",
        "notes_ko": "2024년 이후 표준단위구역 기준 변경을 boundary_version으로 분리한다.",
    },
    {
        "source_id": "seoul_sales_trade_area",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 추정매출-상권",
        "method_axis": "MCDA, TOPSIS, 매출 벤치마크",
        "score_axis": "매출",
        "spatial_unit": "상권",
        "time_unit": "분기",
        "collection_method": "서울 OpenAPI 페이지 수집 또는 기존 CSV 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15572/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_sales_trade_area.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준년분기+상권코드+서비스업종코드+해시 기준",
        "reason_ko": "업종별 상권 매출과 점포당 매출을 만드는 핵심 원천이다.",
        "notes_ko": "개별 매장 실제 매출이 아니라 추정·집계 매출임을 품질 메모에 남긴다.",
    },
    {
        "source_id": "seoul_store_trade_area",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 점포-상권",
        "method_axis": "경쟁/집적, 생존/폐업 위험",
        "score_axis": "경쟁/상권환경, 성장/안정성",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "collection_method": "서울 OpenAPI 페이지 수집 또는 기존 CSV 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15577/A/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_store_trade_area.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준기간+상권코드+서비스업종코드+해시 기준",
        "reason_ko": "점포수, 유사업종 점포, 프랜차이즈, 개폐업률로 경쟁과 안정성을 판단한다.",
        "notes_ko": "서비스업종코드와 업종명을 같이 보존한다.",
    },
    {
        "source_id": "seoul_floating_population_trade_area",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 길단위인구/유동인구-상권",
        "method_axis": "Huff-lite, Dynamic Huff, 수요 분석",
        "score_axis": "수요",
        "spatial_unit": "상권",
        "time_unit": "분기/시간대",
        "collection_method": "서울 OpenAPI 페이지 수집 또는 기존 CSV 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_floating_population_trade_area.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준년분기+상권코드+시간대+해시 기준",
        "reason_ko": "시간대·성별·연령별 수요 표면을 만들기 위한 핵심 데이터다.",
        "notes_ko": "추정 인구이므로 원천 직접성 등급을 프록시로 둔다.",
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 상주인구·직장인구-상권",
        "method_axis": "고객 세분화, 주거/근무 균형",
        "score_axis": "수요",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "collection_method": "서울 OpenAPI 페이지 수집 또는 기존 CSV 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_resident_population_trade_area.html;research/algorithm_evidence_sources/data_docs/seoul_open_data_worker_population_trade_area.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준기간+상권코드+인구유형+해시 기준",
        "reason_ko": "주거형/오피스형/혼합형 상권 해석과 업종 적합도를 보강한다.",
        "notes_ko": "상주인구와 직장인구의 정의 차이를 메타데이터에 남긴다.",
    },
    {
        "source_id": "seoul_trade_area_change_index",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 상권변화지표",
        "method_axis": "성장/쇠퇴, 생존 위험",
        "score_axis": "성장/안정성",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "collection_method": "서울 OpenAPI 페이지 수집 또는 기존 CSV 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15576/A/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_trade_area_change_index.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준기간+상권코드+해시 기준",
        "reason_ko": "상권의 성장, 정체, 쇠퇴, 평균 영업/폐업기간을 설명하는 핵심 지표다.",
        "notes_ko": "등급 의미를 리포트에 풀어쓰기 위한 코드 설명이 필요하다.",
    },
    {
        "source_id": "seoul_facility_trade_area",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울 상권분석서비스 집객시설-상권",
        "method_axis": "접근성, 앵커시설, 2SFCA 보조",
        "score_axis": "접근성/유입",
        "spatial_unit": "상권",
        "time_unit": "기준연도",
        "collection_method": "서울 OpenAPI 페이지 수집 또는 기존 CSV 해시 등록",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15580/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_facility_trade_area.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준기간+상권코드+시설유형+해시 기준",
        "reason_ko": "교통·병원·학교·공공기관 등 앵커시설 접근성을 상권 단위로 평가한다.",
        "notes_ko": "시설 수는 실제 방문객 수가 아니므로 프록시 등급으로 기록한다.",
    },
    {
        "source_id": "seoul_living_migration",
        "priority": "P0",
        "provider": "서울 열린데이터/생활이동",
        "dataset_name": "서울 생활이동 OD",
        "method_axis": "OD 분석, Huff 보강, 유입/유출",
        "score_axis": "접근성/유입, 수요",
        "spatial_unit": "자치구/행정동 OD",
        "time_unit": "월/시간",
        "collection_method": "기존 raw 확인 후 부족 월 수집",
        "credential_ref": "SEOUL_OPEN_DATA_KEY_OR_PUBLIC_DOWNLOAD",
        "source_url": "https://data.seoul.go.kr/dataVisual/seoul/seoulLivingMigration.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_living_migration_guide.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준년월+시간+출발지+도착지+해시 기준",
        "reason_ko": "배후지에서 상권으로 들어오는 유입 규모와 순유입을 설명한다.",
        "notes_ko": "세밀한 통행시간 행렬이 아니라 집계 OD임을 명시한다.",
    },
    {
        "source_id": "molit_rtms_commercial_trade",
        "priority": "P1",
        "provider": "국토교통부/공공데이터포털",
        "dataset_name": "상업·업무용 부동산 매매 실거래",
        "method_axis": "비용/부동산 시장 프록시",
        "score_axis": "비용/임대 리스크",
        "spatial_unit": "시군구/법정동",
        "time_unit": "월/거래",
        "collection_method": "API 페이징 수집",
        "credential_ref": "PUBLIC_DATA_KEY_RTMS",
        "source_url": "https://www.data.go.kr/data/15126463/openapi.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/data_go_kr_molit_commercial_real_estate_trade_api.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "LAWD_CD+DEAL_YMD+거래키+해시 기준",
        "reason_ko": "실제 월세는 아니지만 상업용 부동산 가격 수준과 거래 열기를 나타내는 비용 프록시다.",
        "notes_ko": "수집 시 User-Agent 헤더를 붙이고 서비스키가 포함된 URL은 로그에 남기지 않는다.",
    },
    {
        "source_id": "reb_small_shop_rent",
        "priority": "P1",
        "provider": "한국부동산원/공공데이터",
        "dataset_name": "소규모 상가 임대료·공실률",
        "method_axis": "비용/손익분기, MCDA",
        "score_axis": "비용/임대 리스크",
        "spatial_unit": "권역/상권유형/지역",
        "time_unit": "분기",
        "collection_method": "R-ONE 또는 파일 다운로드",
        "credential_ref": "REB_RONE_KEY",
        "source_url": "https://www.data.go.kr/data/15069766/fileData.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/data_go_kr_kab_small_shop_rent.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "기준분기+지역+상가유형+해시 기준",
        "reason_ko": "월세/임대 부담률을 직접 계산하기 어려운 상황에서 가장 현실적인 공개 임대료 프록시다.",
        "notes_ko": "후보 매물 임대료가 아니라 집계 임대료임을 경고한다.",
    },
    {
        "source_id": "sbdc_store_info",
        "priority": "P1",
        "provider": "소상공인시장진흥공단/공공데이터포털",
        "dataset_name": "상가(상권)정보 파일/API",
        "method_axis": "반경 경쟁, 업종 매핑",
        "score_axis": "경쟁/상권환경",
        "spatial_unit": "점포 좌표",
        "time_unit": "기준월",
        "collection_method": "파일 다운로드 우선, API는 보조",
        "credential_ref": "PUBLIC_DATA_KEY_SBDC",
        "source_url": "https://www.data.go.kr/data/15012005/openapi.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/data_go_kr_sbiz_store_info_api.html",
        "current_status": "existing_candidate",
        "duplicate_policy": "상가업소번호+기준월+해시 기준",
        "reason_ko": "후보지 반경 안의 동종/보완 점포를 좌표 기반으로 집계한다.",
        "notes_ko": "전국 파일은 크므로 서울 필터링 원본과 전국 원본 해시를 분리한다.",
    },
    {
        "source_id": "sgis_small_area_stats",
        "priority": "P2",
        "provider": "SGIS",
        "dataset_name": "소지역 통계와 좌표 기반 행정/집계구 코드",
        "method_axis": "공간조인, 소지역 보정",
        "score_axis": "수요, 데이터신뢰도",
        "spatial_unit": "집계구/행정동",
        "time_unit": "연도",
        "collection_method": "API 토큰 재발급 후 수집",
        "credential_ref": "SGIS_SERVICE_ID_AND_SECRET",
        "source_url": "https://sgis.mods.go.kr/developer/html/openApi/api/data.html",
        "local_doc": "research/algorithm_evidence_sources/data_docs/sgis_openapi_data.html",
        "current_status": "to_collect",
        "duplicate_policy": "연도+경계버전+API명+파라미터+해시 기준",
        "reason_ko": "행정동보다 세밀한 인구·가구·사업체 통계를 상권 경계와 결합하기 위한 보강 원천이다.",
        "notes_ko": "consumer_key는 서비스ID, consumer_secret은 보안 key 순서로 넣고 access token은 매 실행 재발급한다.",
    },
    {
        "source_id": "kosis_population_business_survival",
        "priority": "P2",
        "provider": "KOSIS",
        "dataset_name": "인구·가구·사업체·생존율 통계",
        "method_axis": "거시 보정, 생존 위험",
        "score_axis": "수요, 성장/안정성",
        "spatial_unit": "시도/시군구/통계표 단위",
        "time_unit": "연/분기",
        "collection_method": "메타데이터 우선 수집 후 통계표 확정",
        "credential_ref": "KOSIS_API_KEY",
        "source_url": "https://kosis.kr/openapi/",
        "local_doc": "research/algorithm_evidence_sources/data_docs/kosis_open_api_home.html",
        "current_status": "to_collect",
        "duplicate_policy": "orgId+tblId+item+region+period+해시 기준",
        "reason_ko": "공식 인구·사업체·생존율 벤치마크로 상권 점수의 외부 기준선을 만든다.",
        "notes_ko": "통계표 ID, 항목코드, 지역코드, 기간코드를 반드시 저장한다.",
    },
    {
        "source_id": "vworld_juso_geocoding",
        "priority": "P3",
        "provider": "VWorld/Juso",
        "dataset_name": "주소 정규화와 주소-좌표 변환",
        "method_axis": "공간조인 보조",
        "score_axis": "데이터신뢰도",
        "spatial_unit": "주소/좌표",
        "time_unit": "수집일",
        "collection_method": "후보지·시설 주소 캐시",
        "credential_ref": "VWORLD_KEY;JUSO_KEY",
        "source_url": "https://www.vworld.kr/dev/v4dv_geocoderguide2_s001.do;https://business.juso.go.kr/jst/jstAddressApiList",
        "local_doc": "research/algorithm_evidence_sources/data_docs/vworld_geocoder_api_guide.html",
        "current_status": "to_collect_later",
        "duplicate_policy": "정규화주소+좌표계+해시 기준",
        "reason_ko": "주소 입력과 시설 주소를 좌표·행정코드·상권경계에 안정적으로 붙이기 위한 보조 데이터다.",
        "notes_ko": "영구 캐시 가능 범위는 API 약관 확인 후 확정한다.",
    },
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def ensure_csv(path: Path, fields: list[str]) -> None:
    if path.exists():
        return
    write_csv(path, [], fields)


def find_existing_candidates() -> list[dict]:
    patterns = {
        "seoul_trade_area_boundary": ["영역-상권", "상권분석서비스(영역-상권)", "TbgisTrdarRelm"],
        "seoul_sales_trade_area": ["추정매출-상권", "상권분석서비스(추정매출-상권)"],
        "seoul_store_trade_area": ["점포-상권", "상권분석서비스(점포-상권)"],
        "seoul_floating_population_trade_area": ["길단위인구-상권", "유동인구-상권"],
        "seoul_resident_worker_population_trade_area": ["상주인구-상권", "직장인구-상권"],
        "seoul_trade_area_change_index": ["상권변화지표-상권"],
        "seoul_facility_trade_area": ["집객시설-상권"],
        "seoul_living_migration": ["생활이동"],
        "molit_rtms_commercial_trade": ["상업업무용_실거래", "RTMSDataSvcNrgTrade", "상업업무용 부동산 매매 실거래"],
        "reb_small_shop_rent": ["임대동향", "소규모 상가", "상업용부동산 임대동향"],
        "sbdc_store_info": ["상가(상권)정보", "소상공인시장진흥공단"],
    }
    rows: list[dict] = []
    for path in DATACORPUS.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        rel = str(path.relative_to(ROOT))
        for source_id, terms in patterns.items():
            if any(term in name or term in rel for term in terms):
                stat = path.stat()
                rows.append(
                    {
                        "source_id": source_id,
                        "candidate_path": rel,
                        "bytes": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        "extension": path.suffix.lower(),
                    }
                )
                break
    return rows


def main() -> None:
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    for sub in [
        "run_logs",
        f"{RUN_DATE}/seoul_open_data",
        f"{RUN_DATE}/public_data",
        f"{RUN_DATE}/sgis",
        f"{RUN_DATE}/kosis",
        f"{RUN_DATE}/vworld",
        f"{RUN_DATE}/juso",
        f"{RUN_DATE}/reb_rone",
        f"{RUN_DATE}/localdata",
        f"{RUN_DATE}/metadata",
    ]:
        (RAW_ROOT / sub).mkdir(parents=True, exist_ok=True)

    write_csv(RAW_ROOT / "source_registry.csv", SOURCES, SOURCE_FIELDS)
    ensure_csv(RAW_ROOT / "ingest_manifest.csv", MANIFEST_FIELDS)
    ensure_csv(RAW_ROOT / "failed_downloads.csv", FAILED_FIELDS)
    ensure_csv(RAW_ROOT / "duplicate_candidates.csv", DUP_FIELDS)

    existing = find_existing_candidates()
    write_csv(
        RAW_ROOT / f"{RUN_DATE}" / "metadata" / "existing_datacorpus_candidates.csv",
        existing,
        ["source_id", "candidate_path", "bytes", "mtime", "extension"],
    )

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_date": RUN_DATE,
        "source_count": len(SOURCES),
        "existing_candidate_count": len(existing),
        "raw_root": str(RAW_ROOT.relative_to(ROOT)),
        "notes_ko": [
            "실제 키 값은 파일에 쓰지 않았다.",
            "기존 datacorpus 파일은 덮어쓰지 않고 후보 목록만 생성했다.",
            "다음 단계에서 원본 API 응답을 날짜별 폴더에 저장한다.",
        ],
    }
    run_log = RAW_ROOT / "run_logs" / f"{RUN_DATE}_registry_init.json"
    run_log.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
