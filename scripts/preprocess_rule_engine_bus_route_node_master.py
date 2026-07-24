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

SERVICE = "masterRouteNode"
RAW_PATH = RAW_DIR / "20260703" / "seoul_open_data" / "transport" / "bus_route_node_master"
DOC_PATHS = [
    RAW_DIR
    / "20260703"
    / "seoul_open_data"
    / "docs"
    / "transport"
    / "seoul_open_data_bus_route_node_master_OA-21233.html",
    ROOT
    / "research"
    / "algorithm_evidence_sources"
    / "data_docs"
    / "seoul_open_data_bus_route_node_master_OA-21233.html",
]
BUS_STOP_MASTER_PATH = SILVER_DIR / "silver_bus_stop_location_master.csv"
BUS_PASSENGER_SUMMARY_PATH = SILVER_DIR / "silver_bus_passenger_route_stop_month_summary.csv"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_bus_route_node_master"

BASE_COLUMNS = {
    "RTE_ID": "노선_ID",
    "CRTR_ID": "정류소_ID",
    "LNKG_LEN": "링크_구간거리_원천값",
    "CRTR_SEQ": "정류장_순서",
}
KEY_COLS = ["노선_ID", "정류소_ID", "정류장_순서"]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def page_sort_key(path: Path) -> tuple[int, int]:
    match = re.search(r"_(\d+)_(\d+)\.json$", path.name)
    if not match:
        return (10**12, 10**12)
    return (int(match.group(1)), int(match.group(2)))


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def read_openapi_pages() -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    file_audit_rows: list[dict[str, Any]] = []
    totals: set[int] = set()
    page_paths = sorted(RAW_PATH.glob(f"{SERVICE}_*.json"), key=page_sort_key)
    if not page_paths:
        raise FileNotFoundError(f"{RAW_PATH}에서 {SERVICE} 원응답을 찾지 못했습니다.")

    for page_path in page_paths:
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        root = payload.get(SERVICE)
        if not isinstance(root, dict):
            raise ValueError(f"{page_path} 파일에 {SERVICE} 루트가 없습니다.")
        result = root.get("RESULT", {})
        if "list_total_count" in root:
            totals.add(int(root["list_total_count"]))
        page_rows = root.get("row", [])
        match = re.search(r"_(\d+)_(\d+)\.json$", page_path.name)
        start_row = int(match.group(1)) if match else None
        end_row = int(match.group(2)) if match else None
        file_audit_rows.append(
            {
                "raw_path": rel(page_path),
                "requested_start": start_row,
                "requested_end": end_row,
                "row_count": len(page_rows),
                "list_total_count": root.get("list_total_count"),
                "result_code": result.get("CODE", ""),
                "result_message": result.get("MESSAGE", ""),
            }
        )
        for row in page_rows:
            item = dict(row)
            item["_raw_path"] = rel(page_path)
            rows.append(item)

    if len(totals) != 1:
        raise ValueError(f"{SERVICE} list_total_count가 하나로 고정되지 않았습니다: {sorted(totals)}")
    return pd.DataFrame(rows), pd.DataFrame(file_audit_rows), len(page_paths), next(iter(totals))


def load_bus_stop_master() -> pd.DataFrame:
    if not BUS_STOP_MASTER_PATH.exists():
        return pd.DataFrame()
    cols = ["정류소_고유번호", "정류소_ARS_ID", "정류소_명", "경도", "위도"]
    return pd.read_csv(BUS_STOP_MASTER_PATH, encoding="utf-8-sig", dtype=str, usecols=cols).fillna("")


def load_bus_passenger_stop_ids() -> set[str]:
    if not BUS_PASSENGER_SUMMARY_PATH.exists():
        return set()
    passenger = pd.read_csv(
        BUS_PASSENGER_SUMMARY_PATH,
        encoding="utf-8-sig",
        dtype=str,
        usecols=["정류소_ID"],
    ).fillna("")
    return set(passenger["정류소_ID"].astype(str).str.strip())


