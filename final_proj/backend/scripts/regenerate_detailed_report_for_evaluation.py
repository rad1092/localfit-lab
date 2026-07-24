from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT_PATH = Path(__file__).resolve()
BACKEND_ROOT = SCRIPT_PATH.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.repositories.commercial_area import CommercialAreaRepository  # noqa: E402
from app.services.interpretive_report import SPEC_VERSION  # noqa: E402
from app.services.report_publisher import (  # noqa: E402
    PUBLIC_PRESENTATION_VERSION,
    publish_report_artifacts,
)
from app.services.single_report import SingleReportService  # noqa: E402


KST = ZoneInfo("Asia/Seoul")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="현재 production 리포트 체인으로 평가 대상 리포트와 PDF를 다시 생성합니다."
    )
    parser.add_argument("--area-code", default="3120189")
    parser.add_argument("--industry-code", default="CS200031")
    parser.add_argument("--budget-manwon", type=int, default=5000)
    parser.add_argument("--artifact-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now(KST).strftime("%Y%m%d%H%M%S%f")
    artifact_id = args.artifact_id or f"export_eval_repair_{stamp}"

    db = SessionLocal()
    try:
        service = SingleReportService(CommercialAreaRepository(db))
        response = service.generate(
            args.area_code,
            business_type=args.industry_code,
            budget=args.budget_manwon,
        )
    finally:
        db.close()

    if response is None:
        raise RuntimeError(f"unknown area code: {args.area_code}")

    report_data = response.model_dump(mode="json")
    cache_meta = report_data.get("cache_meta") or {}
    if cache_meta.get("spec_version") != SPEC_VERSION:
        raise RuntimeError(
            "generated report spec mismatch: "
            f"expected={SPEC_VERSION}, actual={cache_meta.get('spec_version')}"
        )
    if report_data.get("quality_status") != "pass" or report_data.get("validation_issues"):
        raise RuntimeError(
            "report did not reach a clean validation state: "
            f"quality={report_data.get('quality_status')}, "
            f"issues={report_data.get('validation_issues')}"
        )

    artifacts = publish_report_artifacts(artifact_id, report_data)
    report_dir = Path(artifacts["markdown_path"]).resolve().parent
    response_path = report_dir / "report_response.generated.json"
    response_path.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    generation_record = {
        "generated_at": datetime.now(KST).isoformat(),
        "request": {
            "area_code": args.area_code,
            "industry_code": args.industry_code,
            "budget_manwon": args.budget_manwon,
        },
        "spec_version": SPEC_VERSION,
        "presentation_version": PUBLIC_PRESENTATION_VERSION,
        "quality_status": report_data.get("quality_status"),
        "generation_mode": report_data.get("generation_mode"),
        "cache_meta": cache_meta,
        "token_usage": report_data.get("token_usage"),
        "original_validation_issues": report_data.get("original_validation_issues"),
        "final_validation_issues": report_data.get("validation_issues"),
        "fallback_fields": report_data.get("fallback_fields"),
        "artifacts": artifacts,
        "report_response_path": str(response_path),
    }
    record_path = report_dir / "generation_record.json"
    record_path.write_text(
        json.dumps(generation_record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(generation_record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
