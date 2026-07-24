# -*- coding: utf-8 -*-
"""
규칙 기반 입지판단 엔진 입력 lookup 테이블 생성.

목적:
  1. 사용자가 상권명/업종명을 외워서 입력하지 않도록 위치·업종 선택용 데이터를 만든다.
  2. 화면에는 이름을 보여주되 알고리즘 조인은 반드시 코드로 하게 만든다.
  3. 지도 클릭 좌표는 bbox 1차 후보와 polygon vertex 최종 판정으로 상권_코드에 연결한다.
  4. 업종은 SBDC 대/중/소 계층을 보조로 쓰되 최종 입력은 서울 서비스_업종_코드로 확정한다.

근거 문서:
  - research/전처리_전_최종확인_20260704.md
  - research/전처리_알고리즘_실행계획_20260703.md
  - research/rule_validation/15_trade_area_boundary_silver_validation_20260704.md
  - research/rule_validation/23_gold_preprocessing_validation_20260704.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
GOLD_VALIDATION = ROOT / "datacorpus" / "_gold_validation"
RULE_VALIDATION = ROOT / "research" / "rule_validation"

RUN_DATE = datetime.now().strftime("%Y-%m-%d")
LOOKUP_VERSION = "rule_input_lookup.v1.1-20260704"


SEOUL_SERVICE_LARGE = {
    "CS10": ("SEOUL_CS1", "외식업"),
    "CS20": ("SEOUL_CS2", "서비스업"),
    "CS30": ("SEOUL_CS3", "소매업"),
}


@dataclass
class Validation:
    artifact: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


validations: list[Validation] = []


def ensure_dirs() -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    GOLD_VALIDATION.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def add_validation(
    artifact: str,
    rule_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
) -> None:
    validations.append(
        Validation(
            artifact=artifact,
            rule_name=rule_name,
            observed=observed,
            expected=expected,
            result="PASS" if passed else "FAIL",
            reason_ko=reason_ko,
        )
    )


def null_count(df: pd.DataFrame, columns: Iterable[str]) -> int:
    columns = list(columns)
    return int(df[columns].isna().any(axis=1).sum())


def duplicate_count(df: pd.DataFrame, columns: Iterable[str]) -> int:
    columns = list(columns)
    return int(df.duplicated(columns).sum())


def normalize_for_search(value: object) -> str:
    """검색 보조 문자열은 표시용이다. 조인은 이 값이 아니라 코드로만 한다."""
    text = "" if pd.isna(value) else str(value)
    text = text.lower()
    text = re.sub(r"[\s\-_·/()]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def join_search_parts(*parts: object) -> str:
    return normalize_for_search(" ".join("" if pd.isna(part) else str(part) for part in parts))


def bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def fallback_large_category(service_code: object) -> tuple[str, str]:
    """SBDC 계층이 없을 때만 서울 서비스업종 코드 prefix로 UI 대분류를 만든다."""
    code = str(service_code)
    return SEOUL_SERVICE_LARGE.get(code[:4], ("SEOUL_UNKNOWN", "서울서비스업종_분류검토필요"))


def fallback_medium_category(service_code: object, service_name: object) -> tuple[str, str]:
    """UI 탐색용 fallback 중분류다. 알고리즘 조인이나 점수 근거로 쓰지 않는다."""
    code = str(service_code)
    name = "" if pd.isna(service_name) else str(service_name)

    if code.startswith("CS1"):
        if any(token in name for token in ["커피", "음료", "주점", "호프"]):
            return "SEOUL_CS1_DRINK", "음료/주점"
        if any(token in name for token in ["제과", "패스트", "분식", "치킨"]):
            return "SEOUL_CS1_LIGHT", "간이/제과/분식"
        return "SEOUL_CS1_RESTAURANT", "음식점"

    if code.startswith("CS2"):
        if any(token in name for token in ["학원", "독서실"]):
            return "SEOUL_CS2_EDU", "교육/학습"
        if any(token in name for token in ["병원", "의원", "약국", "동물"]):
            return "SEOUL_CS2_MEDICAL", "의료/동물"
        if any(token in name for token in ["사무소", "법무", "회계", "세무", "변호", "변리", "통번역"]):
            return "SEOUL_CS2_PRO", "전문서비스"
        if any(token in name for token in ["볼링", "게임", "오락", "복권", "DVD", "녹음", "사진", "여행"]):
            return "SEOUL_CS2_LEISURE", "여가/문화"
        if any(token in name for token in ["수리", "청소", "임대"]):
            return "SEOUL_CS2_LIFE", "생활관리/임대"
        if any(token in name for token in ["게스트", "숙박"]):
            return "SEOUL_CS2_STAY", "숙박"
        return "SEOUL_CS2_ETC", "기타서비스"

    if code.startswith("CS3"):
        if any(token in name for token in ["도매", "주류"]):
            return "SEOUL_CS3_WHOLESALE", "도매/유통"
        if any(token in name for token in ["자동차", "모터사이클", "부품", "주유"]):
            return "SEOUL_CS3_AUTO", "자동차/연료"
        if any(token in name for token in ["의류", "한복", "유아"]):
            return "SEOUL_CS3_CLOTHING", "의류"
        if any(token in name for token in ["가구", "재생", "중고"]):
            return "SEOUL_CS3_USED_HOME", "중고/생활용품"
        if any(token in name for token in ["미용", "악기", "예술"]):
            return "SEOUL_CS3_SPECIAL", "전문상품"
        return "SEOUL_CS3_ETC", "기타소매"

    return "SEOUL_UNKNOWN_ETC", "분류검토필요"


def add_ui_industry_hierarchy(hierarchy: pd.DataFrame) -> pd.DataFrame:
    """SBDC 자동강매칭 계층과 서울 서비스코드 fallback을 분리해서 UI용 대/중/세부 계층을 만든다."""
    out = hierarchy.copy()
    # 수동검토필요 SBDC 후보는 화면 계층에서도 쓰지 않는다.
    # 예: 일반의류가 유흥주점/음식점 계층 아래로 들어가는 식의 UI 오분류를 막기 위함이다.
    sbdc_ui_usable = (
        ~out["SBDC_대분류코드_후보"].eq("UNMAPPED")
        & ~bool_series(out["SBDC_mapping_review_required"])
        & out["SBDC_score_use_status"].astype(str).eq("사용가능_자동강매칭")
    )

    fallback_large = out["서비스_업종_코드"].map(fallback_large_category)
    fallback_medium = [
        fallback_medium_category(code, name)
        for code, name in zip(out["서비스_업종_코드"], out["서비스_업종_코드_명"], strict=False)
    ]

    out["UI_대분류코드"] = [
        row["SBDC_대분류코드_후보"] if usable else large[0]
        for (_, row), usable, large in zip(out.iterrows(), sbdc_ui_usable, fallback_large, strict=False)
    ]
    out["UI_대분류명"] = [
        row["SBDC_대분류명_후보"] if usable else large[1]
        for (_, row), usable, large in zip(out.iterrows(), sbdc_ui_usable, fallback_large, strict=False)
    ]
    out["UI_중분류코드"] = [
        row["SBDC_중분류코드_후보"] if usable else medium[0]
        for (_, row), usable, medium in zip(out.iterrows(), sbdc_ui_usable, fallback_medium, strict=False)
    ]
    out["UI_중분류명"] = [
        row["SBDC_중분류명_후보"] if usable else medium[1]
        for (_, row), usable, medium in zip(out.iterrows(), sbdc_ui_usable, fallback_medium, strict=False)
    ]
    out["UI_세부분류코드"] = [
        row["SBDC_소분류코드_후보"] if usable else row["서비스_업종_코드"]
        for (_, row), usable in zip(out.iterrows(), sbdc_ui_usable, strict=False)
    ]
    out["UI_세부분류명"] = [
        row["SBDC_소분류명_후보"] if usable else row["서비스_업종_코드_명"]
        for (_, row), usable in zip(out.iterrows(), sbdc_ui_usable, strict=False)
    ]
    out["UI_계층_근거"] = np_where(sbdc_ui_usable, "SBDC_업종계층", "서울서비스코드_prefix_fallback")
    out["UI_계층_주의사항"] = np_where(
        sbdc_ui_usable,
        "SBDC 자동강매칭 계층을 UI 탐색에 사용한다. 최종 알고리즘 조인은 서비스_업종_코드다.",
        "SBDC 미매핑 또는 수동검토필요 업종이라 서울 서비스코드와 업종명 키워드로 UI 탐색 계층만 보강했다. SBDC 직접 근거로 쓰지 않는다.",
    )
    return out


def np_where(condition: pd.Series, true_value: object, false_value: object) -> pd.Series:
    return pd.Series(
        [true_value if bool(item) else false_value for item in condition],
        index=condition.index,
    )


def clean_json_value(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def build_location_lookup() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    profile = read_csv(GOLD / "gold_trade_area_profile.csv")
    spatial = read_csv(SILVER / "silver_trade_area_boundary_spatial_index.csv")
    vertices = read_csv(SILVER / "silver_trade_area_boundary_vertices.csv")

    required_profile = [
        "상권_코드",
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
        "representative_lon_wgs84",
        "representative_lat_wgs84",
        "geometry_centroid_lon_wgs84",
        "geometry_centroid_lat_wgs84",
        "bbox_min_lon_wgs84",
        "bbox_min_lat_wgs84",
        "bbox_max_lon_wgs84",
        "bbox_max_lat_wgs84",
        "bbox_min_x_epsg5181",
        "bbox_min_y_epsg5181",
        "bbox_max_x_epsg5181",
        "bbox_max_y_epsg5181",
        "point_in_polygon_use_status",
        "source_id",
        "provider",
        "snapshot_date",
    ]
    missing = [column for column in required_profile if column not in profile.columns]
    if missing:
        raise ValueError(f"gold_trade_area_profile 필수 컬럼 없음: {missing}")

    lookup = profile[required_profile].copy()
    lookup["상권_코드"] = lookup["상권_코드"].astype(str)
    lookup["display_label"] = (
        "["
        + lookup["상권_구분_코드_명"].fillna("")
        + "] "
        + lookup["상권_코드_명"].fillna("")
        + " · "
        + lookup["자치구_코드_명"].fillna("")
        + " "
        + lookup["행정동_코드_명"].fillna("")
    ).str.strip()
    lookup["location_search_text"] = lookup.apply(
        lambda row: join_search_parts(
            row["상권_코드"],
            row["상권_코드_명"],
            row["상권_구분_코드_명"],
            row["자치구_코드_명"],
            row["행정동_코드_명"],
        ),
        axis=1,
    )
    lookup["input_resolution_method"] = "지도클릭/주소검색/장소검색 -> 좌표 -> bbox후보 -> polygon포함 -> 상권_코드"
    lookup["point_in_polygon_required"] = True
    lookup["lookup_use_status"] = "사용가능_입력변환전용"
    lookup["score_use_status"] = "점수값아님_상권코드확정용"
    lookup["lookup_version"] = LOOKUP_VERSION
    lookup["algorithm_use_note_ko"] = "화면 표시와 검색 후보에 쓰되, 점수 조인은 상권_코드만 사용한다."

    spatial_out = spatial.copy()
    spatial_out["상권_코드"] = spatial_out["상권_코드"].astype(str)
    spatial_out["lookup_version"] = LOOKUP_VERSION
    spatial_out["lookup_use_status"] = "bbox_1차후보필터"
    spatial_out["algorithm_use_note_ko"] = (
        "지도 클릭 또는 주소 좌표가 들어오면 bbox로 후보 상권을 줄이고, "
        "gold_location_boundary_vertices.csv로 polygon 포함 여부를 최종 확인한다."
    )

    vertices_out = vertices.copy()
    vertices_out["상권_코드"] = vertices_out["상권_코드"].astype(str)
    vertices_out["lookup_version"] = LOOKUP_VERSION
    vertices_out["lookup_use_status"] = "polygon_최종판정"
    vertices_out["algorithm_use_note_ko"] = "좌표가 상권 polygon 내부인지 판정하는 최종 원천이다. 점수값 자체가 아니다."

    write_csv(lookup, GOLD / "gold_location_input_lookup.csv")
    write_csv(spatial_out, GOLD / "gold_location_spatial_index.csv")
    write_csv(vertices_out, GOLD / "gold_location_boundary_vertices.csv")

    location_codes = set(lookup["상권_코드"])
    spatial_codes = set(spatial_out["상권_코드"])
    vertex_codes = set(vertices_out["상권_코드"])
    bbox_invalid = int(
        (
            (pd.to_numeric(lookup["bbox_min_lon_wgs84"], errors="coerce") >= pd.to_numeric(lookup["bbox_max_lon_wgs84"], errors="coerce"))
            | (pd.to_numeric(lookup["bbox_min_lat_wgs84"], errors="coerce") >= pd.to_numeric(lookup["bbox_max_lat_wgs84"], errors="coerce"))
            | (pd.to_numeric(lookup["bbox_min_x_epsg5181"], errors="coerce") >= pd.to_numeric(lookup["bbox_max_x_epsg5181"], errors="coerce"))
            | (pd.to_numeric(lookup["bbox_min_y_epsg5181"], errors="coerce") >= pd.to_numeric(lookup["bbox_max_y_epsg5181"], errors="coerce"))
        ).sum()
    )
    vertex_min = int(vertices_out.groupby("상권_코드")["vertex_index"].count().min())

    add_validation(
        "gold_location_input_lookup.csv",
        "위치 lookup은 상권_코드 단일 grain",
        f"{len(lookup)}행 / unique={lookup['상권_코드'].nunique()} / key_null={null_count(lookup, ['상권_코드'])}",
        "1650행 / 중복 0 / key_null 0",
        len(lookup) == 1650 and lookup["상권_코드"].nunique() == len(lookup) and null_count(lookup, ["상권_코드"]) == 0,
        "지도·주소 입력은 표시명이 아니라 상권_코드로 확정되어야 하므로 위치 lookup의 grain을 먼저 고정한다.",
    )
    add_validation(
        "gold_location_input_lookup.csv",
        "위치 표시명과 검색문은 조인키가 아님",
        "display_label/location_search_text 생성, algorithm_use_note_ko 존재",
        "표시·검색용 컬럼과 코드 조인 계약 분리",
        {"display_label", "location_search_text", "algorithm_use_note_ko"}.issubset(lookup.columns),
        "상권명은 사람이 보는 값이고 동명이 있을 수 있으므로 코드 조인 계약을 산출물에 명시한다.",
    )
    add_validation(
        "gold_location_spatial_index.csv",
        "bbox 1차 후보 필터 준비",
        f"spatial_coverage={len(location_codes - spatial_codes)} 누락 / bbox_invalid={bbox_invalid}",
        "누락 0 / bbox_invalid 0",
        not (location_codes - spatial_codes) and bbox_invalid == 0,
        "전체 polygon을 매번 훑기 전에 bbox로 후보를 줄여야 지도 클릭 분석의 성능과 정확성을 같이 지킬 수 있다.",
    )
    add_validation(
        "gold_location_boundary_vertices.csv",
        "polygon 최종 판정 vertex 커버리지",
        f"vertex_coverage={len(location_codes - vertex_codes)} 누락 / min_vertex={vertex_min}",
        "누락 0 / 상권별 vertex 3개 이상",
        not (location_codes - vertex_codes) and vertex_min >= 3,
        "bbox 후보만으로는 내부/외부 판정을 끝낼 수 없으므로 polygon vertex가 모든 상권을 덮어야 한다.",
    )

    return lookup, spatial_out, vertices_out


def build_industry_lookup() -> tuple[pd.DataFrame, dict]:
    taxonomy = read_csv(GOLD / "gold_industry_taxonomy.csv")
    required = [
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "매출_원천_존재",
        "점포_원천_존재",
        "mapping_review_required",
        "score_use_status",
        "SBDC_대분류코드_후보",
        "SBDC_대분류명_후보",
        "SBDC_중분류코드_후보",
        "SBDC_중분류명_후보",
        "SBDC_소분류코드_후보",
        "SBDC_소분류명_후보",
        "SBDC_mapping_review_required",
        "SBDC_score_use_status",
        "direct_score_allowed",
        "algorithm_use_note_ko",
    ]
    missing = [column for column in required if column not in taxonomy.columns]
    if missing:
        raise ValueError(f"gold_industry_taxonomy 필수 컬럼 없음: {missing}")

    hierarchy = taxonomy[required].copy()
    hierarchy["서비스_업종_코드"] = hierarchy["서비스_업종_코드"].astype(str)
    for column in ["SBDC_대분류코드_후보", "SBDC_중분류코드_후보", "SBDC_소분류코드_후보"]:
        hierarchy[column] = hierarchy[column].fillna("UNMAPPED")
    for column in ["SBDC_대분류명_후보", "SBDC_중분류명_후보", "SBDC_소분류명_후보"]:
        hierarchy[column] = hierarchy[column].fillna("매핑검토필요")
    hierarchy = add_ui_industry_hierarchy(hierarchy)

    hierarchy["industry_display_label"] = hierarchy["서비스_업종_코드_명"].fillna("") + " (" + hierarchy["서비스_업종_코드"] + ")"
    hierarchy["industry_search_text"] = hierarchy.apply(
        lambda row: join_search_parts(
            row["서비스_업종_코드"],
            row["서비스_업종_코드_명"],
            row["UI_대분류명"],
            row["UI_중분류명"],
            row["UI_세부분류명"],
            row["SBDC_대분류명_후보"] if row["UI_계층_근거"] == "SBDC_업종계층" else "",
            row["SBDC_중분류명_후보"] if row["UI_계층_근거"] == "SBDC_업종계층" else "",
            row["SBDC_소분류명_후보"] if row["UI_계층_근거"] == "SBDC_업종계층" else "",
        ),
        axis=1,
    )
    hierarchy["selection_path"] = (
        hierarchy["UI_대분류명"].astype(str)
        + " > "
        + hierarchy["UI_중분류명"].astype(str)
        + " > "
        + hierarchy["UI_세부분류명"].astype(str)
        + " > "
        + hierarchy["서비스_업종_코드_명"].astype(str)
    )
    hierarchy["final_algorithm_key"] = hierarchy["서비스_업종_코드"]
    hierarchy["lookup_use_status"] = "사용가능_업종선택전용"
    hierarchy["score_use_status"] = hierarchy["score_use_status"].fillna("검토필요")
    hierarchy["lookup_version"] = LOOKUP_VERSION
    hierarchy["algorithm_use_note_ko"] = (
        "SBDC 계층은 화면 선택과 유사업종 맥락 보조이며, 최종 알고리즘 조인은 서비스_업종_코드만 사용한다."
    )

    write_csv(hierarchy, GOLD / "gold_industry_selection_hierarchy.csv")

    tree = {
        "lookup_version": LOOKUP_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "gold_industry_taxonomy.csv",
        "rule_ko": "대/중/세부 분류는 선택 UI용이고 최종 알고리즘 키는 서비스_업종_코드다. SBDC 미매핑 업종은 서울 서비스코드 fallback 계층으로만 보강한다.",
        "large_categories": [],
    }
    for (large_code, large_name), large_df in hierarchy.groupby(["UI_대분류코드", "UI_대분류명"], dropna=False, sort=True):
        large_node = {
            "code": clean_json_value(large_code),
            "name": clean_json_value(large_name),
            "medium_categories": [],
        }
        for (medium_code, medium_name), medium_df in large_df.groupby(["UI_중분류코드", "UI_중분류명"], dropna=False, sort=True):
            medium_node = {
                "code": clean_json_value(medium_code),
                "name": clean_json_value(medium_name),
                "small_categories": [],
            }
            for (small_code, small_name), small_df in medium_df.groupby(["UI_세부분류코드", "UI_세부분류명"], dropna=False, sort=True):
                industries = []
                for _, row in small_df.sort_values(["서비스_업종_코드"]).iterrows():
                    industries.append(
                        {
                            "service_industry_code": clean_json_value(row["서비스_업종_코드"]),
                            "service_industry_name": clean_json_value(row["서비스_업종_코드_명"]),
                            "display_label": clean_json_value(row["industry_display_label"]),
                            "final_algorithm_key": clean_json_value(row["final_algorithm_key"]),
                            "mapping_review_required": bool(clean_json_value(row["SBDC_mapping_review_required"])),
                            "score_use_status": clean_json_value(row["score_use_status"]),
                            "ui_hierarchy_source": clean_json_value(row["UI_계층_근거"]),
                        }
                    )
                medium_node["small_categories"].append(
                    {
                        "code": clean_json_value(small_code),
                        "name": clean_json_value(small_name),
                        "service_industries": industries,
                    }
                )
            large_node["medium_categories"].append(medium_node)
        tree["large_categories"].append(large_node)

    with (GOLD / "gold_industry_selection_tree.json").open("w", encoding="utf-8") as fp:
        json.dump(tree, fp, ensure_ascii=False, indent=2)

    review_required = bool_series(hierarchy["SBDC_mapping_review_required"])
    direct_allowed = bool_series(hierarchy["direct_score_allowed"])
    missing_hierarchy = int(
        (
            hierarchy[["SBDC_대분류코드_후보", "SBDC_중분류코드_후보", "SBDC_소분류코드_후보"]]
            .eq("UNMAPPED")
            .any(axis=1)
        ).sum()
    )
    ui_missing_hierarchy = int(
        hierarchy[["UI_대분류코드", "UI_중분류코드", "UI_세부분류코드"]]
        .isna()
        .any(axis=1)
        .sum()
    )
    review_required_count = int(review_required.sum())
    ui_fallback_count = int((hierarchy["UI_계층_근거"] == "서울서비스코드_prefix_fallback").sum())

    add_validation(
        "gold_industry_selection_hierarchy.csv",
        "업종 lookup은 서비스_업종_코드 단일 grain",
        f"{len(hierarchy)}행 / unique={hierarchy['서비스_업종_코드'].nunique()} / key_null={null_count(hierarchy, ['서비스_업종_코드'])}",
        "100행 / 중복 0 / key_null 0",
        len(hierarchy) == 100 and hierarchy["서비스_업종_코드"].nunique() == len(hierarchy) and null_count(hierarchy, ["서비스_업종_코드"]) == 0,
        "업종명은 표시용이고 실제 점수 입력은 서울 서비스_업종_코드여야 한다.",
    )
    add_validation(
        "gold_industry_selection_hierarchy.csv",
        "대/중/세부 UI 선택 경로 생성",
        f"ui_missing_hierarchy={ui_missing_hierarchy}, sbdc_missing={missing_hierarchy}, review_required={review_required_count}, ui_fallback={ui_fallback_count}",
        "UI 계층 누락 0 / SBDC 미매핑·수동검토필요는 fallback으로 보강",
        ui_missing_hierarchy == 0 and ui_fallback_count == review_required_count,
        "SBDC 계층이 없거나 수동검토필요인 업종을 숨기지 않고, 서울 서비스코드 fallback으로 화면 탐색만 가능하게 해야 한다.",
    )
    add_validation(
        "gold_industry_selection_hierarchy.csv",
        "SBDC 미매핑 상태 보존",
        f"sbdc_missing={missing_hierarchy}, review_required={int(review_required.sum())}",
        "SBDC 미매핑은 검토 플래그로 계속 노출",
        missing_hierarchy > 0 and int(review_required.sum()) >= missing_hierarchy,
        "fallback UI 계층은 사용성을 위한 보강일 뿐이므로, SBDC 자동매칭처럼 둔갑시키면 안 된다.",
    )
    add_validation(
        "gold_industry_selection_hierarchy.csv",
        "점수 가능 업종은 매출/점포 원천을 가진다",
        f"direct_allowed={int(direct_allowed.sum())}, missing_sales_or_store={int((~bool_series(hierarchy['매출_원천_존재']) | ~bool_series(hierarchy['점포_원천_존재'])).sum())}",
        "direct_allowed 업종은 매출·점포 원천 존재",
        int((direct_allowed & (~bool_series(hierarchy["매출_원천_존재"]) | ~bool_series(hierarchy["점포_원천_존재"]))).sum()) == 0,
        "상권×업종 점수는 매출과 점포 원천이 있는 서울 서비스업종만 직접 점수로 쓸 수 있다.",
    )
    add_validation(
        "gold_industry_selection_tree.json",
        "업종 tree JSON은 CSV와 같은 서비스업종 집합",
        sum(
            len(small["service_industries"])
            for large in tree["large_categories"]
            for medium in large["medium_categories"]
            for small in medium["small_categories"]
        ),
        len(hierarchy),
        sum(
            len(small["service_industries"])
            for large in tree["large_categories"]
            for medium in large["medium_categories"]
            for small in medium["small_categories"]
        )
        == len(hierarchy),
        "화면이 JSON tree를 읽더라도 CSV와 다른 업종 집합을 보여주면 안 된다.",
    )

    return hierarchy, tree


def write_validation_outputs() -> None:
    validation_df = pd.DataFrame([validation.__dict__ for validation in validations])
    validation_path = GOLD_VALIDATION / "26_input_lookup_rule_validation.csv"
    write_csv(validation_df, validation_path)

    summary = (
        validation_df.groupby("result", dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("result")
    )
    write_csv(summary, GOLD_VALIDATION / "26_input_lookup_rule_validation_summary.csv")

    manifest_rows = []
    for path in [
        GOLD / "gold_location_input_lookup.csv",
        GOLD / "gold_location_spatial_index.csv",
        GOLD / "gold_location_boundary_vertices.csv",
        GOLD / "gold_industry_selection_hierarchy.csv",
        GOLD / "gold_industry_selection_tree.json",
        validation_path,
    ]:
        manifest_rows.append(
            {
                "path": str(path.relative_to(ROOT)),
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else None,
                "lookup_version": LOOKUP_VERSION,
                "note_ko": "하드코딩 제거용 입력 lookup 또는 검증 산출물",
            }
        )
    write_csv(pd.DataFrame(manifest_rows), GOLD_VALIDATION / "26_input_lookup_manifest.csv")

    report_path = RULE_VALIDATION / "26_input_lookup_hardcoding_removal_validation_20260704.md"
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    report = [
        "# 위치·업종 입력 lookup 전처리 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "상권명과 업종명을 코드에 하드코딩하지 않고, 전처리된 데이터에서 위치·업종 선택 후보를 읽을 수 있게 만드는 검증이다.",
        "",
        "## 2. 생성 산출물",
        "",
        "| 파일 | 의미 |",
        "|---|---|",
        "| `datacorpus/_gold/gold_location_input_lookup.csv` | 상권 검색·목록·대표좌표용 lookup |",
        "| `datacorpus/_gold/gold_location_spatial_index.csv` | 지도 클릭/주소 좌표의 bbox 1차 후보 필터 |",
        "| `datacorpus/_gold/gold_location_boundary_vertices.csv` | polygon 내부 여부 최종 판정 vertex |",
        "| `datacorpus/_gold/gold_industry_selection_hierarchy.csv` | 대/중/세부 업종 선택 후 서울 서비스업종 코드 확정 |",
        "| `datacorpus/_gold/gold_industry_selection_tree.json` | 화면에서 바로 읽기 쉬운 계층형 업종 tree |",
        "",
        "## 3. 핵심 규칙",
        "",
        "1. 위치 입력은 최종적으로 `상권_코드`로 확정한다.",
        "2. 지도 클릭은 bbox 후보 필터 후 polygon 포함 여부로 판정한다.",
        "3. 업종 선택은 대/중/세부 이름을 보여주되 최종 키는 `서비스_업종_코드`다.",
        "4. 표시명, 검색문, 계층명은 조인키가 아니다.",
        "5. 매핑이 약한 업종은 숨기지 않고 검토 플래그로 남긴다.",
        "",
        "## 4. 규칙 검증 결과",
        "",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        "",
        "| 산출물 | 규칙 | 관측값 | 기대값 | 결과 | 이유 |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
        report.append(
            "| {artifact} | {rule_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                artifact=row["artifact"],
                rule_name=row["rule_name"],
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=row["reason_ko"],
            )
        )
    report.extend(
        [
            "",
            "## 5. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: 상권 목록을 코드에 박지 않고 `gold_location_input_lookup.csv`에서 읽게 만들었다.",
            "2. 전진: 업종 목록을 코드에 박지 않고 `gold_industry_selection_tree.json`에서 읽게 만들었다.",
            "3. 후퇴: bbox만으로 상권을 확정하지 않는다. bbox는 후보 필터이고 최종 판정은 polygon vertex로 한다.",
            "4. 후퇴: SBDC 업종 계층과 서울 서비스코드 fallback 계층은 UI 보조이며 점수 조인의 주키가 아니다.",
            "5. 재검토: 다음 단계에서 실제 지도 클릭 좌표를 넣어 `상권_코드`가 제대로 나오는 resolver를 테스트해야 한다.",
            "",
            "## 6. 다음 작업",
            "",
            "1. 좌표 입력을 받아 bbox 후보와 polygon 판정을 수행하는 resolver를 만든다.",
            "2. 업종 tree를 화면에서 대/중/세부 순서로 읽을 수 있게 연결한다.",
            "3. 위치·업종이 확정된 뒤에만 점수 알고리즘을 호출한다.",
        ]
    )
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")

    summary_json = {
        "lookup_version": LOOKUP_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "validation_csv": str(validation_path.relative_to(ROOT)),
        "report": str(report_path.relative_to(ROOT)),
    }
    (GOLD_VALIDATION / "26_input_lookup_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if fail_count:
        raise SystemExit(f"[input_lookup] validation failed: {fail_count}")


def main() -> None:
    ensure_dirs()
    location_lookup, spatial_index, vertices = build_location_lookup()
    industry_hierarchy, industry_tree = build_industry_lookup()
    write_validation_outputs()
    print(
        "[input_lookup] done "
        f"locations={len(location_lookup):,}, spatial={len(spatial_index):,}, "
        f"vertices={len(vertices):,}, industries={len(industry_hierarchy):,}, "
        f"large_categories={len(industry_tree['large_categories'])}"
    )


if __name__ == "__main__":
    main()
