from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

MANIFEST_PATH = RAW_DIR / "ingest_manifest.csv"
BROKER_RAW_DIR = RAW_DIR / "20260703" / "seoul_open_data" / "full" / "landBizInfo"
BROKER_DOC_PATH = RAW_DIR / "20260703" / "seoul_open_data" / "docs" / "seoul_real_estate_broker_office_OA-15550.html"

BROKER_SILVER_PATH = SILVER_DIR / "silver_real_estate_broker_office_seoul.csv"
BROKER_SGG_SUMMARY_PATH = SILVER_DIR / "silver_real_estate_broker_office_sgg_status_summary.csv"
BROKER_DONG_SUMMARY_PATH = SILVER_DIR / "silver_real_estate_broker_office_legal_dong_status_summary.csv"
BROKER_FILE_AUDIT_PATH = SILVER_DIR / "silver_real_estate_broker_office_source_file_audit.csv"

SOURCE_CONTRACT_PATH = VALIDATION_DIR / "20_real_estate_broker_office_source_contract.csv"
DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "20_real_estate_broker_office_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "20_real_estate_broker_office_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "20_real_estate_broker_office_consistency_validation.csv"
STATUS_SUMMARY_PATH = VALIDATION_DIR / "20_real_estate_broker_office_status_summary.csv"
SENSITIVE_FIELD_AUDIT_PATH = VALIDATION_DIR / "20_real_estate_broker_office_sensitive_field_audit.csv"
DUPLICATE_KEY_SAMPLE_PATH = VALIDATION_DIR / "20_real_estate_broker_office_duplicate_key_sample.csv"
SGG_CODE_NAME_ISSUE_PATH = VALIDATION_DIR / "20_real_estate_broker_office_sgg_code_name_issue.csv"
NONOPERATING_DATE_ISSUE_SAMPLE_PATH = VALIDATION_DIR / "20_real_estate_broker_office_nonoperating_date_issue_sample.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "20_real_estate_broker_office_silver_validation_20260704.md"

SNAPSHOT_DATE = "2026-07-04"
SOURCE_ID = "seoul_real_estate_broker_office"
DOC_SOURCE_ID = "seoul_real_estate_broker_office_docs"
PROVIDER = "서울열린데이터광장"

RAW_TO_KO = {
    "SYS_REG_NO": "시스템_등록번호",
    "SGG_CD": "시군구_코드",
    "STDG_CD": "법정동_코드",
    "CGG_CD": "자치구_명",
    "LGL_DONG_NM": "법정동_명",
    "LOTNO_SE": "지번_구분",
    "MNO": "본번",
    "SNO": "부번",
    "ADDR": "주소",
    "REST_BRKR_INFO": "중개업_등록번호",
    "BZMN_CONM": "상호명",
    "STTS_SE": "영업상태",
    "PBADMS_DSPS_STRT_DD": "행정처분_시작일",
    "PBADMS_DSPS_END_DD": "행정처분_종료일",
    "INQ_CNT": "조회수",
    "ROAD_CD": "도로명_코드",
    "BLDG": "건물_여부",
    "BMNO": "건물_본번",
    "BSNO": "건물_부번",
}