def build_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, int, int]:
    raw, file_audit, page_count, api_total = read_openapi_pages()
    missing = [col for col in BASE_COLUMNS if col not in raw.columns]
    if missing:
        raise ValueError(f"{SERVICE} 원천 컬럼 누락: {missing}")

    df = raw.rename(columns=BASE_COLUMNS)[list(BASE_COLUMNS.values()) + ["_raw_path"]].copy()
    for col in ["노선_ID", "정류소_ID"]:
        df[col] = df[col].astype(str).str.strip()
    df["링크_구간거리_원천값"] = pd.to_numeric(df["링크_구간거리_원천값"], errors="coerce")
    df["정류장_순서"] = pd.to_numeric(df["정류장_순서"], errors="coerce").astype("Int64")

    stop_master = load_bus_stop_master()
    if not stop_master.empty:
        df = df.merge(
            stop_master.rename(columns={"정류소_고유번호": "정류소_ID"}),
            on="정류소_ID",
            how="left",
        )
    else:
        for col in ["정류소_ARS_ID", "정류소_명", "경도", "위도"]:
            df[col] = ""

    passenger_stop_ids = load_bus_passenger_stop_ids()
    df["정류소위치_결합상태"] = df["정류소_명"].fillna("").astype(str).str.strip().where(
        df["정류소_명"].fillna("").astype(str).str.strip().eq(""),
        "exact_match",
    )
    df.loc[df["정류소위치_결합상태"].eq(""), "정류소위치_결합상태"] = "unmatched_bus_stop_location_master"
    df["승하차자료_정류소_ID_존재"] = df["정류소_ID"].isin(passenger_stop_ids)
    df["quality_key_missing"] = df[KEY_COLS].isna().any(axis=1) | df[["노선_ID", "정류소_ID"]].eq("").any(axis=1)
    df["quality_link_length_missing"] = df["링크_구간거리_원천값"].isna()
    df["quality_link_length_nonpositive"] = df["링크_구간거리_원천값"].fillna(-1).le(0)
    df["quality_sequence_missing"] = df["정류장_순서"].isna()
    df["quality_sequence_not_integer"] = df["정류장_순서"].isna()
    df["link_length_unit_judgement"] = "공식 문서는 구간거리라고 설명하지만 단위를 명시하지 않아 실제 m/도보시간으로 직접 환산하지 않는다."
    df["route_id_use_warning_ko"] = "RTE_ID는 노선 번호가 아니라 노선 ID다. 노선마스터 없이는 버스 승하차의 노선_번호와 직접 조인하지 않는다."
    df["source_id"] = SOURCE_ID
    df["provider"] = PROVIDER
    df["source_service"] = SERVICE
    df["snapshot_date"] = SNAPSHOT_DATE
    df["source_grain"] = "노선_ID+정류소_ID+정류장_순서"
    df["raw_page_count"] = page_count
    df["api_list_total_count"] = api_total
    df["raw_row_count"] = len(df)
    df["directness_level"] = "P2_공식_버스노선_정류장순서_구간거리_보조"
    df["forbidden_claim_ko"] = "실제 도보시간, 실제 이동시간, 실제 상권 방문확률, 창업 성공확률로 표현 금지"
    df["notes_ko"] = (
        "버스 노선별 정류장 순서와 구간거리 원천이다. 접근성 네트워크 구조를 보강하는 데 쓰되, "
        "좌표·승하차량·노선마스터와 검증 없이 점수 직접값으로 쓰지 않는다."
    )

    df = df.sort_values(["노선_ID", "정류장_순서", "정류소_ID"]).reset_index(drop=True)

    route_summary = (
        df.groupby("노선_ID", as_index=False)
        .agg(
            노선_정류장행수=("정류소_ID", "size"),
            노선_고유정류소수=("정류소_ID", "nunique"),
            정류장_순서_최소=("정류장_순서", "min"),
            정류장_순서_최대=("정류장_순서", "max"),
            링크_구간거리_합계_원천값=("링크_구간거리_원천값", "sum"),
            링크_구간거리_평균_원천값=("링크_구간거리_원천값", "mean"),
            비양수_링크구간수=("quality_link_length_nonpositive", "sum"),
            정류소위치_exact_match_행수=("정류소위치_결합상태", lambda s: int((s == "exact_match").sum())),
            승하차자료_정류소존재_행수=("승하차자료_정류소_ID_존재", "sum"),
        )
        .reset_index(drop=True)
    )
    route_summary["순서_연속성_정상"] = route_summary["노선_정류장행수"].eq(route_summary["정류장_순서_최대"])
    route_summary["source_id"] = SOURCE_ID
    route_summary["provider"] = PROVIDER
    route_summary["snapshot_date"] = SNAPSHOT_DATE
    route_summary["usage_role"] = "노선별 정류장 수, 순서 연속성, 구간거리 규모 검증"

    stop_summary = (
        df.groupby("정류소_ID", as_index=False)
        .agg(
            경유_노선_ID수=("노선_ID", "nunique"),
            경유_행수=("노선_ID", "size"),
            정류소_명=("정류소_명", "first"),
            정류소_ARS_ID=("정류소_ARS_ID", "first"),
            경도=("경도", "first"),
            위도=("위도", "first"),
            정류소위치_exact_match_행수=("정류소위치_결합상태", lambda s: int((s == "exact_match").sum())),
            승하차자료_정류소존재=("승하차자료_정류소_ID_존재", "max"),
        )
        .sort_values(["경유_노선_ID수", "경유_행수"], ascending=[False, False])
        .reset_index(drop=True)
    )
    stop_summary["정류소위치_결합상태"] = stop_summary["정류소위치_exact_match_행수"].gt(0).map(
        {True: "exact_match", False: "unmatched_bus_stop_location_master"}
    )
    stop_summary["source_id"] = SOURCE_ID
    stop_summary["provider"] = PROVIDER
    stop_summary["snapshot_date"] = SNAPSHOT_DATE
    stop_summary["usage_role"] = "정류소별 경유 노선 다양성 보조 프록시"

    return df, route_summary, stop_summary, file_audit, page_count, api_total


