from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "datacorpus" / "_raw_ingest"
CANDIDATES = RAW_ROOT / "20260703" / "metadata" / "existing_datacorpus_candidates.csv"
SAMPLES = RAW_ROOT / "20260703" / "seoul_open_data" / "core_samples"
OUTPUT = RAW_ROOT / "seoul_core_coverage_audit.csv"

FIELDS = [
    "source_id",
    "service",
    "dataset_name_ko",
    "api_total_count",
    "direct_root_rows",
    "canonical_candidate_file_count",
    "canonical_candidate_rows_sum",
    "full_api_raw_pages",
    "full_api_raw_rows",
    "coverage_judgement_ko",
    "recommended_action_ko",
    "evidence_paths",
]

SERVICES = [
    {
        "source_id": "seoul_trade_area_boundary",
        "service": "TbgisTrdarRelm",
        "dataset_name_ko": "서울 상권분석서비스 영역-상권",
        "must_contain": "영역-상권",
        "exclude": [],
    },
    {
        "source_id": "seoul_sales_trade_area",
        "service": "VwsmTrdarSelngQq",
        "dataset_name_ko": "서울 상권분석서비스 추정매출-상권",
        "must_contain": "추정매출-상권",
        "exclude": ["상권배후지", "자치구"],
    },
    {
        "source_id": "seoul_store_trade_area",
        "service": "VwsmTrdarStorQq",
        "dataset_name_ko": "서울 상권분석서비스 점포-상권",
        "must_contain": "점포-상권",
        "exclude": ["상권배후지"],
    },
    {
        "source_id": "seoul_floating_population_trade_area",
        "service": "VwsmTrdarFlpopQq",
        "dataset_name_ko": "서울 상권분석서비스 길단위인구-상권",
        "must_contain": "길단위인구-상권",
        "exclude": [],
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "service": "VwsmTrdarRepopQq",
        "dataset_name_ko": "서울 상권분석서비스 상주인구-상권",
        "must_contain": "상주인구-상권",
        "exclude": [],
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "service": "VwsmTrdarWrcPopltnQq",
        "dataset_name_ko": "서울 상권분석서비스 직장인구-상권",
        "must_contain": "직장인구-상권",
        "exclude": [],
    },
    {
        "source_id": "seoul_trade_area_change_index",
        "service": "VwsmTrdarIxQq",
        "dataset_name_ko": "서울 상권분석서비스 상권변화지표-상권",
        "must_contain": "상권변화지표-상권",
        "exclude": [],
    },
    {
        "source_id": "seoul_facility_trade_area",
        "service": "VwsmTrdarFcltyQq",
        "dataset_name_ko": "서울 상권분석서비스 집객시설-상권",
        "must_contain": "집객시설-상권",
        "exclude": [],
    },
]


def read_api_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in SAMPLES.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if isinstance(value, dict) and "list_total_count" in value:
                counts[key] = int(value.get("list_total_count") or 0)
    return counts


