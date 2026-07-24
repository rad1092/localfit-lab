from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "datacorpus" / "_raw_ingest" / "ingest_manifest.csv"


REPAIRS = {
    ("20260703_092301_raw_smoke", "seoul_trade_area_boundary"): {
        "quality_notes_ko": "이전 스모크 실행에서 API 키 파싱 오류 가능성이 있어 이후 본수집 결과로 대체했다. 이 행은 유효 원천으로 집계하지 않는다.",
    },
    ("20260703_101539_kosis_manual_register", "kosis_population_business_survival"): {
        "dataset_name": "KOSIS OpenAPI 공식 개발가이드 PDF",
        "quality_notes_ko": "KOSIS 통계목록, 본자료, getMeta 호출 절차를 확인하기 위해 공식 개발가이드를 보존했다.",
    },
    ("20260703_103938_kosis_current_devguide", "kosis_population_business_survival"): {
        "dataset_name": "KOSIS 현행 통계자료 API 개발가이드 HTML",
        "quality_notes_ko": "KOSIS 본자료 호출 엔드포인트가 Param/statisticsParameterData.do임을 확인하기 위해 현행 공식 개발가이드 HTML을 저장했다.",
    },
    ("20260703_104247_reb_catalog", "reb_small_shop_rent"): {
        "provider": "한국부동산원 R-ONE",
        "dataset_name": "R-ONE 부동산통계 OpenAPI 통계표 전체 목록",
        "quality_notes_ko": "상업용부동산 임대료/공실률 본자료 후보를 찾기 위해 R-ONE 통계표 전체 목록 원응답을 저장했다.",
    },
}


SUPERSEDED_RETRY_NOTE = (
    "최신 순영업소득 수집. 상권 비용 대비 운영성과 참고 지표. "
    "재시도 run 20260703_104813_reb_office_income_retry에서 같은 표 전체 페이지를 다시 저장했으므로 "
    "이 부분 저장분은 집계에서 제외한다."
)


def main() -> None:
    with MANIFEST.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys()) if rows else []
    changed = 0
    for row in rows:
        key = (row.get("run_id", ""), row.get("source_id", ""))
        if key in REPAIRS:
            row.update(REPAIRS[key])
            changed += 1
        if (
            row.get("run_id") == "20260703_104557_reb_commercial_rent"
            and row.get("collection_status") == "superseded_partial_retry"
            and "TT242303134253883" in row.get("raw_path", "")
        ):
            row["quality_notes_ko"] = SUPERSEDED_RETRY_NOTE
            changed += 1
    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print({"changed_rows": changed})


if __name__ == "__main__":
    main()
