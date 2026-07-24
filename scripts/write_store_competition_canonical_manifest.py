from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "datacorpus" / "_raw_ingest"
AUDIT_CSV = RAW_ROOT / "store_competition_source_audit.csv"
OUT_CSV = RAW_ROOT / "store_competition_canonical_manifest.csv"
RUN_LOG = RAW_ROOT / "run_logs" / "20260703_store_competition_canonical_manifest_ko.md"

FIELDS = [
    "source_group",
    "canonical_role_ko",
    "path",
    "row_filter_ko",
    "quarter_min",
    "quarter_max",
    "row_count",
    "use_priority",
    "reason_ko",
]


def read_audit() -> list[dict[str, str]]:
    with AUDIT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def is_direct_root(path: str) -> bool:
    return path.count("\\") == 1 and not path.startswith("datacorpus\\_")


def build_rows(audit_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in audit_rows:
        judgement = row["adoption_judgement_ko"]
        group = row["source_group"]
        path = row["path"]
        if judgement == "중복보류":
            continue

        if group == "seoul_store_trade_area" and judgement == "최신루트후보":
            rows.append(
                {
                    "source_group": group,
                    "canonical_role_ko": "최신 분기 점포-상권 보강",
                    "path": path,
                    "row_filter_ko": "silver 적재 시 기준_년분기_코드=20261만 사용한다. 20251~20254는 2025 연도별 대표 파일과 중복될 수 있다.",
                    "quarter_min": row["quarter_min"],
                    "quarter_max": row["quarter_max"],
                    "row_count": row["row_count"],
                    "use_priority": "10",
                    "reason_ko": "루트 파일은 2026년 최신 분기를 포함하지만 2025년 분기도 함께 포함하므로 최신 분기 보강 파일로만 채택한다.",
                }
            )
        elif group == "seoul_store_trade_area":
            rows.append(
                {
                    "source_group": group,
                    "canonical_role_ko": "연도별 점포-상권 대표 파일",
                    "path": path,
                    "row_filter_ko": "해당 파일의 전체 분기를 사용한다.",
                    "quarter_min": row["quarter_min"],
                    "quarter_max": row["quarter_max"],
                    "row_count": row["row_count"],
                    "use_priority": "20",
                    "reason_ko": "SHA 중복이 아닌 연도별 대표 파일이다. 상권-업종-분기 단위 경쟁/개폐업 지표의 핵심 원천으로 채택한다.",
                }
            )
        elif group == "seoul_store_trade_area_hinterland":
            rows.append(
                {
                    "source_group": group,
                    "canonical_role_ko": "상권 배후지 점포 보조 파일",
                    "path": path,
                    "row_filter_ko": "상권 내부 점포 지표와 섞지 않고 배후지 보조축으로 별도 사용한다.",
                    "quarter_min": row["quarter_min"],
                    "quarter_max": row["quarter_max"],
                    "row_count": row["row_count"],
                    "use_priority": "30",
                    "reason_ko": "배후지 범위는 상권 내부와 다르므로 경쟁점 직접 산정이 아니라 보조 설명 변수로 채택한다.",
                }
            )
        elif group == "sbdc_store_seoul_file":
            rows.append(
                {
                    "source_group": group,
                    "canonical_role_ko": "SBDC 서울 개별 점포 좌표 파일",
                    "path": path,
                    "row_filter_ko": "서울특별시 행만 이미 포함된 파일로 보고 전체 사용한다.",
                    "quarter_min": row["quarter_min"],
                    "quarter_max": row["quarter_max"],
                    "row_count": row["row_count"],
                    "use_priority": "15",
                    "reason_ko": "개별 점포 좌표, 주소, 업종코드가 있어 반경 경쟁점과 집적효과 산정에 직접 사용한다.",
                }
            )
        elif group == "sbdc_major_commercial_area":
            if not is_direct_root(path):
                continue
            rows.append(
                {
                    "source_group": group,
                    "canonical_role_ko": "SBDC 주요상권 경계 비교 파일",
                    "path": path,
                    "row_filter_ko": "시도명=서울특별시 행을 중심으로 사용한다.",
                    "quarter_min": row["quarter_min"],
                    "quarter_max": row["quarter_max"],
                    "row_count": row["row_count"],
                    "use_priority": "40",
                    "reason_ko": "동일 SHA 사본 중 루트 파일을 대표본으로 채택한다. 서울시 상권 경계와 외부 주요상권 경계 비교에 사용한다.",
                }
            )
    return sorted(rows, key=lambda r: (int(r["use_priority"]), r["path"]))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_log(rows: list[dict[str, Any]]) -> None:
    lines = [
        "# 2026-07-03 점포·경쟁 대표 원천 manifest",
        "",
        "## 목적",
        "",
        "점포·경쟁 축에서 실제 silver 적재에 사용할 대표 원천 파일을 명시한다. 중복 파일은 삭제하지 않고 감사표에 남기며, 이 manifest에는 대표본만 올린다.",
        "",
        "## 산출물",
        "",
        f"- 대표 원천 manifest: `{OUT_CSV.relative_to(ROOT)}`",
        f"- 기반 감사표: `{AUDIT_CSV.relative_to(ROOT)}`",
        "",
        "## 대표본 수",
        "",
        f"- 총 {len(rows)}개",
        "",
        "## 중요한 필터 규칙",
        "",
        "- 루트 `서울시 상권분석서비스(점포-상권).csv`는 2025년과 2026년 분기를 함께 포함하므로, 대표 manifest에서는 2026년 최신 분기 보강 파일로만 표시했다.",
        "- 2019~2025 연도별 점포-상권 파일은 `(1)` 사본을 제외한 대표 파일만 사용한다.",
        "- 점포-상권배후지는 상권 내부 점포와 공간 범위가 다르므로 별도 보조축으로만 사용한다.",
        "- SBDC 서울 상가업소 파일은 개별 점포 좌표 기반 반경 경쟁점 원천으로 사용한다.",
        "",
        f"작성 시각: {datetime.now().isoformat(timespec='seconds')}",
    ]
    RUN_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows(read_audit())
    write_csv(OUT_CSV, rows)
    write_log(rows)
    print({"canonical_manifest": str(OUT_CSV), "rows": len(rows), "log": str(RUN_LOG)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