SILVER_COLUMNS = [
    "source_id",
    "provider",
    "snapshot_date",
    "source_file",
    "원천행번호",
    "시스템_등록번호",
    "중개업_등록번호",
    "시군구_코드",
    "자치구_명",
    "법정동_코드",
    "법정동_명",
    "주소",
    "상호명",
    "영업상태",
    "행정처분_시작일",
    "행정처분_종료일",
    "조회수",
    "도로명_코드",
    "건물_여부",
    "건물_본번",
    "건물_부번",
    "대표자명_존재",
    "전화번호_존재",
    "민감필드_제외",
    "analysis_use_status",
    "proxy_reason_ko",
    "forbidden_claim_ko",
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_없음_"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in df.iterrows():
        values: list[str] = []
        for col in cols:
            value = row[col]
            if pd.isna(value):
                text = ""
            elif isinstance(value, float):
                text = f"{value:.6f}".rstrip("0").rstrip(".")
            else:
                text = str(value)
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def parse_request_params(text: str) -> dict[str, Any]:
    if not clean_text(text):
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def raw_path_to_abs(raw_path: str) -> Path:
    return ROOT / raw_path.replace("\\", "/")


def page_sort_key(path: Path) -> int:
    match = re.search(r"landBizInfo_(\d+)_", path.name)
    return int(match.group(1)) if match else 0


def load_manifest_subset() -> pd.DataFrame:
    manifest = pd.read_csv(MANIFEST_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    subset = manifest[manifest["source_id"].isin([SOURCE_ID, DOC_SOURCE_ID])].copy()
    subset["request_params"] = subset["request_params_json"].map(parse_request_params)
    subset["source_file"] = subset["raw_path"].str.replace("\\", "/", regex=False)
    return subset


def load_raw_pages(manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    manifest_by_path = {row["source_file"]: row for row in manifest.to_dict("records")}

    for path in sorted(BROKER_RAW_DIR.glob("landBizInfo_*.json"), key=page_sort_key):
        data = json.loads(path.read_text(encoding="utf-8"))
        payload = data.get("landBizInfo", {}) if isinstance(data, dict) else {}
        page_rows = payload.get("row", []) if isinstance(payload, dict) else []
        if not isinstance(page_rows, list):
            page_rows = []
        result = payload.get("RESULT", {}) if isinstance(payload, dict) else {}
        rel = path.relative_to(ROOT).as_posix()
        manifest_row = manifest_by_path.get(rel, {})
        params = manifest_row.get("request_params", {}) if isinstance(manifest_row.get("request_params", {}), dict) else {}

        audits.append(
            {
                "source_file": rel,
                "start": params.get("start", ""),
                "end": params.get("end", ""),
                "list_total_count": payload.get("list_total_count", ""),
                "row_count": len(page_rows),
                "result_code": clean_text(result.get("CODE")),
                "result_message": clean_text(result.get("MESSAGE")),
                "http_status": manifest_row.get("http_status", ""),
                "collection_status": manifest_row.get("collection_status", ""),
                "sha256": manifest_row.get("sha256", ""),
            }
        )

        for idx, row in enumerate(page_rows, start=1):
            normalized = {RAW_TO_KO[k]: clean_text(row.get(k)) for k in RAW_TO_KO}
            tel_present = bool(clean_text(row.get("TELNO")) and clean_text(row.get("TELNO")) != "-")
            owner_present = bool(clean_text(row.get("MDT_BSNS_NM")))
            rows.append(
                {
                    "source_id": SOURCE_ID,
                    "provider": PROVIDER,
                    "snapshot_date": SNAPSHOT_DATE,
                    "source_file": rel,
                    "원천행번호": idx,
                    **normalized,
                    "대표자명_존재": owner_present,
                    "전화번호_존재": tel_present,
                    "민감필드_제외": "MDT_BSNS_NM;TELNO",
                    "analysis_use_status": "real_estate_broker_density_proxy",
                    "proxy_reason_ko": "중개업소 분포와 영업상태는 부동산 서비스 밀집/거래 환경 보조 프록시다.",
                    "forbidden_claim_ko": "월세, 권리금, 임대수익, 개별 매물 가격, 창업 성공확률로 표현 금지",
                }
            )

    return pd.DataFrame(rows)[SILVER_COLUMNS], pd.DataFrame(audits)


def build_summaries(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    sgg = (
        df.groupby(["시군구_코드", "자치구_명", "영업상태"], dropna=False)
        .agg(
            중개업소_수=("시스템_등록번호", "count"),
            고유_등록번호_수=("중개업_등록번호", "nunique"),
            법정동_수=("법정동_코드", "nunique"),
        )
        .reset_index()
        .sort_values(["자치구_명", "영업상태"])
    )
    dong = (
        df.groupby(["시군구_코드", "자치구_명", "법정동_코드", "법정동_명", "영업상태"], dropna=False)
        .agg(
            중개업소_수=("시스템_등록번호", "count"),
            고유_등록번호_수=("중개업_등록번호", "nunique"),
        )
        .reset_index()
        .sort_values(["자치구_명", "법정동_명", "영업상태"])
    )
    return sgg, dong


def build_validation_tables(
    manifest: pd.DataFrame,
    df: pd.DataFrame,
    audit: pd.DataFrame,
    sgg_summary: pd.DataFrame,
    dong_summary: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    expected_total = int(pd.to_numeric(audit["list_total_count"], errors="coerce").dropna().max())
    file_count = int(len(audit))
    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_path": BROKER_RAW_DIR.relative_to(ROOT).as_posix(),
                "row_count": len(df),
                "source_file_count": file_count,
                "expected_total_count": expected_total,
                "doc_path": BROKER_DOC_PATH.relative_to(ROOT).as_posix(),
                "usage_role": "부동산 중개업소 분포와 영업상태 기반 부동산 서비스 밀집 보조 프록시",
                "contract_status": "CONDITIONAL_PASS",
            }
        ]
    )

    status_summary = (
        df.groupby("영업상태", dropna=False)
        .size()
        .reset_index(name="row_count")
        .sort_values("영업상태")
    )
    sensitive_audit = pd.DataFrame(
        [
            {
                "필드": "MDT_BSNS_NM",
                "원천의미": "대표자명",
                "silver처리": "값 제외, 대표자명_존재 boolean만 보존",
                "근거": "공개 리포트와 샘플에는 개인정보성 필드를 노출하지 않는다.",
            },
            {
                "필드": "TELNO",
                "원천의미": "전화번호",
                "silver처리": "값 제외, 전화번호_존재 boolean만 보존",
                "근거": "연락처는 입지 점수 산정에 필요 없고 공개 리포트에는 마스킹이 필요하다.",
            },
        ]
    )

    manifest_rows = int(
        manifest[
            manifest["source_id"].eq(SOURCE_ID)
            & manifest["collection_status"].eq("success")
            & manifest["raw_path"].str.endswith(".json")
        ].shape[0]
    )
    domain = pd.DataFrame(
        [
            {
                "검증항목": "원응답 페이지 수",
                "측정값": file_count,
                "기준값": manifest_rows,
                "판정": "PASS" if file_count == manifest_rows else "FAIL",
                "근거": "수집 manifest의 성공 JSON 페이지와 실제 파일 수가 일치해야 한다.",
            },
            {
                "검증항목": "row 보존",
                "측정값": len(df),
                "기준값": expected_total,
                "판정": "PASS" if len(df) == expected_total else "FAIL",
                "근거": "서울 OpenAPI list_total_count와 적재 row 수가 일치해야 한다.",
            },
            {
                "검증항목": "공식 문서 보존",
                "측정값": int(BROKER_DOC_PATH.exists()),
                "기준값": 1,
                "판정": "PASS" if BROKER_DOC_PATH.exists() else "FAIL",
                "근거": "서비스명, 원천, 갱신 성격을 공식 문서로 추적해야 한다.",
            },
            {
                "검증항목": "민감필드 분석용 제외",
                "측정값": "MDT_BSNS_NM;TELNO 제외",
                "기준값": "제외",
                "판정": "PASS",
                "근거": "대표자명과 전화번호는 원천에는 남기되 알고리즘용 silver에는 직접 노출하지 않는다.",
            },
        ]
    )

    required_cols = ["시스템_등록번호", "중개업_등록번호", "시군구_코드", "법정동_코드", "주소", "영업상태"]
    required_null = int(df[required_cols].apply(lambda col: col.map(clean_text).eq("")).any(axis=1).sum())
    duplicate_key_rows = int(df.duplicated(["시스템_등록번호", "중개업_등록번호"], keep=False).sum())
    sgg_sum = int(sgg_summary["중개업소_수"].sum())
    dong_sum = int(dong_summary["중개업소_수"].sum())
    grain = pd.DataFrame(
        [
            {
                "검증항목": "필수 키 결측",
                "측정값": required_null,
                "기준값": 0,
                "판정": "PASS" if required_null == 0 else "FAIL",
                "근거": "중개업소 분포 분석은 등록번호, 행정구역 코드, 주소, 영업상태가 있어야 재현된다.",
            },
            {
                "검증항목": "시스템등록번호+중개업등록번호 중복 row",
                "측정값": duplicate_key_rows,
                "기준값": 0,
                "판정": "PASS" if duplicate_key_rows == 0 else "CONDITIONAL_PASS",
                "근거": "같은 중개업소가 중복되면 자치구·법정동 밀집도가 부풀려진다.",
            },
            {
                "검증항목": "자치구 상태 집계 합계",
                "측정값": sgg_sum,
                "기준값": len(df),
                "판정": "PASS" if sgg_sum == len(df) else "FAIL",
                "근거": "자치구 상태 요약은 원천 row를 누락하지 않아야 한다.",
            },
            {
                "검증항목": "법정동 상태 집계 합계",
                "측정값": dong_sum,
                "기준값": len(df),
                "판정": "PASS" if dong_sum == len(df) else "FAIL",
                "근거": "법정동 상태 요약은 원천 row를 누락하지 않아야 한다.",
            },
        ]
    )

    sgg_code_name_mismatch = int(
        df.groupby("시군구_코드")["자치구_명"].nunique(dropna=False).gt(1).sum()
    )
    status_blank = int(df["영업상태"].map(clean_text).eq("").sum())
    address_not_seoul = int((~df["주소"].str.startswith("서울", na=False)).sum())
    closure_date_issue = int((
        df["영업상태"].str.contains("휴업|폐업|등록취소|업무정지", na=False)
        & df["행정처분_시작일"].map(clean_text).eq("")
    ).sum())
    consistency = pd.DataFrame(
        [
            {
                "검증항목": "시군구 코드-명 다대일 이슈",
                "측정값": sgg_code_name_mismatch,
                "기준값": 0,
                "판정": "PASS" if sgg_code_name_mismatch == 0 else "CONDITIONAL_PASS",
                "근거": "시군구 코드 하나가 여러 구 이름으로 연결되면 행정구역 집계가 흔들린다.",
            },
            {
                "검증항목": "영업상태 결측",
                "측정값": status_blank,
                "기준값": 0,
                "판정": "PASS" if status_blank == 0 else "FAIL",
                "근거": "영업상태가 없으면 활성/비활성 중개업소 구분이 불가능하다.",
            },
            {
                "검증항목": "서울 주소 외 row",
                "측정값": address_not_seoul,
                "기준값": 0,
                "판정": "PASS" if address_not_seoul == 0 else "CONDITIONAL_PASS",
                "근거": "서울 입지 분석 원천이므로 주소가 서울로 시작하지 않는 row는 감사해야 한다.",
            },
            {
                "검증항목": "비영업 상태 처분일 결측",
                "측정값": closure_date_issue,
                "기준값": 0,
                "판정": "PASS" if closure_date_issue == 0 else "CONDITIONAL_PASS",
                "근거": "비영업 상태 해석은 행정처분일 여부와 함께 봐야 한다.",
            },
            {
                "검증항목": "점수 직접 사용 제한",
                "측정값": "명시",
                "기준값": "명시",
                "판정": "PASS",
                "근거": "중개업소 분포는 월세/권리금/매물가 직접값이 아니라 부동산 서비스 밀집 프록시다.",
            },
        ]
    )
    return source_contract, domain, grain, consistency, status_summary, sensitive_audit


def write_report(
    source_contract: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    status_summary: pd.DataFrame,
    sensitive_audit: pd.DataFrame,
    file_audit: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    report = f"""# 서울시 부동산 중개업소 silver 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

`landBizInfo`는 서울시 부동산 중개업소의 등록번호, 주소, 법정동 코드, 영업상태를 담은 원응답이다. 이 데이터는 월세·권리금·개별 매물 가격이 아니라 부동산 서비스 밀집과 지역 부동산 거래 환경을 설명하는 보조 프록시다.

전화번호와 대표자명은 원천에는 존재하지만 알고리즘용 silver에서는 실제 값을 제외하고 존재 여부만 남긴다.

## 2. 사용 원천과 근거

{markdown_table(source_contract)}

근거:

- 수집 기록: `datacorpus/_raw_ingest/run_logs/20260703_real_estate_localdata_sources_ko.md`
- 공식 문서: `datacorpus/_raw_ingest/20260703/seoul_open_data/docs/seoul_real_estate_broker_office_OA-15550.html`
- 전처리 계획: `research/전처리_알고리즘_실행계획_20260703.md`

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_real_estate_broker_office_seoul.csv` | {metrics["row_count"]:,} | 부동산 중개업소 분석용 silver |
| `datacorpus/_silver/silver_real_estate_broker_office_sgg_status_summary.csv` | {metrics["sgg_summary_rows"]:,} | 자치구×영업상태 요약 |
| `datacorpus/_silver/silver_real_estate_broker_office_legal_dong_status_summary.csv` | {metrics["dong_summary_rows"]:,} | 법정동×영업상태 요약 |
| `datacorpus/_silver/silver_real_estate_broker_office_source_file_audit.csv` | {metrics["file_audit_rows"]:,} | 페이지별 row/결과코드 audit |
| `datacorpus/_rule_validation/20_real_estate_broker_office_duplicate_key_sample.csv` | {metrics["duplicate_sample_rows"]:,} | 중복 등록번호 샘플 |
| `datacorpus/_rule_validation/20_real_estate_broker_office_sgg_code_name_issue.csv` | {metrics["sgg_issue_rows"]:,} | 시군구 코드-구명 다대일 감사 |
| `datacorpus/_rule_validation/20_real_estate_broker_office_nonoperating_date_issue_sample.csv` | {metrics["nonoperating_issue_sample_rows"]:,} | 비영업 상태 처분일 결측 샘플 |

## 4. 영업상태 분포

{markdown_table(status_summary)}

## 5. 민감필드 처리

{markdown_table(sensitive_audit)}

## 6. 도메인 검증

{markdown_table(domain)}

## 7. grain 검증

{markdown_table(grain)}

## 8. 정합성 검증

{markdown_table(consistency)}

## 9. 파일별 audit

{markdown_table(file_audit.head(30))}

## 10. 알고리즘 사용 판단

- 사용 가능: 자치구/법정동 단위 부동산 중개업소 밀집도, 영업상태 분포, 부동산 서비스 환경 설명.
- 조건부 사용: 주소 좌표가 없으므로 상권 polygon 직접 매칭은 Juso/VWorld/SGIS 지오코딩 후 별도 검증이 필요하다.
- 보류: 상권 단위 비용 점수에 직접 투입하지 않는다. 행정동/법정동 또는 주소 지오코딩 후 보조 설명으로만 쓴다.
- 금지: 월세, 권리금, 임대수익, 매물가격, 창업 성공확률로 표현하지 않는다.

## 11. 2보 전진 1보 후퇴 검토

1. 전진: `landBizInfo` 26개 원응답 파일의 {metrics["row_count"]:,}개 row를 분석용 silver로 보존했다.
2. 전진: 자치구·법정동·영업상태 요약 테이블을 분리해 부동산 서비스 밀집 프록시로 쓸 수 있게 했다.
3. 후퇴 검토: 전화번호와 대표자명은 알고리즘용 silver에서 제외했다.
4. 후퇴 검토: 좌표가 없으므로 상권 직접 점수값으로 쓰지 않는다.
5. 후퇴 검토: 이 데이터는 월세/권리금 직접값이 아니므로 비용 리스크의 보조 설명으로만 제한한다.
6. 재검토 결과: 부동산 중개업소 데이터는 비용/부동산 환경 보조 프록시와 데이터 신뢰도 설명 자료로 유지한다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    manifest = load_manifest_subset()
    broker, file_audit = load_raw_pages(manifest)
    sgg_summary, dong_summary = build_summaries(broker)
    source_contract, domain, grain, consistency, status_summary, sensitive_audit = build_validation_tables(
        manifest, broker, file_audit, sgg_summary, dong_summary
    )

    duplicate_sample = broker[
        broker.duplicated(["시스템_등록번호", "중개업_등록번호"], keep=False)
    ].sort_values(["시스템_등록번호", "중개업_등록번호"])
    sgg_code_counts = broker.groupby("시군구_코드")["자치구_명"].nunique(dropna=False)
    issue_sgg_codes = set(sgg_code_counts[sgg_code_counts.gt(1)].index)
    sgg_issue = (
        broker[broker["시군구_코드"].isin(issue_sgg_codes)]
        .groupby(["시군구_코드", "자치구_명"], dropna=False)
        .size()
        .reset_index(name="row_count")
        .sort_values(["시군구_코드", "자치구_명"])
    )
    nonoperating_issue = broker[
        broker["영업상태"].str.contains("휴업|폐업|등록취소|업무정지", na=False)
        & broker["행정처분_시작일"].map(clean_text).eq("")
    ].head(200)

    write_csv(broker, BROKER_SILVER_PATH)
    write_csv(sgg_summary, BROKER_SGG_SUMMARY_PATH)
    write_csv(dong_summary, BROKER_DONG_SUMMARY_PATH)
    write_csv(file_audit, BROKER_FILE_AUDIT_PATH)
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_csv(status_summary, STATUS_SUMMARY_PATH)
    write_csv(sensitive_audit, SENSITIVE_FIELD_AUDIT_PATH)
    write_csv(duplicate_sample, DUPLICATE_KEY_SAMPLE_PATH)
    write_csv(sgg_issue, SGG_CODE_NAME_ISSUE_PATH)
    write_csv(nonoperating_issue, NONOPERATING_DATE_ISSUE_SAMPLE_PATH)

    metrics = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": len(broker),
        "sgg_summary_rows": len(sgg_summary),
        "dong_summary_rows": len(dong_summary),
        "file_audit_rows": len(file_audit),
        "duplicate_sample_rows": len(duplicate_sample),
        "sgg_issue_rows": len(sgg_issue),
        "nonoperating_issue_sample_rows": len(nonoperating_issue),
    }
    write_report(
        source_contract,
        domain,
        grain,
        consistency,
        status_summary,
        sensitive_audit,
        file_audit,
        metrics,
    )

    print("완료: real estate broker office silver")
    print(f"- rows: {metrics['row_count']:,}")
    print(f"- sgg summary rows: {metrics['sgg_summary_rows']:,}")
    print(f"- dong summary rows: {metrics['dong_summary_rows']:,}")
    print(f"- report: {MD_REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
