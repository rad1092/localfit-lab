from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "datacorpus"
OUT_DIR = DATA_DIR / "_processed"
GENERATED_DIRS = {"_inventory", "_processed", "_analysis_outputs"}


def is_source_file(path: Path) -> bool:
    return path.is_file() and not any(part in GENERATED_DIRS for part in path.parts)


def choose_encoding(path: Path) -> str:
    name = path.name
    if name.startswith("LOCAL_PEOPLE_DONG"):
        return "utf-8-sig"
    if "상가(상권)정보_서울" in name:
        return "utf-8-sig"
    if any(token in name for token in ["서울시 ", "서울시_", "S-DoT", "소상공인", "임대동향", "생활이동", "250_LOCAL_RESD", "행정안전부"]):
        return "cp949"
    sample = path.read_bytes()[:200_000]
    for encoding in ["cp949", "euc-kr", "utf-8-sig", "utf-8"]:
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"


def read_csv_dicts(path: Path):
    with path.open("r", encoding=choose_encoding(path), newline="") as f:
        yield from csv.DictReader(f)


def read_csv_rows(path: Path):
    with path.open("r", encoding=choose_encoding(path), newline="") as f:
        yield from csv.reader(f)


def to_float(value: object) -> float:
    text = "" if value is None else str(value).replace(",", "").strip()
    if text in {"", "-", "nan", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def first_text(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def first_number(row: dict, *names: str) -> float:
    for name in names:
        if name in row and str(row.get(name, "")).strip() != "":
            return to_float(row.get(name))
    return 0.0


def write_rows(path: Path, rows: list[dict], fieldnames: list[str]) -> tuple[Path, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows)


def csv_files(predicate) -> list[Path]:
    paths = [p for p in DATA_DIR.rglob("*.csv") if is_source_file(p) and predicate(p.name)]
    # 같은 파일이 (1) 폴더와 원본 폴더에 동시에 있으면 상대 경로가 짧은 쪽만 쓴다.
    selected: dict[str, Path] = {}
    for path in sorted(paths, key=lambda p: (len(p.relative_to(DATA_DIR).parts), str(p))):
        selected.setdefault(path.name, path)
    return list(selected.values())


def keep_best(groups: dict, key: tuple, row: dict, score: float) -> None:
    old = groups.get(key)
    if old is None or score >= old["_score"]:
        row["_score"] = score
        groups[key] = row


def remove_score(rows: list[dict]) -> list[dict]:
    for row in rows:
        row.pop("_score", None)
    return rows


def summarize_sales() -> tuple[Path, int]:
    paths = csv_files(lambda n: "추정매출-상권" in n and "배후지" not in n and "자치구" not in n)
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (
                row.get("기준_년분기_코드", ""),
                row.get("상권_코드", ""),
                row.get("서비스_업종_코드", ""),
            )
            sales = first_number(row, "당월_매출_금액")
            item = {
                "기준_년분기_코드": key[0],
                "상권_코드": key[1],
                "상권_코드_명": row.get("상권_코드_명", ""),
                "서비스_업종_코드": key[2],
                "서비스_업종_코드_명": row.get("서비스_업종_코드_명", ""),
                "당월_매출_금액": int(sales),
                "당월_매출_건수": int(first_number(row, "당월_매출_건수")),
                "주중_매출_금액": int(first_number(row, "주중_매출_금액")),
                "주말_매출_금액": int(first_number(row, "주말_매출_금액")),
                "남성_매출_금액": int(first_number(row, "남성_매출_금액")),
                "여성_매출_금액": int(first_number(row, "여성_매출_금액")),
                "연령대_20_매출_금액": int(first_number(row, "연령대_20_매출_금액")),
                "연령대_30_매출_금액": int(first_number(row, "연령대_30_매출_금액")),
                "연령대_40_매출_금액": int(first_number(row, "연령대_40_매출_금액")),
                "시간대_11_14_매출_금액": int(first_number(row, "시간대_11~14_매출_금액")),
                "시간대_17_21_매출_금액": int(first_number(row, "시간대_17~21_매출_금액")),
            }
            count = item["당월_매출_건수"]
            item["평균_객단가"] = round(sales / count, 2) if count else 0
            item["주말_매출_비율"] = round(item["주말_매출_금액"] / sales, 4) if sales else 0
            item["여성_매출_비율"] = round(item["여성_매출_금액"] / sales, 4) if sales else 0
            item["2030_매출_비율"] = round((item["연령대_20_매출_금액"] + item["연령대_30_매출_금액"]) / sales, 4) if sales else 0
            item["점심저녁_매출_비율"] = round((item["시간대_11_14_매출_금액"] + item["시간대_17_21_매출_금액"]) / sales, 4) if sales else 0
            keep_best(groups, key, item, sales)

    fields = [
        "기준_년분기_코드", "상권_코드", "상권_코드_명", "서비스_업종_코드", "서비스_업종_코드_명",
        "당월_매출_금액", "당월_매출_건수", "평균_객단가", "주중_매출_금액", "주말_매출_금액",
        "주말_매출_비율", "남성_매출_금액", "여성_매출_금액", "여성_매출_비율",
        "연령대_20_매출_금액", "연령대_30_매출_금액", "연령대_40_매출_금액", "2030_매출_비율",
        "시간대_11_14_매출_금액", "시간대_17_21_매출_금액", "점심저녁_매출_비율",
    ]
    rows = sorted(remove_score(list(groups.values())), key=lambda r: (r["기준_년분기_코드"], r["상권_코드"], r["서비스_업종_코드"]))
    return write_rows(OUT_DIR / "상권_업종_분기별_매출요약.csv", rows, fields)


def summarize_stores() -> tuple[Path, int]:
    paths = csv_files(lambda n: "점포-상권" in n and "배후지" not in n)
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (
                row.get("기준_년분기_코드", ""),
                row.get("상권_코드", ""),
                row.get("서비스_업종_코드", ""),
            )
            store_count = first_number(row, "점포_수", "전체_점포_수")
            item = {
                "기준_년분기_코드": key[0],
                "상권_코드": key[1],
                "상권_코드_명": row.get("상권_코드_명", ""),
                "서비스_업종_코드": key[2],
                "서비스_업종_코드_명": row.get("서비스_업종_코드_명", ""),
                "점포_수": int(store_count),
                "일반_점포_수": int(first_number(row, "일반_점포_수")),
                "프랜차이즈_점포_수": int(first_number(row, "프랜차이즈_점포_수")),
                "유사_업종_점포_수": int(first_number(row, "유사_업종_점포_수", "전체_점포_수", "점포_수")),
                "개업_율": first_number(row, "개업_율", "개업률"),
                "폐업_률": first_number(row, "폐업_률", "폐업률"),
                "개업_점포_수": int(first_number(row, "개업_점포_수")),
                "폐업_점포_수": int(first_number(row, "폐업_점포_수")),
            }
            keep_best(groups, key, item, store_count)

    fields = [
        "기준_년분기_코드", "상권_코드", "상권_코드_명", "서비스_업종_코드", "서비스_업종_코드_명",
        "점포_수", "일반_점포_수", "프랜차이즈_점포_수", "유사_업종_점포_수", "개업_율", "폐업_률", "개업_점포_수", "폐업_점포_수",
    ]
    rows = sorted(remove_score(list(groups.values())), key=lambda r: (r["기준_년분기_코드"], r["상권_코드"], r["서비스_업종_코드"]))
    return write_rows(OUT_DIR / "상권_업종_분기별_점포요약.csv", rows, fields)


def summarize_area_population() -> tuple[Path, int]:
    paths = csv_files(lambda n: "길단위인구-상권" in n)
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (row.get("기준_년분기_코드", ""), row.get("상권_코드", ""))
            total = first_number(row, "총_유동인구_수")
            item = {
                "기준_년분기_코드": key[0],
                "상권_코드": key[1],
                "상권_코드_명": row.get("상권_코드_명", ""),
                "총_유동인구_수": int(total),
                "남성_유동인구_수": int(first_number(row, "남성_유동인구_수")),
                "여성_유동인구_수": int(first_number(row, "여성_유동인구_수")),
                "연령대_20_유동인구_수": int(first_number(row, "연령대_20_유동인구_수")),
                "연령대_30_유동인구_수": int(first_number(row, "연령대_30_유동인구_수")),
                "시간대_11_14_유동인구_수": int(first_number(row, "시간대_11_14_유동인구_수")),
                "시간대_17_21_유동인구_수": int(first_number(row, "시간대_17_21_유동인구_수")),
                "토요일_유동인구_수": int(first_number(row, "토요일_유동인구_수")),
                "일요일_유동인구_수": int(first_number(row, "일요일_유동인구_수")),
            }
            item["여성_비율"] = round(item["여성_유동인구_수"] / total, 4) if total else 0
            item["2030_유동인구_비율"] = round((item["연령대_20_유동인구_수"] + item["연령대_30_유동인구_수"]) / total, 4) if total else 0
            item["주말_유동인구_비율"] = round((item["토요일_유동인구_수"] + item["일요일_유동인구_수"]) / total, 4) if total else 0
            keep_best(groups, key, item, total)

    fields = [
        "기준_년분기_코드", "상권_코드", "상권_코드_명", "총_유동인구_수", "남성_유동인구_수", "여성_유동인구_수",
        "여성_비율", "연령대_20_유동인구_수", "연령대_30_유동인구_수", "2030_유동인구_비율",
        "시간대_11_14_유동인구_수", "시간대_17_21_유동인구_수", "토요일_유동인구_수", "일요일_유동인구_수", "주말_유동인구_비율",
    ]
    rows = sorted(remove_score(list(groups.values())), key=lambda r: (r["기준_년분기_코드"], r["상권_코드"]))
    return write_rows(OUT_DIR / "상권_분기별_유동인구요약.csv", rows, fields)


def summarize_trade_area_table(file_keyword: str, out_name: str, wanted: list[str], score_col: str) -> tuple[Path, int]:
    paths = csv_files(lambda n: file_keyword in n)
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (row.get("기준_년분기_코드", ""), row.get("상권_코드", ""))
            item = {
                "기준_년분기_코드": key[0],
                "상권_코드": key[1],
                "상권_코드_명": row.get("상권_코드_명", ""),
            }
            for col in wanted:
                item[col] = row.get(col, "")
            keep_best(groups, key, item, first_number(row, score_col))
    rows = sorted(remove_score(list(groups.values())), key=lambda r: (r["기준_년분기_코드"], r["상권_코드"]))
    return write_rows(OUT_DIR / out_name, rows, ["기준_년분기_코드", "상권_코드", "상권_코드_명", *wanted])


def summarize_area_master() -> tuple[Path, int]:
    paths = csv_files(lambda n: "영역-상권" in n)
    groups: dict[str, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            code = row.get("상권_코드", "")
            if not code:
                continue
            groups[code] = {
                "상권_코드": code,
                "상권_코드_명": row.get("상권_코드_명", ""),
                "상권_구분_코드_명": row.get("상권_구분_코드_명", ""),
                "자치구_코드": row.get("자치구_코드", ""),
                "자치구_코드_명": row.get("자치구_코드_명", ""),
                "행정동_코드": row.get("행정동_코드", ""),
                "행정동_코드_명": row.get("행정동_코드_명", ""),
                "엑스좌표_값": row.get("엑스좌표_값", ""),
                "와이좌표_값": row.get("와이좌표_값", ""),
                "영역_면적": row.get("영역_면적", ""),
            }
    fields = ["상권_코드", "상권_코드_명", "상권_구분_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드", "행정동_코드_명", "엑스좌표_값", "와이좌표_값", "영역_면적"]
    rows = sorted(groups.values(), key=lambda r: r["상권_코드"])
    return write_rows(OUT_DIR / "상권_영역기본정보.csv", rows, fields)


def summarize_local_people_months() -> tuple[Path, int]:
    paths = csv_files(lambda n: n.startswith("LOCAL_PEOPLE_DONG_") and n.endswith(".csv"))
    groups: dict[tuple, dict] = {}
    for src in paths:
        month = re.search(r"(\d{6})", src.name)
        month_text = month.group(1) if month else ""
        for row in read_csv_dicts(src):
            key = (month_text, row.get("행정동코드", ""), row.get("시간대구분", ""))
            item = groups.setdefault(key, {"기준월": key[0], "행정동코드": key[1], "시간대구분": key[2], "관측수": 0, "총생활인구수_합계": 0.0})
            item["관측수"] += 1
            item["총생활인구수_합계"] += first_number(row, "총생활인구수")
    rows = list(groups.values())
    for row in rows:
        row["총생활인구수_평균"] = round(row["총생활인구수_합계"] / row["관측수"], 2) if row["관측수"] else 0
        row["총생활인구수_합계"] = round(row["총생활인구수_합계"], 2)
    rows.sort(key=lambda r: (r["기준월"], r["행정동코드"], int(r["시간대구분"] or 0)))
    return write_rows(OUT_DIR / "행정동_시간대별_생활인구요약.csv", rows, ["기준월", "행정동코드", "시간대구분", "관측수", "총생활인구수_합계", "총생활인구수_평균"])


def summarize_250m_people() -> tuple[Path, int]:
    paths = csv_files(lambda n: n.startswith("250_LOCAL_RESD_") and n.endswith(".csv"))
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (row.get("일자", ""), row.get("시간", ""), row.get("행정동코드", ""))
            item = groups.setdefault(key, {"일자": key[0], "시간": key[1], "행정동코드": key[2], "격자수": 0, "생활인구합계": 0.0})
            item["격자수"] += 1
            item["생활인구합계"] += first_number(row, "생활인구합계")
    rows = list(groups.values())
    for row in rows:
        row["격자당_생활인구평균"] = round(row["생활인구합계"] / row["격자수"], 2) if row["격자수"] else 0
        row["생활인구합계"] = round(row["생활인구합계"], 2)
    rows.sort(key=lambda r: (r["일자"], r["시간"], r["행정동코드"]))
    return write_rows(OUT_DIR / "행정동_일자시간_250m생활인구요약.csv", rows, ["일자", "시간", "행정동코드", "격자수", "생활인구합계", "격자당_생활인구평균"])


def summarize_sdot_walk() -> tuple[Path, int]:
    paths = csv_files(lambda n: n.startswith("S-DoT_WALK_"))
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (first_text(row, "자치구", "구명"), first_text(row, "행정동", "동명"))
            item = groups.setdefault(key, {"자치구": key[0], "행정동": key[1], "관측수": 0, "방문자수_합계": 0.0})
            item["관측수"] += 1
            item["방문자수_합계"] += first_number(row, "방문자수", "VISITOR_COUNT", "유동인구수", "방문자 수")
    rows = list(groups.values())
    for row in rows:
        row["방문자수_평균"] = round(row["방문자수_합계"] / row["관측수"], 2) if row["관측수"] else 0
        row["방문자수_합계"] = round(row["방문자수_합계"], 2)
    rows.sort(key=lambda r: r["방문자수_합계"], reverse=True)
    return write_rows(OUT_DIR / "SDOT_자치구_행정동_보행요약.csv", rows, ["자치구", "행정동", "관측수", "방문자수_합계", "방문자수_평균"])


def summarize_sdot_nature() -> tuple[Path, int]:
    paths = csv_files(lambda n: n.startswith("S-DoT_NATURE_"))
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (first_text(row, "자치구"), first_text(row, "행정동"))
            item = groups.setdefault(
                key,
                {"자치구": key[0], "행정동": key[1], "관측수": 0, "온도평균_합계": 0.0, "습도평균_합계": 0.0, "소음평균_합계": 0.0, "오존평균_합계": 0.0},
            )
            item["관측수"] += 1
            item["온도평균_합계"] += first_number(row, "온도 평균(℃)")
            item["습도평균_합계"] += first_number(row, "습도 평균(%)")
            item["소음평균_합계"] += first_number(row, "소음 평균(dB)")
            item["오존평균_합계"] += first_number(row, "오존 평균(ppm)")
    rows = list(groups.values())
    for row in rows:
        n = row["관측수"] or 1
        row["온도_평균"] = round(row.pop("온도평균_합계") / n, 3)
        row["습도_평균"] = round(row.pop("습도평균_합계") / n, 3)
        row["소음_평균"] = round(row.pop("소음평균_합계") / n, 3)
        row["오존_평균"] = round(row.pop("오존평균_합계") / n, 5)
    rows.sort(key=lambda r: (r["자치구"], r["행정동"]))
    return write_rows(OUT_DIR / "SDOT_자치구_행정동_환경요약.csv", rows, ["자치구", "행정동", "관측수", "온도_평균", "습도_평균", "소음_평균", "오존_평균"])


def summarize_district_facilities() -> tuple[Path, int]:
    groups: dict[str, dict] = defaultdict(lambda: {"자치구": "", "문화공간_수": 0, "문화행사_수": 0, "공공와이파이_수": 0, "공중화장실_수": 0, "시영주차장_수": 0, "시영주차_총면수": 0})
    mapping = [
        ("서울시 문화공간 정보.csv", "자치구", "문화공간_수", None),
        ("서울시 문화행사 정보.csv", "자치구", "문화행사_수", None),
        ("서울시 공공와이파이 서비스 위치 정보.csv", "자치구", "공공와이파이_수", None),
        ("서울시 공중화장실 위치정보.csv", "구 명칭", "공중화장실_수", None),
        ("서울시 시영주차장 실시간 주차대수 정보.csv", "주소", "시영주차장_수", "총 주차면"),
    ]
    for filename, district_col, count_col, sum_col in mapping:
        paths = csv_files(lambda n, filename=filename: n == filename)
        for src in paths:
            for row in read_csv_dicts(src):
                district = row.get(district_col, "").strip()
                if count_col == "시영주차장_수":
                    district = next((name for name in ["종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구"] if name in district), "")
                if not district:
                    continue
                groups[district]["자치구"] = district
                groups[district][count_col] += 1
                if sum_col:
                    groups[district]["시영주차_총면수"] += int(first_number(row, sum_col))
    rows = sorted(groups.values(), key=lambda r: r["자치구"])
    return write_rows(OUT_DIR / "자치구_생활시설_보조지표.csv", rows, ["자치구", "문화공간_수", "문화행사_수", "공공와이파이_수", "공중화장실_수", "시영주차장_수", "시영주차_총면수"])


def summarize_air_quality() -> tuple[Path, int]:
    paths = csv_files(lambda n: n == "서울시 권역별 실시간 대기환경 현황.csv")
    rows = []
    for src in paths:
        for row in read_csv_dicts(src):
            rows.append(
                {
                    "측정일시": row.get("측정일시", ""),
                    "권역명": row.get("권역명", ""),
                    "측정소명": row.get("측정소명", ""),
                    "미세먼지": first_number(row, "미세먼지(㎍/㎥)"),
                    "초미세먼지": first_number(row, "초미세먼지농도(㎍/㎥)"),
                    "오존": first_number(row, "오존(ppm)"),
                    "통합대기환경등급": row.get("통합대기환경등급", ""),
                    "통합대기환경지수": first_number(row, "통합대기환경지수"),
                }
            )
    return write_rows(OUT_DIR / "권역_대기환경요약.csv", rows, ["측정일시", "권역명", "측정소명", "미세먼지", "초미세먼지", "오존", "통합대기환경등급", "통합대기환경지수"])


def summarize_rent() -> tuple[Path, int]:
    paths = csv_files(lambda n: n.startswith("임대동향 지역별 임대료"))
    rows = []
    seen = set()
    for src in paths:
        for row in read_csv_rows(src):
            if not row or row[0] == "No":
                continue
            joined = "|".join(row)
            if joined in seen:
                continue
            seen.add(joined)
            if not any("서울" in cell for cell in row[:4]):
                continue
            rows.append(
                {
                    "지역1": row[1] if len(row) > 1 else "",
                    "지역2": row[2] if len(row) > 2 else "",
                    "지역3": row[3] if len(row) > 3 else "",
                    "2024년_3분기": row[4] if len(row) > 4 else "",
                    "2024년_4분기": row[5] if len(row) > 5 else "",
                    "2025년_1분기": row[6] if len(row) > 6 else "",
                    "2025년_2분기": row[7] if len(row) > 7 else "",
                    "2025년_3분기": row[8] if len(row) > 8 else "",
                    "2025년_4분기": row[9] if len(row) > 9 else "",
                    "2026년_1분기": row[10] if len(row) > 10 else "",
                }
            )
    return write_rows(OUT_DIR / "임대료_소규모상가_서울권역요약.csv", rows, ["지역1", "지역2", "지역3", "2024년_3분기", "2024년_4분기", "2025년_1분기", "2025년_2분기", "2025년_3분기", "2025년_4분기", "2026년_1분기"])


def summarize_sbdc_shops() -> tuple[Path, int]:
    paths = csv_files(lambda n: "상가(상권)정보_서울" in n)
    groups: dict[tuple, dict] = {}
    for src in paths:
        for row in read_csv_dicts(src):
            key = (row.get("시군구명", ""), row.get("행정동명", ""), row.get("상권업종대분류명", ""), row.get("상권업종중분류명", ""))
            item = groups.setdefault(
                key,
                {"시군구명": key[0], "행정동명": key[1], "상권업종대분류명": key[2], "상권업종중분류명": key[3], "상가업소_수": 0},
            )
            item["상가업소_수"] += 1
    rows = sorted(groups.values(), key=lambda r: (r["시군구명"], r["행정동명"], r["상권업종대분류명"], r["상권업종중분류명"]))
    return write_rows(OUT_DIR / "소상공인_서울_행정동업종_점포요약.csv", rows, ["시군구명", "행정동명", "상권업종대분류명", "상권업종중분류명", "상가업소_수"])


def summarize_movement() -> tuple[Path, int]:
    paths = csv_files(lambda n: n.startswith("생활이동_자치구_") and n.endswith(".csv"))
    rows = []
    for src in paths:
        month = re.search(r"(\d{4}\.\d{2})", src.name)
        hour = re.search(r"_(\d{2})시", src.name)
        rows.append(
            {
                "대상월": month.group(1) if month else "",
                "시간대": hour.group(1) if hour else "",
                "파일명": src.name,
                "상대경로": str(src.relative_to(DATA_DIR)),
                "용량_MB": round(src.stat().st_size / 1024 / 1024, 3),
                "상태": "원본 보관, 전용 배치 집계 필요",
            }
        )
    rows.sort(key=lambda r: (r["대상월"], r["시간대"], r["상대경로"]))
    return write_rows(OUT_DIR / "생활이동_파일커버리지요약.csv", rows, ["대상월", "시간대", "파일명", "상대경로", "용량_MB", "상태"])


def summarize_manifest(outputs: list[tuple[Path, int]]) -> None:
    manifest = {
        "created_for": "서울 상권 상세리포트 데이터 코퍼스 요약",
        "source_dir": str(DATA_DIR),
        "outputs": [{"path": str(path.relative_to(ROOT)), "rows": rows} for path, rows in outputs],
    }
    (OUT_DIR / "요약_생성결과.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [
        summarize_sales,
        summarize_stores,
        summarize_area_population,
        lambda: summarize_trade_area_table(
            "상주인구-상권",
            "상권_분기별_상주인구요약.csv",
            ["총_상주인구_수", "남성_상주인구_수", "여성_상주인구_수", "연령대_20_상주인구_수", "연령대_30_상주인구_수", "연령대_40_상주인구_수", "총_가구_수", "아파트_가구_수"],
            "총_상주인구_수",
        ),
        lambda: summarize_trade_area_table(
            "직장인구-상권",
            "상권_분기별_직장인구요약.csv",
            ["총_직장_인구_수", "남성_직장_인구_수", "여성_직장_인구_수", "연령대_20_직장_인구_수", "연령대_30_직장_인구_수", "연령대_40_직장_인구_수"],
            "총_직장_인구_수",
        ),
        lambda: summarize_trade_area_table(
            "소비-상권",
            "상권_분기별_소비요약.csv",
            ["지출_총금액", "식료품_지출_총금액", "의류_신발_지출_총금액", "생활용품_지출_총금액", "의료비_지출_총금액", "교통_지출_총금액", "여가_지출_총금액", "문화_지출_총금액", "교육_지출_총금액", "유흥_지출_총금액"],
            "지출_총금액",
        ),
        lambda: summarize_trade_area_table(
            "아파트-상권",
            "상권_분기별_아파트요약.csv",
            ["아파트_단지_수", "아파트_평균_면적", "아파트_평균_시가", "아파트_가격_6_억_이상_세대_수"],
            "아파트_단지_수",
        ),
        lambda: summarize_trade_area_table(
            "집객시설-상권",
            "상권_분기별_집객시설요약.csv",
            ["집객시설_수", "관공서_수", "은행_수", "종합병원_수", "일반_병원_수", "약국_수", "초등학교_수", "대학교_수", "백화점_수", "극장_수", "숙박_시설_수", "지하철_역_수", "버스_정거장_수"],
            "집객시설_수",
        ),
        lambda: summarize_trade_area_table(
            "상권변화지표-상권",
            "상권_분기별_변화지표요약.csv",
            ["상권_변화_지표", "상권_변화_지표_명", "운영_영업_개월_평균", "폐업_영업_개월_평균", "서울_운영_영업_개월_평균", "서울_폐업_영업_개월_평균"],
            "운영_영업_개월_평균",
        ),
        summarize_area_master,
        summarize_local_people_months,
        summarize_250m_people,
        summarize_sdot_walk,
        summarize_sdot_nature,
        summarize_district_facilities,
        summarize_air_quality,
        summarize_rent,
        summarize_sbdc_shops,
        summarize_movement,
    ]
    outputs = []
    for task in tasks:
        output = task()
        outputs.append(output)
        print(f"생성 완료: {output[0].name} ({output[1]:,}행)")
    summarize_manifest(outputs)


if __name__ == "__main__":
    main()
