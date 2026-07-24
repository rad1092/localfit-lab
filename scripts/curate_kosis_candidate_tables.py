from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "datacorpus" / "_raw_ingest" / "20260703" / "kosis" / "kosis_candidate_tables_population_business_survival.csv"
OUTPUT = ROOT / "datacorpus" / "_raw_ingest" / "20260703" / "kosis" / "kosis_curated_candidate_tables.csv"

FIELDS = [
    "use_priority",
    "use_domain",
    "org_id",
    "tbl_id",
    "stat_id",
    "table_name",
    "send_de",
    "reason_ko",
    "caution_ko",
    "raw_json_path",
]

EXCLUDE_TERMS = [
    "고령친화",
    "아동복지",
    "보건",
    "의료",
    "장애",
    "청소년",
    "어린이",
    "노인",
    "다문화",
    "농업",
    "어업",
    "임업",
    "광업",
    "제조업실태",
    "문화예술",
    "스포츠",
    "관광사업체",
    "프랜차이즈",
    "기술인력",
    "연구개발",
    "연금",
    "사회복지",
    "시설",
]


MANUAL_PICKS = {
    "DT_1B040A3": (
        "P0",
        "resident_population_sgg",
        "시군구 단위 성별 주민등록 인구로 서울 자치구 배후수요 기준선에 쓴다.",
        "상권 내부 직접 인구가 아니라 자치구 단위 보정 지표다.",
    ),
    "DT_1B04006": (
        "P0",
        "resident_population_sgg_age1",
        "시군구/1세별 주민등록 인구로 연령대별 수요 보정에 쓴다.",
        "연령대 집계 전 항목 코드와 기간 코드를 확인해야 한다.",
    ),
    "DT_1B04005N": (
        "P0",
        "resident_population_emd_age5",
        "읍면동/5세별 주민등록 인구로 상권-행정동 보정에 쓴다.",
        "행정동 코드 체계와 서울 상권 경계 버전을 함께 관리해야 한다.",
    ),
}


def classify(row: dict[str, str]) -> tuple[str, str, str, str] | None:
    tbl_id = row["tbl_id"]
    name = row["table_name"] or row["list_name"]

    if tbl_id in MANUAL_PICKS:
        return MANUAL_PICKS[tbl_id]

    if any(term in name for term in EXCLUDE_TERMS):
        return None

    if "기업생멸" in name or "생존" in name or "신생" in name or "소멸" in name:
        return (
            "P1",
            "business_demography_survival",
            "기업 생존/신생/소멸 흐름으로 창업 안정성의 외부 벤치마크에 쓴다.",
            "상권 단위 직접 데이터가 아니므로 서울/업종 단위 보정 지표로만 사용한다.",
        )

    if "전국사업체조사" in name or ("사업체" in name and ("산업" in name or "시군구" in name or "읍면동" in name or "종사자" in name)):
        return (
            "P1",
            "business_establishment_density",
            "사업체 수와 종사자 수로 지역 경제활동 밀도와 업무 수요를 보정한다.",
            "상권 점포 데이터와 중복될 수 있으므로 산업분류/행정구역 단위 보정에 제한한다.",
        )

    if "종사자" in name and ("산업" in name or "시군구" in name or "사업체" in name):
        return (
            "P2",
            "worker_density_external",
            "외부 종사자 통계로 서울 상권 직장인구 지표를 교차검증한다.",
            "서울 상권분석 직장인구가 우선 원천이며 KOSIS는 보조 검증이다.",
        )

    return None


def main() -> None:
    with INPUT.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    curated: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        classified = classify(row)
        if not classified:
            continue
        priority, domain, reason, caution = classified
        key = (row["org_id"], row["tbl_id"], row["table_name"] or row["list_name"])
        if key in seen:
            continue
        seen.add(key)
        curated.append(
            {
                "use_priority": priority,
                "use_domain": domain,
                "org_id": row["org_id"],
                "tbl_id": row["tbl_id"],
                "stat_id": row["stat_id"],
                "table_name": row["table_name"] or row["list_name"],
                "send_de": row["send_de"],
                "reason_ko": reason,
                "caution_ko": caution,
                "raw_json_path": row["raw_json_path"],
            }
        )

    curated.sort(key=lambda r: (r["use_priority"], r["use_domain"], r["org_id"], r["tbl_id"], r["table_name"]))
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(curated)

    print({"input_rows": len(rows), "curated_rows": len(curated), "output": str(OUTPUT)})


if __name__ == "__main__":
    main()
