from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ingest_common import MANIFEST_FIELDS, RAW_ROOT, append_csv


ROOT = Path(__file__).resolve().parents[1]
RUN_DATE = "20260703"
CANDIDATES = RAW_ROOT / RUN_DATE / "metadata" / "existing_datacorpus_candidates.csv"
REGISTERED = RAW_ROOT / RUN_DATE / "metadata" / "existing_registered_manifest.csv"
INGEST_MANIFEST = RAW_ROOT / "ingest_manifest.csv"

REGISTERED_FIELDS = [
    "source_id",
    "candidate_path",
    "bytes",
    "sha256",
    "mtime",
    "extension",
    "register_status",
    "notes_ko",
]

SOURCE_META = {
    "seoul_trade_area_boundary": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 영역-상권 후보",
        "spatial_unit": "상권",
        "time_unit": "기준연도/버전",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드",
    },
    "seoul_sales_trade_area": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 추정매출-상권 후보",
        "spatial_unit": "상권",
        "time_unit": "분기",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드+서비스업종코드",
    },
    "seoul_store_trade_area": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 점포-상권 후보",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드+서비스업종코드",
    },
    "seoul_floating_population_trade_area": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 길단위인구/유동인구 후보",
        "spatial_unit": "상권",
        "time_unit": "분기/시간대",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드",
    },
    "seoul_resident_worker_population_trade_area": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 상주/직장인구 후보",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드",
    },
    "seoul_trade_area_change_index": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 상권변화지표 후보",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드",
    },
    "seoul_facility_trade_area": {
        "provider": "서울열린데이터광장",
        "dataset_name": "기존 서울 상권분석서비스 집객시설 후보",
        "spatial_unit": "상권",
        "time_unit": "기준연도",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "상권코드",
    },
    "seoul_living_migration": {
        "provider": "서울열린데이터/생활이동",
        "dataset_name": "기존 서울 생활이동 후보",
        "spatial_unit": "자치구/행정동 OD",
        "time_unit": "월/시간",
        "boundary_version": "기존 파일 기준",
        "area_code_type": "행정동/자치구 코드",
    },
    "molit_rtms_commercial_trade": {
        "provider": "국토교통부/공공데이터포털",
        "dataset_name": "기존 상업·업무용 실거래 후보",
        "spatial_unit": "시군구/법정동",
        "time_unit": "월/거래",
        "boundary_version": "",
        "area_code_type": "LAWD_CD",
    },
    "reb_small_shop_rent": {
        "provider": "한국부동산원/공공데이터",
        "dataset_name": "기존 상가 임대료/공실 후보",
        "spatial_unit": "권역/상권유형/지역",
        "time_unit": "분기",
        "boundary_version": "",
        "area_code_type": "지역/권역",
    },
    "sbdc_store_info": {
        "provider": "소상공인시장진흥공단/공공데이터포털",
        "dataset_name": "기존 상가(상권)정보 후보",
        "spatial_unit": "점포 좌표",
        "time_unit": "기준월",
        "boundary_version": "",
        "area_code_type": "상가업소번호+행정동/법정동",
    },
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def load_existing_registered_paths() -> set[str]:
    if not INGEST_MANIFEST.exists():
        return set()
    rows = read_csv(INGEST_MANIFEST)
    return {
        row.get("raw_path", "")
        for row in rows
        if row.get("collection_status") == "existing_registered" and row.get("raw_path")
    }


def main() -> None:
    candidates = read_csv(CANDIDATES)
    already_registered = load_existing_registered_paths()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_existing_register")
    registered_rows: list[dict] = []
    appended = 0
    skipped = 0

    for index, row in enumerate(candidates, start=1):
        rel = row["candidate_path"]
        source_id = row["source_id"]
        path = ROOT / rel
        if not path.exists() or not path.is_file():
            registered_rows.append(
                {
                    **row,
                    "sha256": "",
                    "register_status": "missing",
                    "notes_ko": "후보 목록에는 있으나 현재 파일이 없어 등록하지 않았다.",
                }
            )
            continue

        digest = sha256_file(path)
        registered_rows.append(
            {
                **row,
                "sha256": digest,
                "register_status": "registered",
                "notes_ko": "기존 datacorpus 파일을 새로 이동하거나 복사하지 않고 원천 후보로 해시 등록했다.",
            }
        )

        if rel in already_registered:
            skipped += 1
            continue

        meta = SOURCE_META.get(source_id, {})
        append_csv(
            INGEST_MANIFEST,
            {
                "run_id": run_id,
                "source_id": source_id,
                "snapshot_date": "기존파일",
                "provider": meta.get("provider", ""),
                "dataset_name": meta.get("dataset_name", ""),
                "raw_path": rel,
                "bytes": row.get("bytes", ""),
                "sha256": digest,
                "collection_status": "existing_registered",
                "request_url_redacted": "",
                "request_params_json": json.dumps({"registered_from": "existing_datacorpus_candidates.csv"}, ensure_ascii=False),
                "http_status": "",
                "provider_result_code": "",
                "provider_result_message": "",
                "spatial_unit": meta.get("spatial_unit", ""),
                "time_unit": meta.get("time_unit", ""),
                "source_period": "",
                "boundary_version": meta.get("boundary_version", ""),
                "area_code_type": meta.get("area_code_type", ""),
                "quality_notes_ko": "기존 보유 파일을 원천 후보로 등록했다. 원본 URL/요청 파라미터는 과거 수집 당시 보존되지 않았을 수 있어 이후 보강 필요.",
                "collected_at": now_utc(),
            },
            MANIFEST_FIELDS,
        )
        appended += 1
        if index % 50 == 0:
            print(f"processed={index} appended={appended} skipped={skipped}")

    write_csv(REGISTERED, registered_rows, REGISTERED_FIELDS)
    summary = {
        "run_id": run_id,
        "candidate_count": len(candidates),
        "registered_rows": len([r for r in registered_rows if r.get("register_status") == "registered"]),
        "manifest_appended": appended,
        "manifest_skipped_existing": skipped,
        "registered_manifest": str(REGISTERED.relative_to(ROOT)),
    }
    log_path = RAW_ROOT / "run_logs" / f"{run_id}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