def count_data_rows(path: Path) -> int:
    with path.open("rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def candidate_matches(row: dict[str, str], spec: dict[str, object]) -> bool:
    candidate_path = row["candidate_path"]
    if row["source_id"] != spec["source_id"]:
        return False
    if row["extension"].lower() != ".csv":
        return False
    if spec["must_contain"] not in candidate_path:
        return False
    return not any(token in candidate_path for token in spec["exclude"])


def is_direct_root(path_text: str) -> bool:
    normalized = path_text.replace("/", "\\")
    return normalized.startswith("datacorpus\\") and normalized.count("\\") == 1


def count_full_api_raw_rows(service: str) -> tuple[int, int, int, list[str]]:
    service_dir = RAW_ROOT / "20260703" / "seoul_open_data" / "full" / service
    if not service_dir.exists():
        return 0, 0, 0, []
    pages = sorted(service_dir.glob("*.json"))
    row_count = 0
    total_counts: set[int] = set()
    evidence_paths: list[str] = []
    for path in pages:
        data = json.loads(path.read_text(encoding="utf-8"))
        payload = data.get(service, {})
        rows = payload.get("row", []) if isinstance(payload, dict) else []
        row_count += len(rows or [])
        if isinstance(payload, dict) and payload.get("list_total_count") is not None:
            total_counts.add(int(payload.get("list_total_count") or 0))
        if len(evidence_paths) < 20:
            evidence_paths.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    full_total = next(iter(total_counts)) if len(total_counts) == 1 else 0
    return len(pages), row_count, full_total, evidence_paths


def judgement(api_total: int, direct_rows: int, canonical_rows: int, full_raw_rows: int) -> tuple[str, str]:
    if api_total and full_raw_rows == api_total:
        return (
            "전체 API 원응답 수집 행 수가 API 총 건수와 일치한다.",
            "새로 수집한 전체 원응답을 raw 기준 증빙으로 채택하고, 기존 CSV와 기간/중복을 비교해 silver 적재한다.",
        )
    if api_total and direct_rows == api_total:
        return (
            "루트 CSV 행 수가 API 총 건수와 일치한다.",
            "전체 API 재수집보다 기존 CSV를 원천 후보로 유지하고 샘플 API 원응답을 증빙으로 둔다.",
        )
    if api_total and canonical_rows >= api_total:
        return (
            "루트 CSV만으로는 부족하지만 연도별 후보를 포함하면 API 총 건수 이상을 보유한다.",
            "연도별 파일의 기간 중복을 정리한 뒤 원천 후보로 승격한다. 전체 API 재수집은 중복 가능성이 높으므로 보류한다.",
        )
    if api_total and canonical_rows < api_total:
        return (
            "기존 후보를 모두 합쳐도 API 총 건수보다 적다.",
            "페이지네이션 본수집을 우선 검토한다.",
        )
    return (
        "API 총 건수를 확인하지 못했다.",
        "샘플 응답 또는 데이터 문서를 다시 확인한다.",
    )


def main() -> None:
    api_counts = read_api_counts()
    with CANDIDATES.open("r", encoding="utf-8-sig", newline="") as f:
        candidates = list(csv.DictReader(f))

    output_rows: list[dict[str, str]] = []
    for spec in SERVICES:
        service = str(spec["service"])
        matches = [row for row in candidates if candidate_matches(row, spec)]
        direct_rows = 0
        canonical_rows = 0
        evidence_paths: list[str] = []
        full_pages, full_raw_rows, full_api_total, full_evidence_paths = count_full_api_raw_rows(service)
        api_total = full_api_total or api_counts.get(service, 0)

        for row in matches:
            path = ROOT / row["candidate_path"]
            if not path.exists():
                continue
            rows = count_data_rows(path)
            canonical_rows += rows
            evidence_paths.append(row["candidate_path"])
            if is_direct_root(row["candidate_path"]):
                direct_rows += rows

        evidence_paths.extend(full_evidence_paths)
        coverage, action = judgement(api_total, direct_rows, canonical_rows, full_raw_rows)
        output_rows.append(
            {
                "source_id": str(spec["source_id"]),
                "service": service,
                "dataset_name_ko": str(spec["dataset_name_ko"]),
                "api_total_count": str(api_total),
                "direct_root_rows": str(direct_rows),
                "canonical_candidate_file_count": str(len(matches)),
                "canonical_candidate_rows_sum": str(canonical_rows),
                "full_api_raw_pages": str(full_pages),
                "full_api_raw_rows": str(full_raw_rows),
                "coverage_judgement_ko": coverage,
                "recommended_action_ko": action,
                "evidence_paths": " | ".join(evidence_paths[:20]),
            }
        )

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print({"output": str(OUTPUT), "rows": len(output_rows)})


if __name__ == "__main__":
    main()
