from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATACORPUS = ROOT / "datacorpus"
RAW_ROOT = DATACORPUS / "_raw_ingest"

csv.field_size_limit(100 * 1024 * 1024)

AUDIT_CSV = RAW_ROOT / "store_competition_source_audit.csv"
DUP_CSV = RAW_ROOT / "store_competition_duplicate_groups.csv"
RUN_LOG = RAW_ROOT / "run_logs" / "20260703_store_competition_sources_ko.md"


AUDIT_FIELDS = [
    "source_group",
    "candidate_role_ko",
    "path",
    "bytes",
    "sha256",
    "encoding",
    "row_count",
    "column_count",
    "quarter_min",
    "quarter_max",
    "quarter_count",
    "district_count",
    "main_category_count",
    "service_or_industry_count",
    "key_duplicate_count",
    "longitude_column",
    "latitude_column",
    "adoption_judgement_ko",
    "reason_ko",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_encoding_and_header(path: Path) -> tuple[str, list[str]]:
    last: Exception | None = None
    for enc in ["utf-8-sig", "cp949", "euc-kr"]:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
            return enc, header
        except Exception as exc:
            last = exc
    raise RuntimeError(f"CSV 헤더 읽기 실패: {path} / {last}")


def classify(path: Path) -> tuple[str, str] | None:
    text = str(path)
    name = path.name
    if "점포-상권배후지" in text:
        return "seoul_store_trade_area_hinterland", "상권 배후지 점포 경쟁 보조축"
    if "점포-상권" in text:
        return "seoul_store_trade_area", "상권-업종-분기 점포/개폐업 핵심축"
    if "상가(상권)정보_서울" in name:
        return "sbdc_store_seoul_file", "개별 점포 좌표 기반 반경 경쟁점 핵심축"
    if "주요상권현황" in name:
        return "sbdc_major_commercial_area", "외부 주요상권 경계 비교축"
    return None


def first_existing(header: list[str], names: list[str]) -> str:
    for name in names:
        if name in header:
            return name
    return ""


def normalize_int(value: str) -> str:
    return (value or "").strip()


def audit_csv(path: Path) -> dict[str, Any]:
    classified = classify(path)
    if classified is None:
        raise ValueError(f"지원하지 않는 후보 파일: {path}")
    source_group, role = classified
    encoding, header = detect_encoding_and_header(path)
    quarter_col = first_existing(header, ["기준_년분기_코드", "stdr_yyqu_cd"])
    district_col = first_existing(header, ["시군구명", "자치구_코드_명", "자치구", "sgg_nm"])
    main_cat_col = first_existing(header, ["상권업종대분류명", "상권업종대분류코드"])
    service_col = first_existing(header, ["서비스_업종_코드", "svc_induty_cd", "상권업종소분류코드", "상권업종중분류코드"])
    lon_col = first_existing(header, ["경도", "상권_중심경도", "X좌표", "x 좌표"])
    lat_col = first_existing(header, ["위도", "상권_중심위도", "Y좌표", "y 좌표"])
    store_id_col = first_existing(header, ["상가업소번호"])
    trade_col = first_existing(header, ["상권_코드", "trdar_cd", "상권번호"])

    quarters: set[str] = set()
    districts: set[str] = set()
    main_categories: set[str] = set()
    services: set[str] = set()
    keys_seen: set[tuple[str, ...]] = set()
    key_duplicate_count = 0
    row_count = 0

    if store_id_col:
        key_cols = [store_id_col]
    elif quarter_col and trade_col and service_col:
        key_cols = [quarter_col, trade_col, service_col]
    elif trade_col:
        key_cols = [trade_col]
    else:
        key_cols = []

    with path.open("r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_count += 1
            if quarter_col:
                value = normalize_int(row.get(quarter_col, ""))
                if value:
                    quarters.add(value)
            if district_col:
                value = (row.get(district_col, "") or "").strip()
                if value:
                    districts.add(value)
            if main_cat_col:
                value = (row.get(main_cat_col, "") or "").strip()
                if value:
                    main_categories.add(value)
            if service_col:
                value = (row.get(service_col, "") or "").strip()
                if value:
                    services.add(value)
            if key_cols:
                key = tuple((row.get(col, "") or "").strip() for col in key_cols)
                if all(key):
                    if key in keys_seen:
                        key_duplicate_count += 1
                    else:
                        keys_seen.add(key)

    quarters_sorted = sorted(quarters)
    digest = sha256_file(path)

    adoption, reason = judge(source_group, path, quarters_sorted, row_count, key_duplicate_count)
    return {
        "source_group": source_group,
        "candidate_role_ko": role,
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "encoding": encoding,
        "row_count": row_count,
        "column_count": len(header),
        "quarter_min": quarters_sorted[0] if quarters_sorted else "",
        "quarter_max": quarters_sorted[-1] if quarters_sorted else "",
        "quarter_count": len(quarters_sorted),
        "district_count": len(districts),
        "main_category_count": len(main_categories),
        "service_or_industry_count": len(services),
        "key_duplicate_count": key_duplicate_count,
        "longitude_column": lon_col,
        "latitude_column": lat_col,
        "adoption_judgement_ko": adoption,
        "reason_ko": reason,
    }


def judge(source_group: str, path: Path, quarters: list[str], row_count: int, dup_count: int) -> tuple[str, str]:
    text = str(path)
    if source_group == "seoul_store_trade_area":
        if "(1)" in text:
            return "중복보류", "동일 연도 파일의 복제본 후보이므로 SHA 중복 확인 후 대표본만 채택한다."
        if path.parent == DATACORPUS:
            return "최신루트후보", "루트 CSV는 2026년 최신 분기 후보로 보이며 서울 OpenAPI 총량 감사와 함께 사용한다."
        return "연도별채택후보", f"{quarters[0] if quarters else ''}~{quarters[-1] if quarters else ''} 기간의 상권-업종 점포/개폐업 원천 후보다."
    if source_group == "seoul_store_trade_area_hinterland":
        if "(1)" in text:
            return "중복보류", "배후지 점포 파일의 복제본 후보이므로 대표본만 채택한다."
        return "보조축채택후보", "상권 내부 점포와 구분되는 배후지 점포 지표로 따로 보관한다."
    if source_group == "sbdc_store_seoul_file":
        if row_count == 0:
            return "보류", "서울 상가업소 파일이지만 행이 없다."
        return "채택후보", "개별 점포 좌표와 도로명주소, 업종코드를 포함해 반경 경쟁점/집적효과 산정에 직접 사용 가능하다."
    if source_group == "sbdc_major_commercial_area":
        return "보조축채택후보", "상권좌표 폴리곤을 포함해 서울시 상권 경계와 외부 주요상권 경계를 비교할 수 있다."
    return "검토필요", "자동 판단 규칙 밖의 후보다."


def candidate_files() -> list[Path]:
    files: list[Path] = []
    for path in DATACORPUS.rglob("*.csv"):
        if classify(path) is not None:
            files.append(path)
    return sorted(files, key=lambda p: str(p))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def duplicate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["sha256"]].append(row)

    def canonical_score(row: dict[str, Any]) -> tuple[int, int, str]:
        path = row["path"]
        penalty = 0
        if "(1)" in path:
            penalty += 10
        if "(251128)" in path:
            penalty += 5
        if "\\_unzipped\\" in path:
            penalty += 1
        return (penalty, path.count("\\"), path)

    out: list[dict[str, Any]] = []
    for digest, group in groups.items():
        if len(group) <= 1:
            continue
        canonical = sorted(group, key=canonical_score)[0]
        for item in sorted(group, key=lambda r: r["path"]):
            if item["path"] == canonical["path"]:
                continue
            out.append(
                {
                    "sha256": digest,
                    "canonical_path": canonical["path"],
                    "duplicate_path": item["path"],
                    "source_group": item["source_group"],
                    "bytes": item["bytes"],
                    "row_count": item["row_count"],
                    "notes_ko": "SHA256이 같은 점포/상권 후보 파일이다. 자동 삭제하지 않고 대표본 채택 판단에 사용한다.",
                }
            )
    return out


def write_log(rows: list[dict[str, Any]], dups: list[dict[str, Any]]) -> None:
    counts = Counter(row["source_group"] for row in rows)
    adoption = Counter(row["adoption_judgement_ko"] for row in rows)
    success_sbdc_api = sorted((RAW_ROOT / "20260703" / "sbdc" / "store_api_samples").glob("*.json"))
    lines = [
        "# 2026-07-03 점포·경쟁 원천 감사 기록",
        "",
        "## 목적",
        "",
        "입지 분석의 경쟁/집적 축에 필요한 서울 상권 점포 지표와 SBDC 개별 점포 좌표 원천을 구분한다. 중복 파일은 삭제하지 않고 대표본 채택 후보만 표시한다.",
        "",
        "## 감사 산출물",
        "",
        f"- 파일 감사표: `{AUDIT_CSV.relative_to(ROOT)}`",
        f"- SHA 중복표: `{DUP_CSV.relative_to(ROOT)}`",
        "",
        "## 후보 유형별 파일 수",
        "",
    ]
    for key, value in counts.most_common():
        lines.append(f"- `{key}`: {value}개")
    lines += ["", "## 채택 판단별 파일 수", ""]
    for key, value in adoption.most_common():
        lines.append(f"- `{key}`: {value}개")
    lines += [
        "",
        "## 주요 판단",
        "",
        "- `서울시 상권분석서비스(점포-상권)`은 상권-업종-분기 단위 점포 수, 유사업종 점포 수, 개업률, 폐업률을 제공하므로 상권 내부 경쟁/안정성 축의 핵심 원천으로 둔다.",
        "- `점포-상권배후지`는 상권 내부와 공간 범위가 다르므로 같은 축에 섞지 않고 배후지 보조축으로 둔다.",
        "- `소상공인시장진흥공단_상가(상권)정보_서울_202603.csv`는 개별 점포 좌표와 도로명주소, 업종코드를 포함하므로 반경 경쟁점과 집적효과 산정에 직접 쓸 수 있다.",
        "- `(1)` 폴더의 같은 연도 파일은 SHA 중복 후보가 많으므로 자동 삭제하지 않고 중복표에 남긴 뒤 대표본만 채택한다.",
        "- `주요상권현황_20240101.csv`는 상권좌표 폴리곤을 포함하므로 서울시 상권경계와 외부 주요상권 경계 비교용 보조 원천으로 둔다.",
        "",
        "## SBDC API 확인",
        "",
        f"- SBDC API 샘플 응답 파일 수: {len(success_sbdc_api)}개",
        "- 강남역 반경 500m 전체 업종과 부동산 대분류 L1 샘플 호출을 저장했다.",
        "- API는 전체 적재보다 사용자가 입력한 위치 반경 경쟁점 검증 또는 최신성 보완에 적합하다.",
        "",
        "## 중복 처리 원칙",
        "",
        f"- SHA 중복 후보 행 수: {len(dups)}",
        "- 중복 후보는 삭제하지 않고 `store_competition_duplicate_groups.csv`에 기록한다.",
        "- 실제 silver 적재 시에는 같은 SHA 그룹에서 대표 파일 하나만 사용한다.",
        "",
        f"작성 시각: {datetime.now().isoformat(timespec='seconds')}",
    ]
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = [audit_csv(path) for path in candidate_files()]
    dups = duplicate_rows(rows)
    write_csv(AUDIT_CSV, rows, AUDIT_FIELDS)
    write_csv(
        DUP_CSV,
        dups,
        ["sha256", "canonical_path", "duplicate_path", "source_group", "bytes", "row_count", "notes_ko"],
    )
    write_log(rows, dups)
    print(
        json.dumps(
            {
                "audit_csv": str(AUDIT_CSV),
                "duplicate_csv": str(DUP_CSV),
                "log": str(RUN_LOG),
                "files": len(rows),
                "duplicate_rows": len(dups),
                "source_group_counts": Counter(row["source_group"] for row in rows),
            },
            ensure_ascii=False,
            indent=2,
            default=dict,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