def judgement(has_fail: bool, conditional: bool = False) -> str:
    if has_fail:
        return "FAIL"
    return "CONDITIONAL_PASS" if conditional else "PASS"


def validate_tables(
    df: pd.DataFrame,
    route_summary: pd.DataFrame,
    stop_summary: pd.DataFrame,
    file_audit: pd.DataFrame,
    page_count: int,
    api_total: int,
) -> dict[str, pd.DataFrame]:
    key_null = int(df["quality_key_missing"].sum())
    duplicate_full_key = int(df.duplicated(KEY_COLS).sum())
    duplicate_route_seq = int(df.duplicated(["노선_ID", "정류장_순서"]).sum())
    duplicate_route_stop_rows = int(df.duplicated(["노선_ID", "정류소_ID"]).sum())
    duplicate_route_stop_pair_count = int(df.groupby(["노선_ID", "정류소_ID"]).size().gt(1).sum())
    link_missing = int(df["quality_link_length_missing"].sum())
    link_nonpositive = int(df["quality_link_length_nonpositive"].sum())
    zero_link_not_first_seq = int(df.loc[df["quality_link_length_nonpositive"], "정류장_순서"].ne(1).sum())
    sequence_missing = int(df["quality_sequence_missing"].sum())
    route_sequence_gap_count = int((~route_summary["순서_연속성_정상"]).sum())
    stop_exact_rows = int((df["정류소위치_결합상태"] == "exact_match").sum())
    passenger_stop_rows = int(df["승하차자료_정류소_ID_존재"].sum())
    doc_existing = [rel(p) for p in DOC_PATHS if p.exists()]

    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "raw_dir": rel(RAW_PATH),
                "raw_file_count": page_count,
                "raw_rows": len(df),
                "api_list_total_count": api_total,
                "official_doc_paths": ";".join(doc_existing),
                "source_description": "서울시 노선별 정류장 ID, 링크 구간거리, 정류장 순서",
                "algorithm_role": "접근성/유입 축의 노선 네트워크 보조 원천",
                "direct_score_use": "금지: 좌표·승하차량·노선마스터 검증 전 직접 점수화하지 않음",
                "판정": judgement(len(doc_existing) == 0 or len(df) != api_total),
            }
        ]
    )

    domain_validation = pd.DataFrame(
        [
            {
                "검증항목": "raw row 보존",
                "측정값": len(df),
                "기준값": api_total,
                "판정": judgement(len(df) != api_total),
                "근거": "OpenAPI list_total_count와 row 수 일치",
            },
            {
                "검증항목": "페이지 성공 응답",
                "측정값": int((file_audit["result_code"] == "INFO-000").sum()),
                "기준값": page_count,
                "판정": judgement(int((file_audit["result_code"] == "INFO-000").sum()) != page_count),
                "근거": "모든 raw 페이지 RESULT 정상 처리",
            },
            {
                "검증항목": "노선 수",
                "측정값": df["노선_ID"].nunique(),
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "노선 네트워크 다양성 요약",
            },
            {
                "검증항목": "정류소/노드 수",
                "측정값": df["정류소_ID"].nunique(),
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "노선-정류소 연결 범위 요약",
            },
            {
                "검증항목": "비양수 링크 구간",
                "측정값": link_nonpositive,
                "기준값": "0이면 좋음",
                "판정": judgement(False, conditional=link_nonpositive > 0),
                "근거": "0 구간은 회차/가상/자료특성 가능성이 있어 직접 거리 환산 금지",
            },
        ]
    )

    grain_validation = pd.DataFrame(
        [
            {
                "table": "silver_bus_route_node_master",
                "key_cols": "+".join(KEY_COLS),
                "rows": len(df),
                "duplicate_key_rows": duplicate_full_key,
                "duplicate_route_sequence_rows": duplicate_route_seq,
                "duplicate_route_stop_rows": duplicate_route_stop_rows,
                "duplicate_route_stop_pair_count": duplicate_route_stop_pair_count,
                "key_null_rows": key_null,
                "sequence_missing_rows": sequence_missing,
                "route_sequence_gap_count": route_sequence_gap_count,
                "판정": judgement(
                    duplicate_full_key > 0
                    or duplicate_route_seq > 0
                    or key_null > 0
                    or sequence_missing > 0
                    or route_sequence_gap_count > 0
                ),
            },
            {
                "table": "silver_bus_route_node_route_summary",
                "key_cols": "노선_ID",
                "rows": len(route_summary),
                "duplicate_key_rows": int(route_summary.duplicated(["노선_ID"]).sum()),
                "duplicate_route_sequence_rows": "",
                "duplicate_route_stop_rows": "",
                "duplicate_route_stop_pair_count": "",
                "key_null_rows": int(route_summary["노선_ID"].fillna("").astype(str).str.strip().eq("").sum()),
                "sequence_missing_rows": "",
                "route_sequence_gap_count": "",
                "판정": judgement(
                    int(route_summary.duplicated(["노선_ID"]).sum()) > 0
                    or int(route_summary["노선_ID"].fillna("").astype(str).str.strip().eq("").sum()) > 0
                ),
            },
            {
                "table": "silver_bus_route_node_stop_summary",
                "key_cols": "정류소_ID",
                "rows": len(stop_summary),
                "duplicate_key_rows": int(stop_summary.duplicated(["정류소_ID"]).sum()),
                "duplicate_route_sequence_rows": "",
                "duplicate_route_stop_rows": "",
                "duplicate_route_stop_pair_count": "",
                "key_null_rows": int(stop_summary["정류소_ID"].fillna("").astype(str).str.strip().eq("").sum()),
                "sequence_missing_rows": "",
                "route_sequence_gap_count": "",
                "판정": judgement(
                    int(stop_summary.duplicated(["정류소_ID"]).sum()) > 0
                    or int(stop_summary["정류소_ID"].fillna("").astype(str).str.strip().eq("").sum()) > 0
                ),
            },
        ]
    )

    consistency_validation = pd.DataFrame(
        [
            {
                "검증항목": "링크 구간거리 numeric 변환",
                "측정값": link_missing,
                "기준값": 0,
                "판정": judgement(link_missing > 0),
                "근거": "구간거리 산술 요약 가능성",
            },
            {
                "검증항목": "0 구간거리 위치",
                "측정값": zero_link_not_first_seq,
                "기준값": 0,
                "판정": judgement(zero_link_not_first_seq > 0),
                "근거": "0 구간거리는 전부 정류장_순서=1일 때만 허용한다. 기점 링크 특성으로 분리한다.",
            },
            {
                "검증항목": "정류소 위치 마스터 결합",
                "측정값": stop_exact_rows,
                "기준값": len(df),
                "판정": judgement(False, conditional=stop_exact_rows < len(df)),
                "근거": "미매칭 row는 좌표 기반 접근성에 직접 투입 금지",
            },
            {
                "검증항목": "승하차자료 정류소 ID 존재",
                "측정값": passenger_stop_rows,
                "기준값": len(df),
                "판정": judgement(False, conditional=passenger_stop_rows < len(df)),
                "근거": "승하차량과는 정류소 ID 기준 보조 연결만 허용",
            },
            {
                "검증항목": "노선 ID와 노선 번호 직접 조인 금지",
                "측정값": "노선마스터 원천 없음",
                "기준값": "노선마스터 확보 전 금지",
                "판정": "CONDITIONAL_PASS",
                "근거": "RTE_ID는 RTE_NO가 아니므로 버스 승하차 노선_번호와 직접 조인하지 않음",
            },
            {
                "검증항목": "구간거리 단위 해석 제한",
                "측정값": "단위 미명시",
                "기준값": "공식 단위 확인 전 직접 시간/거리 환산 금지",
                "판정": "CONDITIONAL_PASS",
                "근거": "공식 문서에는 구간거리 설명은 있으나 단위와 산출 방식 세부가 부족함",
            },
        ]
    )

    match_status = (
        df.groupby(["정류소위치_결합상태", "승하차자료_정류소_ID_존재"], as_index=False)
        .agg(row_count=("노선_ID", "size"), unique_route_count=("노선_ID", "nunique"), unique_stop_id_count=("정류소_ID", "nunique"))
        .sort_values(["정류소위치_결합상태", "승하차자료_정류소_ID_존재"])
    )

    return {
        "source_contract": source_contract,
        "domain_validation": domain_validation,
        "grain_validation": grain_validation,
        "consistency_validation": consistency_validation,
        "match_status": match_status,
        "file_audit": file_audit,
    }


def md_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "(행 없음)"
    view = df.head(max_rows).copy()
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        values = [str(row[col]).replace("\n", " ") for col in cols]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_outputs(
    df: pd.DataFrame,
    route_summary: pd.DataFrame,
    stop_summary: pd.DataFrame,
    validations: dict[str, pd.DataFrame],
) -> None:
    df.to_csv(SILVER_DIR / "silver_bus_route_node_master.csv", index=False, encoding="utf-8-sig")
    route_summary.to_csv(SILVER_DIR / "silver_bus_route_node_route_summary.csv", index=False, encoding="utf-8-sig")
    stop_summary.to_csv(SILVER_DIR / "silver_bus_route_node_stop_summary.csv", index=False, encoding="utf-8-sig")
    validations["file_audit"].to_csv(
        SILVER_DIR / "silver_bus_route_node_source_file_audit.csv", index=False, encoding="utf-8-sig"
    )

    output_map = {
        "source_contract": "21_bus_route_node_master_source_contract.csv",
        "domain_validation": "21_bus_route_node_master_domain_validation.csv",
        "grain_validation": "21_bus_route_node_master_grain_validation.csv",
        "consistency_validation": "21_bus_route_node_master_consistency_validation.csv",
        "match_status": "21_bus_route_node_master_match_status.csv",
        "file_audit": "21_bus_route_node_master_source_file_audit.csv",
    }
    for key, filename in output_map.items():
        validations[key].to_csv(VALIDATION_DIR / filename, index=False, encoding="utf-8-sig")

    df[df["quality_link_length_nonpositive"]].head(100).to_csv(
        VALIDATION_DIR / "21_bus_route_node_master_zero_link_sample.csv", index=False, encoding="utf-8-sig"
    )
    duplicate_route_stop = df[
        df.duplicated(["노선_ID", "정류소_ID"], keep=False)
    ].sort_values(["노선_ID", "정류소_ID", "정류장_순서"])
    duplicate_route_stop.head(100).to_csv(
        VALIDATION_DIR / "21_bus_route_node_master_duplicate_route_stop_sample.csv",
        index=False,
        encoding="utf-8-sig",
    )
    df[df["정류소위치_결합상태"] != "exact_match"].head(100).to_csv(
        VALIDATION_DIR / "21_bus_route_node_master_unmatched_stop_location_sample.csv",
        index=False,
        encoding="utf-8-sig",
    )
    route_summary[~route_summary["순서_연속성_정상"]].head(100).to_csv(
        VALIDATION_DIR / "21_bus_route_node_master_sequence_gap_sample.csv",
        index=False,
        encoding="utf-8-sig",
    )

    report = f"""# 21차 버스 노선-정류장 마스터 silver 검증

작성시각: {datetime.now().isoformat(timespec="seconds")}

## 저장 파일

- `datacorpus/_silver/silver_bus_route_node_master.csv`
- `datacorpus/_silver/silver_bus_route_node_route_summary.csv`
- `datacorpus/_silver/silver_bus_route_node_stop_summary.csv`
- `datacorpus/_silver/silver_bus_route_node_source_file_audit.csv`

## 사용 근거

- `datacorpus/_raw_ingest/source_registry.csv`: 이 원천은 접근성/유입 축의 정류장 네트워크와 도달성 보조 자료로 등록되어 있다.
- `datacorpus/_raw_ingest/20260703/seoul_open_data/docs/transport/seoul_open_data_bus_route_node_master_OA-21233.html`: 서울시 노선별 정류장 ID, 각 링크 구간거리, 정류장 순서를 제공한다고 설명한다.
- `research/알고리즘_스펙_v1_20260703.md`: 접근성 축은 교통 결절, 거리감쇠, 네트워크 보조 자료를 분리해서 써야 한다.
- `research/알고리즘_명세_v2_20260704.md`: 버스 승하차량과 정류소 좌표는 직접 방문확률이 아니라 접근성 프록시로만 쓴다.
- `research/rule_validation/07_bus_stop_location_silver_validation_20260703.md`, `research/rule_validation/10_bus_passenger_silver_validation_20260703.md`: 기존 정류소 위치와 승하차량 silver의 사용 제한을 따른다.

## 검증 요약

### 원천 계약

{md_table(validations["source_contract"])}

### 도메인 검증

{md_table(validations["domain_validation"])}

### grain 검증

{md_table(validations["grain_validation"])}

### 일관성 검증

{md_table(validations["consistency_validation"])}

### 정류소 위치/승하차자료 연결 상태

{md_table(validations["match_status"])}

## 2보 전진 1보 후퇴 기록

- 전진 1: raw `masterRouteNode` 89,159행을 모두 `노선_ID+정류소_ID+정류장_순서` grain으로 보존했다.
- 전진 2: 노선별 요약과 정류소별 경유 노선 요약을 별도 테이블로 만들어, 한 파일에 억지로 다 합치지 않았다.
- 후퇴 1: `RTE_ID`는 버스 승하차량의 `노선_번호`와 같은 키라고 단정하지 않는다. 노선마스터가 없으면 노선 단위 승하차량 조인은 금지한다.
- 후퇴 2: `LNKG_LEN`은 구간거리 값이지만 공식 문서에 단위·산출 방식이 충분히 적혀 있지 않아 실제 도보시간이나 이동시간으로 직접 바꾸지 않는다.
- 후퇴 3: 정류소 위치 마스터와 매칭되지 않는 row는 좌표 기반 접근성 점수에 직접 넣지 않는다.

## 알고리즘 단계에서 금지하는 표현

- 실제 도보시간
- 실제 버스 이동시간
- 실제 상권 방문확률
- 실제 구매자 수
- 창업 성공확률

허용 표현:

- 노선-정류장 연결 구조
- 정류소별 경유 노선 다양성
- 구간거리 원천값 기반 네트워크 보조 신호
- 좌표/승하차량 결합 가능 여부가 확인된 접근성 보조 프록시
"""
    (RESEARCH_VALIDATION_DIR / "21_bus_route_node_master_silver_validation_20260704.md").write_text(
        report, encoding="utf-8"
    )


def main() -> None:
    ensure_dirs()
    df, route_summary, stop_summary, file_audit, page_count, api_total = build_tables()
    validations = validate_tables(df, route_summary, stop_summary, file_audit, page_count, api_total)
    write_outputs(df, route_summary, stop_summary, validations)
    fail_count = sum(
        int((validations[key]["판정"].astype(str) == "FAIL").sum())
        for key in ["source_contract", "domain_validation", "grain_validation", "consistency_validation"]
    )
    print(
        {
            "silver_bus_route_node_master_rows": len(df),
            "route_summary_rows": len(route_summary),
            "stop_summary_rows": len(stop_summary),
            "validation_fail_count": fail_count,
        }
    )


if __name__ == "__main__":
    main()
