from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "datacorpus"
PROCESSED_DIR = DATA_DIR / "_processed"
FINAL_DIR = DATA_DIR / "_final"
SPATIAL_DIR = FINAL_DIR / "spatial_od"
REPORTFACTS_DIR = FINAL_DIR / "reportfacts"
MODEL_READY_DIR = FINAL_DIR / "model_ready"

GENERATED_DIRS = {"_inventory", "_processed", "_analysis_outputs", "_final"}

SEOUL_MOBILITY_CODE = {
    "11010": ("11110", "종로구"),
    "11020": ("11140", "중구"),
    "11030": ("11170", "용산구"),
    "11040": ("11200", "성동구"),
    "11050": ("11215", "광진구"),
    "11060": ("11230", "동대문구"),
    "11070": ("11260", "중랑구"),
    "11080": ("11290", "성북구"),
    "11090": ("11305", "강북구"),
    "11100": ("11320", "도봉구"),
    "11110": ("11350", "노원구"),
    "11120": ("11380", "은평구"),
    "11130": ("11410", "서대문구"),
    "11140": ("11440", "마포구"),
    "11150": ("11470", "양천구"),
    "11160": ("11500", "강서구"),
    "11170": ("11530", "구로구"),
    "11180": ("11545", "금천구"),
    "11190": ("11560", "영등포구"),
    "11200": ("11590", "동작구"),
    "11210": ("11620", "관악구"),
    "11220": ("11650", "서초구"),
    "11230": ("11680", "강남구"),
    "11240": ("11710", "송파구"),
    "11250": ("11740", "강동구"),
}

SEOUL_LEGAL_CODE_TO_NAME = {legal: name for _, (legal, name) in SEOUL_MOBILITY_CODE.items()}
SEOUL_DISTRICTS = set(SEOUL_LEGAL_CODE_TO_NAME)


def ensure_dirs() -> None:
    for path in [FINAL_DIR, SPATIAL_DIR, REPORTFACTS_DIR, MODEL_READY_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def is_source_file(path: Path) -> bool:
    return path.is_file() and not any(part in GENERATED_DIRS for part in path.parts)


def choose_encoding(path: Path) -> str:
    name = path.name
    if name.startswith("LOCAL_PEOPLE_DONG") or "상가(상권)정보_서울" in name:
        return "utf-8-sig"
    if any(token in name for token in ["서울시 ", "서울시_", "S-DoT", "소상공인", "임대동향", "생활이동", "250_LOCAL_RESD", "행정안전부"]):
        return "cp949"
    sample = path.read_bytes()[:200_000]
    for enc in ["utf-8-sig", "cp949", "euc-kr", "utf-8"]:
        try:
            sample.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    return "utf-8-sig"


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding=choose_encoding(path), low_memory=False, **kwargs)


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False).str.replace("*", "", regex=False), errors="coerce")


def quarter_code_from_month(value: object) -> int | float:
    text = re.sub(r"[^0-9]", "", str(value))
    if len(text) < 6:
        return np.nan
    year = int(text[:4])
    month = int(text[4:6])
    return year * 10 + math.ceil(month / 3)


def month_text_from_quarter(quarter: object) -> str:
    text = str(quarter)
    if len(text) < 5:
        return ""
    year = int(text[:4])
    q = int(text[-1])
    return f"{year}-{(q - 1) * 3 + 1:02d}~{year}-{q * 3:02d}"


def clean_key(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.strip()


def write_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def write_json(data: object, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def source_csv_files(predicate) -> list[Path]:
    return sorted(p for p in DATA_DIR.rglob("*.csv") if is_source_file(p) and predicate(p.name))


def pick_one_file(predicate, label: str) -> Path:
    files = source_csv_files(predicate)
    if not files:
        raise FileNotFoundError(f"{label} 파일을 찾지 못했습니다.")
    return sorted(files, key=lambda p: (("(1)" in str(p)), len(p.parts), str(p)))[0]


def load_area_master() -> pd.DataFrame:
    path = PROCESSED_DIR / "상권_영역기본정보.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    for col in ["상권_코드", "자치구_코드", "행정동_코드"]:
        if col in df.columns:
            df[col] = clean_key(df[col])
    return df


def safe_read_geopandas():
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError as exc:
        raise SystemExit("GeoPandas가 필요합니다. uv run --with geopandas --with pyogrio --with shapely --with pyproj 로 실행하세요.") from exc
    return gpd, Point


def make_valid(gdf):
    gdf = gdf.copy()
    gdf = gdf[gdf.geometry.notna()].copy()
    try:
        gdf["geometry"] = gdf.geometry.make_valid()
    except Exception:
        gdf["geometry"] = gdf.geometry.buffer(0)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    return gdf


def read_trade_area_gdf():
    gpd, _ = safe_read_geopandas()
    path = next(p for p in DATA_DIR.rglob("*.shp") if "영역-상권" in p.name)
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(5181)
    gdf = make_valid(gdf)
    rename = {
        "TRDAR_CD": "상권_코드",
        "TRDAR_CD_N": "상권_코드_명",
        "TRDAR_SE_1": "상권_구분_코드_명",
        "SIGNGU_CD": "자치구_코드",
        "SIGNGU_CD_": "자치구_코드_명",
        "ADSTRD_CD": "행정동_코드",
        "ADSTRD_CD_": "행정동_코드_명",
        "RELM_AR": "영역_면적",
    }
    gdf = gdf.rename(columns=rename)
    for col in ["상권_코드", "자치구_코드", "행정동_코드"]:
        if col in gdf.columns:
            gdf[col] = clean_key(gdf[col])
    return gdf


def build_geo_source_manifest() -> pd.DataFrame:
    gpd, _ = safe_read_geopandas()
    rows = []
    for shp in sorted(DATA_DIR.rglob("*.shp")):
        try:
            gdf = gpd.read_file(shp)
            key_cols = [c for c in gdf.columns if c != "geometry" and any(token in c.upper() for token in ["CD", "CODE", "SGG", "DONG", "TRDAR", "AREA"])]
            rows.append(
                {
                    "원천경로": str(shp.relative_to(DATA_DIR)),
                    "CRS": str(gdf.crs) if gdf.crs is not None else "없음_EPSG5181_수동지정대상",
                    "geometry_type": ",".join(sorted(set(gdf.geometry.geom_type.dropna().astype(str)))) if "geometry" in gdf else "",
                    "record_count": len(gdf),
                    "key_columns": ", ".join(key_cols),
                    "신뢰도": "공식 prj 확인" if gdf.crs is not None else "prj 없음, 좌표범위와 필드 기준 EPSG:5181로 처리",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "원천경로": str(shp.relative_to(DATA_DIR)),
                    "CRS": "읽기실패",
                    "geometry_type": "",
                    "record_count": 0,
                    "key_columns": "",
                    "신뢰도": f"읽기 실패: {type(exc).__name__}",
                }
            )
    manifest = pd.DataFrame(rows)
    write_csv(manifest, SPATIAL_DIR / "공간원천_매니페스트.csv")
    return manifest


def read_place_gdfs():
    gpd, _ = safe_read_geopandas()
    rows = []
    for shp in DATA_DIR.rglob("*.shp"):
        name = shp.name
        parent = str(shp.parent)
        if "주요 121장소" in parent:
            dataset = "서울_주요121장소"
        elif "주요 82장소" in parent:
            dataset = "서울_주요82장소"
        else:
            continue
        gdf = gpd.read_file(shp)
        if gdf.crs is None:
            gdf = gdf.set_crs(4326)
        gdf = make_valid(gdf).to_crs(5181)
        gdf["공간데이터셋"] = dataset
        rows.append(gdf[["공간데이터셋", "AREA_CD", "CATEGORY", "AREA_NM", "geometry"]])
    if not rows:
        return gpd.GeoDataFrame(columns=["공간데이터셋", "AREA_CD", "CATEGORY", "AREA_NM", "geometry"], crs=5181)
    gdf = pd.concat(rows, ignore_index=True)
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=5181)
    gdf = gdf.drop_duplicates(subset=["공간데이터셋", "AREA_CD", "AREA_NM"]).copy()
    return gdf


def read_sbdc365_gdfs():
    gpd, _ = safe_read_geopandas()
    rows = []
    for shp in DATA_DIR.rglob("*.shp"):
        text = str(shp.parent)
        label = None
        for token in ["배달상권", "성장상권", "신생상권", "역주행상권"]:
            if token in text:
                label = token
                break
        if not label:
            continue
        gdf = gpd.read_file(shp)
        if gdf.crs is None:
            gdf = gdf.set_crs(5181)
        gdf = make_valid(gdf).to_crs(5181)
        gdf["소상공인365_유형"] = label
        if "SGG_CD" in gdf.columns:
            gdf["SGG_CD"] = clean_key(gdf["SGG_CD"])
            gdf = gdf[gdf["SGG_CD"].str.startswith("11", na=False)].copy()
        keep = ["소상공인365_유형", "CRTR_YM", "MJR_BZZNNO", "MJR_BIZON_", "SGG_CD", "DONG_CD", "ARA", "geometry"]
        rows.append(gdf[[c for c in keep if c in gdf.columns]])
    if not rows:
        return gpd.GeoDataFrame(columns=["소상공인365_유형", "geometry"], crs=5181)
    gdf = pd.concat(rows, ignore_index=True)
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=5181)


def aggregate_sbdc365_admin_features(sbdc365) -> pd.DataFrame:
    if len(sbdc365) == 0:
        out = pd.DataFrame(columns=["자치구_코드", "행정동_코드"])
        write_csv(out, SPATIAL_DIR / "소상공인365_행정동자치구_피처.csv")
        return out
    df = sbdc365.drop(columns="geometry", errors="ignore").copy()
    df["자치구_코드"] = clean_key(df["SGG_CD"]) + "0"
    df["행정동_코드"] = clean_key(df["DONG_CD"])
    df["면적_m2"] = to_num(df.get("ARA", pd.Series(index=df.index))).fillna(0)
    grouped = (
        df.groupby(["자치구_코드", "행정동_코드", "소상공인365_유형"], dropna=False)
        .agg(상권수=("MJR_BZZNNO", "nunique"), 총면적_m2=("면적_m2", "sum"), 평균면적_m2=("면적_m2", "mean"))
        .reset_index()
    )
    wide = grouped.pivot(index=["자치구_코드", "행정동_코드"], columns="소상공인365_유형", values=["상권수", "총면적_m2", "평균면적_m2"]).fillna(0)
    wide.columns = [f"소상공인365_{typ}_{metric}" for metric, typ in wide.columns]
    out = wide.reset_index()
    for col in [c for c in out.columns if c.endswith("_상권수")]:
        out[f"{col}_존재"] = (out[col] > 0).astype(int)
    write_csv(out, SPATIAL_DIR / "소상공인365_행정동자치구_피처.csv")
    return out


def overlay_area_features(trade_gdf):
    gpd, _ = safe_read_geopandas()
    trade = trade_gdf[["상권_코드", "상권_코드_명", "자치구_코드_명", "행정동_코드_명", "geometry"]].copy()
    base = trade_gdf.drop(columns="geometry").copy()
    base["상권_폴리곤_면적_m2"] = trade_gdf.geometry.area.round(2)
    cent = trade_gdf.geometry.centroid
    cent_wgs = gpd.GeoSeries(cent, crs=trade_gdf.crs).to_crs(4326)
    base["상권_중심경도"] = cent_wgs.x.round(7)
    base["상권_중심위도"] = cent_wgs.y.round(7)
    write_csv(base.drop(columns="geometry", errors="ignore"), SPATIAL_DIR / "상권_공간기준테이블.csv")

    detail_frames = []
    place_gdf = read_place_gdfs()
    if len(place_gdf):
        joined = gpd.overlay(
            trade,
            place_gdf,
            how="intersection",
            keep_geom_type=False,
        )
        if len(joined):
            joined["겹침면적_m2"] = joined.geometry.area
            detail = joined.drop(columns="geometry").copy()
            write_csv(detail, SPATIAL_DIR / "상권_주요장소_겹침상세.csv")
            pivot = (
                detail.groupby(["상권_코드", "공간데이터셋"])
                .agg(주요장소_겹침수=("AREA_CD", "nunique"), 주요장소_겹침면적_m2=("겹침면적_m2", "sum"))
                .reset_index()
            )
            wide = pivot.pivot(index="상권_코드", columns="공간데이터셋", values=["주요장소_겹침수", "주요장소_겹침면적_m2"])
            wide.columns = [f"{col2}_{col1}" for col1, col2 in wide.columns]
            base = base.merge(wide.reset_index(), on="상권_코드", how="left")
            top = (
                detail.sort_values("겹침면적_m2", ascending=False)
                .groupby("상권_코드", as_index=False)
                .first()[["상권_코드", "AREA_NM", "CATEGORY"]]
                .rename(columns={"AREA_NM": "대표_주요장소명", "CATEGORY": "대표_주요장소유형"})
            )
            base = base.merge(top, on="상권_코드", how="left")
            detail_frames.append(("주요장소", len(detail)))
    else:
        write_csv(pd.DataFrame(), SPATIAL_DIR / "상권_주요장소_겹침상세.csv")

    sbdc365 = read_sbdc365_gdfs()
    aggregate_sbdc365_admin_features(sbdc365)
    if len(sbdc365):
        joined = gpd.overlay(trade, sbdc365, how="intersection", keep_geom_type=False)
        if len(joined):
            joined["겹침면적_m2"] = joined.geometry.area
            detail = joined.drop(columns="geometry").copy()
            write_csv(detail, SPATIAL_DIR / "상권_소상공인365_겹침상세.csv")
            pivot = (
                detail.groupby(["상권_코드", "소상공인365_유형"])
                .agg(소상공인365_겹침수=("MJR_BZZNNO", "nunique"), 소상공인365_겹침면적_m2=("겹침면적_m2", "sum"))
                .reset_index()
            )
            wide = pivot.pivot(index="상권_코드", columns="소상공인365_유형", values=["소상공인365_겹침수", "소상공인365_겹침면적_m2"])
            wide.columns = [f"{col2}_{col1}" for col1, col2 in wide.columns]
            base = base.merge(wide.reset_index(), on="상권_코드", how="left")
            detail_frames.append(("소상공인365", len(detail)))
    else:
        write_csv(pd.DataFrame(), SPATIAL_DIR / "상권_소상공인365_겹침상세.csv")

    count_cols = [c for c in base.columns if c.endswith("_주요장소_겹침수") or c.endswith("_소상공인365_겹침수")]
    area_cols = [c for c in base.columns if c.endswith("_겹침면적_m2")]
    for col in count_cols:
        base[col] = base[col].fillna(0).astype(int)
    for col in area_cols:
        base[col] = base[col].fillna(0).round(2)
    write_csv(base, SPATIAL_DIR / "상권_공간기본피처.csv")
    return base


def make_point_gdf(df: pd.DataFrame, lon_col: str, lat_col: str):
    gpd, Point = safe_read_geopandas()
    work = df.copy()
    work[lon_col] = to_num(work[lon_col])
    work[lat_col] = to_num(work[lat_col])
    work = work[work[lon_col].between(124, 132) & work[lat_col].between(33, 39)].copy()
    if work.empty:
        return gpd.GeoDataFrame(work, geometry=[], crs=4326)
    geometry = [Point(xy) for xy in zip(work[lon_col], work[lat_col])]
    return gpd.GeoDataFrame(work, geometry=geometry, crs=4326).to_crs(5181)


def spatial_join_points_to_trade(points, trade_gdf):
    if len(points) == 0:
        return points.drop(columns="geometry", errors="ignore")
    gpd, _ = safe_read_geopandas()
    trade = trade_gdf[["상권_코드", "상권_코드_명", "자치구_코드_명", "행정동_코드_명", "geometry"]]
    joined = gpd.sjoin(points, trade, how="left", predicate="within")
    return joined.drop(columns=["geometry", "index_right"], errors="ignore")


def aggregate_sbdc_poi(trade_gdf):
    path = pick_one_file(lambda n: "상가(상권)정보_서울" in n, "소상공인 상가 서울")
    usecols = ["상가업소번호", "상호명", "상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "시군구명", "행정동명", "경도", "위도"]
    detail_parts = []
    feature_parts = []
    for chunk in pd.read_csv(path, encoding=choose_encoding(path), usecols=usecols, chunksize=120_000, low_memory=False):
        pts = make_point_gdf(chunk, "경도", "위도")
        joined = spatial_join_points_to_trade(pts, trade_gdf)
        joined = joined[joined["상권_코드"].notna()].copy()
        if joined.empty:
            continue
        detail = (
            joined.groupby(["상권_코드", "상권업종대분류명", "상권업종중분류명"], dropna=False)
            .agg(상가업소_수=("상가업소번호", "nunique"))
            .reset_index()
        )
        detail_parts.append(detail)
        feat = (
            joined.groupby(["상권_코드", "상권업종대분류명"], dropna=False)
            .agg(상가업소_수=("상가업소번호", "nunique"))
            .reset_index()
        )
        feature_parts.append(feat)
    if detail_parts:
        detail_all = pd.concat(detail_parts, ignore_index=True)
        detail_all = (
            detail_all.groupby(["상권_코드", "상권업종대분류명", "상권업종중분류명"], dropna=False)["상가업소_수"]
            .sum()
            .reset_index()
        )
    else:
        detail_all = pd.DataFrame(columns=["상권_코드", "상권업종대분류명", "상권업종중분류명", "상가업소_수"])
    write_csv(detail_all, SPATIAL_DIR / "상권_소상공인POI_중분류집계.csv")

    if feature_parts:
        major = pd.concat(feature_parts, ignore_index=True)
        major = major.groupby(["상권_코드", "상권업종대분류명"], dropna=False)["상가업소_수"].sum().reset_index()
        wide = major.pivot(index="상권_코드", columns="상권업종대분류명", values="상가업소_수").fillna(0)
        wide.columns = [f"공간POI_{str(c)}_점포수" for c in wide.columns]
        features = wide.reset_index()
        total = major.groupby("상권_코드", as_index=False)["상가업소_수"].sum().rename(columns={"상가업소_수": "공간POI_총점포수"})
        features = total.merge(features, on="상권_코드", how="left")
    else:
        features = pd.DataFrame(columns=["상권_코드", "공간POI_총점포수"])
    write_csv(features, SPATIAL_DIR / "상권_소상공인POI_공간피처.csv")
    return features


def collect_facility_points():
    frames = []

    def add_csv(label: str, filename: str, lon: str, lat: str, keep_cols: list[str]):
        files = source_csv_files(lambda n: n == filename)
        for path in files[:1]:
            df = read_csv(path)
            cols = [c for c in keep_cols if c in df.columns] + [lon, lat]
            part = df[cols].copy()
            part["시설유형"] = label
            part = part.rename(columns={lon: "경도", lat: "위도"})
            frames.append(part)

    add_csv("문화공간", "서울시 문화공간 정보.csv", "경도", "위도", ["문화시설명", "주제분류", "자치구"])
    add_csv("문화행사", "서울시 문화행사 정보.csv", "경도(Y좌표)", "위도(X좌표)", ["공연/행사명", "분류", "자치구"])
    add_csv("공공와이파이", "서울시 공공와이파이 서비스 위치 정보.csv", "X좌표", "Y좌표", ["와이파이명", "자치구", "설치유형"])
    add_csv("공중화장실", "서울시 공중화장실 위치정보.csv", "x 좌표", "y 좌표", ["건물명", "구 명칭", "유형"])

    for path in source_csv_files(lambda n: n.startswith("S-DoT")):
        continue

    for path in sorted(DATA_DIR.glob("*.xlsx")):
        name = path.name
        try:
            if "유동인구 설치 위치정보" in name:
                df = pd.read_excel(path)
                part = df.rename(columns={"경도": "경도", "위도": "위도"}).copy()
                part["시설유형"] = "S-DoT유동센서"
                frames.append(part[["방문자 센서코드", "시리얼번호", "주소", "경도", "위도", "시설유형"]])
            elif "환경정보 설치 위치정보" in name:
                df = pd.read_excel(path)
                part = df.rename(columns={"모델 시리얼(*)": "시리얼번호"}).copy()
                part["시설유형"] = "S-DoT환경센서"
                frames.append(part[["시리얼번호", "주소", "경도", "위도", "시설유형"]])
            elif "주요 공원현황" in name:
                df = pd.read_excel(path)
                part = df.rename(columns={"X좌표(WGS84)": "경도", "Y좌표(WGS84)": "위도"}).copy()
                part["시설유형"] = "주요공원"
                frames.append(part[["공원명", "지역", "공원주소", "경도", "위도", "시설유형"]])
        except Exception:
            continue

    if not frames:
        return pd.DataFrame(columns=["시설유형", "경도", "위도"])
    return pd.concat(frames, ignore_index=True, sort=False)


def aggregate_facilities(trade_gdf):
    points_df = collect_facility_points()
    pts = make_point_gdf(points_df, "경도", "위도")
    joined = spatial_join_points_to_trade(pts, trade_gdf)
    write_csv(joined, SPATIAL_DIR / "상권_공간시설_조인상세.csv")
    if joined.empty:
        features = pd.DataFrame(columns=["상권_코드"])
    else:
        grouped = joined[joined["상권_코드"].notna()].groupby(["상권_코드", "시설유형"]).size().reset_index(name="시설수")
        wide = grouped.pivot(index="상권_코드", columns="시설유형", values="시설수").fillna(0)
        wide.columns = [f"공간시설_내부_{c}_수" for c in wide.columns]
        features = wide.reset_index()
        features["공간시설_총수"] = features[[c for c in features.columns if c.startswith("공간시설_")]].sum(axis=1)
    if len(pts):
        gpd, _ = safe_read_geopandas()
        point_base = pts.drop(columns=["index_right"], errors="ignore").copy()
        buffer_frames = []
        for radius in [100, 300, 500]:
            trade_buffer = trade_gdf[["상권_코드", "geometry"]].copy()
            trade_buffer["geometry"] = trade_buffer.geometry.buffer(radius)
            bj = gpd.sjoin(point_base, trade_buffer, how="inner", predicate="within").drop(columns=["index_right"], errors="ignore")
            if len(bj):
                bgroup = bj.groupby(["상권_코드", "시설유형"]).size().reset_index(name=f"{radius}m_시설수")
                bwide = bgroup.pivot(index="상권_코드", columns="시설유형", values=f"{radius}m_시설수").fillna(0)
                bwide.columns = [f"공간시설_{radius}m_{c}_수" for c in bwide.columns]
                buffer_frames.append(bwide.reset_index())
        for bf in buffer_frames:
            features = features.merge(bf, on="상권_코드", how="outer")
    write_csv(features, SPATIAL_DIR / "상권_공간시설_피처.csv")
    return features


def build_spatial_features() -> pd.DataFrame:
    build_geo_source_manifest()
    trade_gdf = read_trade_area_gdf()
    base = overlay_area_features(trade_gdf)
    poi = aggregate_sbdc_poi(trade_gdf)
    facilities = aggregate_facilities(trade_gdf)
    spatial = base.merge(poi, on="상권_코드", how="left").merge(facilities, on="상권_코드", how="left")
    count_cols = [c for c in spatial.columns if c.endswith("_수") or c.endswith("_점포수") or c.endswith("_겹침수")]
    for col in count_cols:
        spatial[col] = pd.to_numeric(spatial[col], errors="coerce").fillna(0).astype(int)
    write_csv(spatial, SPATIAL_DIR / "상권_공간통합피처.csv")
    return spatial


def mobility_source_files() -> tuple[list[Path], pd.DataFrame]:
    all_files = sorted(DATA_DIR.rglob("생활이동_자치구_2026*.csv"))
    selected: dict[str, Path] = {}
    for path in sorted(all_files, key=lambda p: (("(1)" in str(p)), len(p.parts), str(p))):
        selected.setdefault(path.name, path)
    include_set = set(selected.values())
    rows = []
    for path in all_files:
        month = re.search(r"(\d{4})\.(\d{2})", path.name)
        hour = re.search(r"_(\d{2})시", path.name)
        include = path in include_set
        rows.append(
            {
                "대상연월": f"{month.group(1)}{month.group(2)}" if month else "",
                "시간대": hour.group(1) if hour else "",
                "원천파일경로": str(path.relative_to(DATA_DIR)),
                "파일크기_byte": path.stat().st_size,
                "수정시각": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                "집계포함여부": include,
                "제외사유": "" if include else "같은 파일명의 중복 추출본",
                "원천_데이터행수": np.nan,
            }
        )
    audit = pd.DataFrame(rows).sort_values(["대상연월", "시간대", "원천파일경로"])
    return sorted(include_set), audit


def map_mobility_code(series: pd.Series, which: str) -> pd.DataFrame:
    code = clean_key(series)
    legal = code.map(lambda x: SEOUL_MOBILITY_CODE.get(x, (x if x in SEOUL_DISTRICTS else "", ""))[0])
    name = code.map(lambda x: SEOUL_MOBILITY_CODE.get(x, ("", SEOUL_LEGAL_CODE_TO_NAME.get(x, "")))[1])
    return pd.DataFrame({f"{which}_자치구_코드": legal, f"{which}_자치구_명": name, f"{which}_서울자치구_여부": name.ne("")})


def mobility_file_output_paths(path: Path) -> tuple[Path, Path]:
    stem = path.stem.replace(".", "").replace(" ", "_")
    od_dir = SPATIAL_DIR / "생활이동_파일별_OD집계"
    demo_dir = SPATIAL_DIR / "생활이동_파일별_도착성연령유형집계"
    od_dir.mkdir(parents=True, exist_ok=True)
    demo_dir.mkdir(parents=True, exist_ok=True)
    return od_dir / f"{stem}_OD.csv", demo_dir / f"{stem}_도착성연령유형.csv"


def aggregate_one_mobility_file(path: Path, usecols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    od_path, demo_path = mobility_file_output_paths(path)
    if od_path.exists() and demo_path.exists():
        od = pd.read_csv(od_path, encoding="utf-8-sig", low_memory=False)
        demo = pd.read_csv(demo_path, encoding="utf-8-sig", low_memory=False)
        row_count = int(od["원천_행수"].sum()) if "원천_행수" in od.columns else 0
        return od, demo, row_count

    df = pd.read_csv(path, encoding="cp949", usecols=usecols, low_memory=False)
    row_count = len(df)
    df["대상연월"] = clean_key(df["대상연월"])
    df["기준_년분기_코드"] = df["대상연월"].map(quarter_code_from_month).astype("Int64")
    df["시간대"] = clean_key(df["도착시간"]).str.zfill(2)
    df["출발_생활이동_시군구_코드"] = clean_key(df["출발 시군구 코드"])
    df["도착_생활이동_시군구_코드"] = clean_key(df["도착 시군구 코드"])
    df["이동인구_합계"] = to_num(df["이동인구(합)"]).fillna(0)
    df["평균_이동시간_분_raw"] = to_num(df["평균 이동 시간(분)"]).fillna(0)
    df["이동시간_가중합"] = df["평균_이동시간_분_raw"] * df["이동인구_합계"]
    df["원천_행수"] = 1
    from_map = map_mobility_code(df["출발_생활이동_시군구_코드"], "출발")
    to_map = map_mobility_code(df["도착_생활이동_시군구_코드"], "도착")
    df = pd.concat([df, from_map, to_map], axis=1)

    od = (
        df.groupby(
            [
                "대상연월",
                "기준_년분기_코드",
                "시간대",
                "출발_생활이동_시군구_코드",
                "도착_생활이동_시군구_코드",
                "출발_서울자치구_여부",
                "도착_서울자치구_여부",
                "출발_자치구_코드",
                "출발_자치구_명",
                "도착_자치구_코드",
                "도착_자치구_명",
            ],
            dropna=False,
        )
        .agg(이동인구_합계=("이동인구_합계", "sum"), 이동시간_가중합=("이동시간_가중합", "sum"), 원천_행수=("원천_행수", "sum"))
        .reset_index()
    )
    write_csv(od, od_path)

    df["연령대"] = (to_num(df["나이"]) // 10 * 10).astype("Int64").astype(str).str.replace("<NA>", "미상")
    df["주말여부"] = clean_key(df["요일"]).isin(["토", "일", "6", "7"])
    demo = (
        df[df["도착_서울자치구_여부"]]
        .groupby(["대상연월", "기준_년분기_코드", "시간대", "도착_자치구_코드", "도착_자치구_명", "성별", "연령대", "이동유형", "주말여부"], dropna=False)
        .agg(도착_이동인구_합계=("이동인구_합계", "sum"), 도착_이동시간_가중합=("이동시간_가중합", "sum"), 원천_행수=("원천_행수", "sum"))
        .reset_index()
    )
    write_csv(demo, demo_path)
    return od, demo, row_count


def aggregate_mobility() -> pd.DataFrame:
    files, audit = mobility_source_files()
    od_parts = []
    demo_parts = []
    row_count_by_name = {}

    usecols = ["대상연월", "요일", "도착시간", "출발 시군구 코드", "도착 시군구 코드", "성별", "나이", "이동유형", "평균 이동 시간(분)", "이동인구(합)"]
    for idx, path in enumerate(files, start=1):
        od, demo, row_count = aggregate_one_mobility_file(path, usecols)
        row_count_by_name[path.name] = row_count
        od_parts.append(od)
        demo_parts.append(demo)
        print(f"[생활이동] {idx:03d}/{len(files):03d} {path.name} rows={row_count:,}", flush=True)

    audit["원천_데이터행수"] = audit["원천파일경로"].map(lambda p: row_count_by_name.get(Path(p).name, np.nan))
    write_csv(audit, SPATIAL_DIR / "생활이동_집계_원천파일감사.csv")

    od_all = pd.concat(od_parts, ignore_index=True)
    od_all = (
        od_all.groupby(
            [
                "대상연월",
                "기준_년분기_코드",
                "시간대",
                "출발_생활이동_시군구_코드",
                "도착_생활이동_시군구_코드",
                "출발_서울자치구_여부",
                "도착_서울자치구_여부",
                "출발_자치구_코드",
                "출발_자치구_명",
                "도착_자치구_코드",
                "도착_자치구_명",
            ],
            dropna=False,
        )
        .agg(이동인구_합계=("이동인구_합계", "sum"), 이동시간_가중합=("이동시간_가중합", "sum"), 원천_행수=("원천_행수", "sum"))
        .reset_index()
    )
    od_all["평균_이동시간_분"] = np.where(od_all["이동인구_합계"] > 0, od_all["이동시간_가중합"] / od_all["이동인구_합계"], np.nan)
    od_all["평균_이동시간_분"] = od_all["평균_이동시간_분"].round(3)
    write_csv(od_all, SPATIAL_DIR / "생활이동_OD_월시간_집계.csv")

    demo_all = pd.concat(demo_parts, ignore_index=True)
    demo_all = (
        demo_all.groupby(["대상연월", "기준_년분기_코드", "시간대", "도착_자치구_코드", "도착_자치구_명", "성별", "연령대", "이동유형", "주말여부"], dropna=False)
        .agg(도착_이동인구_합계=("도착_이동인구_합계", "sum"), 도착_이동시간_가중합=("도착_이동시간_가중합", "sum"), 원천_행수=("원천_행수", "sum"))
        .reset_index()
    )
    demo_all["도착_평균_이동시간_분"] = np.where(demo_all["도착_이동인구_합계"] > 0, demo_all["도착_이동시간_가중합"] / demo_all["도착_이동인구_합계"], np.nan)
    write_csv(demo_all, SPATIAL_DIR / "생활이동_도착자치구_월시간_성연령유형_집계.csv")

    direction = build_mobility_direction_features(od_all)
    quarter = build_mobility_quarter_features(direction, demo_all)
    return quarter


def weighted_avg(sum_col: pd.Series, weight_col: pd.Series) -> float:
    total = weight_col.sum()
    return float(sum_col.sum() / total) if total else np.nan


def build_mobility_direction_features(od_all: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in od_all.iterrows():
        from_name = row["출발_자치구_명"]
        to_name = row["도착_자치구_명"]
        from_code = row["출발_자치구_코드"]
        to_code = row["도착_자치구_코드"]
        base = {
            "대상연월": row["대상연월"],
            "기준_년분기_코드": row["기준_년분기_코드"],
            "시간대": row["시간대"],
            "이동인구": row["이동인구_합계"],
            "이동시간_가중합": row["이동시간_가중합"],
        }
        if to_name:
            kind = "내부이동" if from_code == to_code else "유입"
            rows.append({**base, "자치구_코드": to_code, "자치구_명": to_name, "방향": kind, "서울내외": "서울내" if from_name else "외부"})
        if from_name and from_code != to_code:
            rows.append({**base, "자치구_코드": from_code, "자치구_명": from_name, "방향": "유출", "서울내외": "서울내" if to_name else "외부"})
    long = pd.DataFrame(rows)
    grouped = (
        long.groupby(["대상연월", "기준_년분기_코드", "시간대", "자치구_코드", "자치구_명", "방향", "서울내외"], dropna=False)
        .agg(이동인구=("이동인구", "sum"), 이동시간_가중합=("이동시간_가중합", "sum"))
        .reset_index()
    )
    pivot = grouped.pivot_table(index=["대상연월", "기준_년분기_코드", "시간대", "자치구_코드", "자치구_명"], columns=["방향", "서울내외"], values=["이동인구", "이동시간_가중합"], aggfunc="sum", fill_value=0)
    pivot.columns = ["_".join(map(str, c)).strip("_") for c in pivot.columns]
    out = pivot.reset_index()

    def col(name: str) -> pd.Series:
        return out[name] if name in out.columns else pd.Series(0, index=out.index)

    out["유입_이동인구_합계"] = col("이동인구_유입_서울내") + col("이동인구_유입_외부")
    out["유출_이동인구_합계"] = col("이동인구_유출_서울내") + col("이동인구_유출_외부")
    out["내부이동_이동인구_합계"] = col("이동인구_내부이동_서울내") + col("이동인구_내부이동_외부")
    out["서울내유입_이동인구_합계"] = col("이동인구_유입_서울내")
    out["외부유입_이동인구_합계"] = col("이동인구_유입_외부")
    out["서울내유출_이동인구_합계"] = col("이동인구_유출_서울내")
    out["외부유출_이동인구_합계"] = col("이동인구_유출_외부")
    out["총관련_이동인구_합계"] = out["유입_이동인구_합계"] + out["유출_이동인구_합계"] + out["내부이동_이동인구_합계"]
    out["순유입_이동인구"] = out["유입_이동인구_합계"] - out["유출_이동인구_합계"]
    out["유입유출_비율"] = np.where(out["유출_이동인구_합계"] > 0, out["유입_이동인구_합계"] / out["유출_이동인구_합계"], np.nan)

    for direction in ["유입", "유출", "내부이동"]:
        pop = out[f"{direction}_이동인구_합계"]
        if direction == "내부이동":
            tw = col("이동시간_가중합_내부이동_서울내") + col("이동시간_가중합_내부이동_외부")
        else:
            tw = col(f"이동시간_가중합_{direction}_서울내") + col(f"이동시간_가중합_{direction}_외부")
        out[f"{direction}_평균_이동시간_분"] = np.where(pop > 0, tw / pop, np.nan)

    keep = [
        "대상연월",
        "기준_년분기_코드",
        "시간대",
        "자치구_코드",
        "자치구_명",
        "유입_이동인구_합계",
        "유입_평균_이동시간_분",
        "유출_이동인구_합계",
        "유출_평균_이동시간_분",
        "내부이동_이동인구_합계",
        "내부이동_평균_이동시간_분",
        "서울내유입_이동인구_합계",
        "외부유입_이동인구_합계",
        "서울내유출_이동인구_합계",
        "외부유출_이동인구_합계",
        "총관련_이동인구_합계",
        "순유입_이동인구",
        "유입유출_비율",
    ]
    out = out[keep].copy()
    write_csv(out, SPATIAL_DIR / "생활이동_자치구_월시간_방향집계.csv")
    return out


def build_mobility_quarter_features(direction: pd.DataFrame, demo_all: pd.DataFrame) -> pd.DataFrame:
    direction = direction.copy()
    direction["시간대_int"] = pd.to_numeric(direction["시간대"], errors="coerce")
    direction["출근시간"] = direction["시간대_int"].between(7, 10)
    direction["점심시간"] = direction["시간대_int"].between(11, 14)
    direction["퇴근시간"] = direction["시간대_int"].between(17, 20)
    direction["야간"] = direction["시간대_int"].isin([21, 22, 23, 0, 1, 2, 3, 4, 5])
    gcols = ["기준_년분기_코드", "자치구_코드", "자치구_명"]
    grouped = direction.groupby(gcols, dropna=False)
    q = grouped.agg(
        생활이동_유입_이동인구_합계=("유입_이동인구_합계", "sum"),
        생활이동_유출_이동인구_합계=("유출_이동인구_합계", "sum"),
        생활이동_내부이동_이동인구_합계=("내부이동_이동인구_합계", "sum"),
        생활이동_총관련_이동인구_합계=("총관련_이동인구_합계", "sum"),
        생활이동_순유입_이동인구=("순유입_이동인구", "sum"),
        생활이동_외부유입_이동인구_합계=("외부유입_이동인구_합계", "sum"),
        생활이동_외부유출_이동인구_합계=("외부유출_이동인구_합계", "sum"),
        생활이동_분기_포함월수=("대상연월", "nunique"),
    ).reset_index()
    q["생활이동_유입유출_비율"] = np.where(q["생활이동_유출_이동인구_합계"] > 0, q["생활이동_유입_이동인구_합계"] / q["생활이동_유출_이동인구_합계"], np.nan)

    for direction_name in ["유입", "유출", "내부이동"]:
        pop_col = f"{direction_name}_이동인구_합계"
        time_col = f"{direction_name}_평균_이동시간_분"
        temp = direction.copy()
        temp["시간가중합"] = temp[pop_col] * temp[time_col]
        agg = temp.groupby(gcols).agg(시간가중합=("시간가중합", "sum"), 인구=(pop_col, "sum")).reset_index()
        agg[f"생활이동_{direction_name}_평균_이동시간_분"] = np.where(agg["인구"] > 0, agg["시간가중합"] / agg["인구"], np.nan)
        q = q.merge(agg[gcols + [f"생활이동_{direction_name}_평균_이동시간_분"]], on=gcols, how="left")

    for label, mask_col in [("출근시간", "출근시간"), ("점심시간", "점심시간"), ("퇴근시간", "퇴근시간"), ("야간", "야간")]:
        temp = direction[direction[mask_col]].groupby(gcols, dropna=False)["유입_이동인구_합계"].sum().reset_index(name=f"생활이동_{label}_유입_이동인구")
        q = q.merge(temp, on=gcols, how="left")

    demo = demo_all.copy()
    demo_gender = demo.groupby(["기준_년분기_코드", "도착_자치구_코드", "도착_자치구_명", "성별"], dropna=False)["도착_이동인구_합계"].sum().reset_index()
    gender_total = demo_gender.groupby(["기준_년분기_코드", "도착_자치구_코드", "도착_자치구_명"], dropna=False)["도착_이동인구_합계"].sum().reset_index(name="도착합계")
    demo_gender = demo_gender.merge(gender_total, on=["기준_년분기_코드", "도착_자치구_코드", "도착_자치구_명"], how="left")
    female = demo_gender[demo_gender["성별"].astype(str).str.contains("F|여", case=False, regex=True)].copy()
    female = female.groupby(["기준_년분기_코드", "도착_자치구_코드", "도착_자치구_명"]).agg(여성=("도착_이동인구_합계", "sum"), 합계=("도착합계", "max")).reset_index()
    female["생활이동_도착여성비율"] = np.where(female["합계"] > 0, female["여성"] / female["합계"], np.nan)
    female = female.rename(columns={"도착_자치구_코드": "자치구_코드", "도착_자치구_명": "자치구_명"})
    q = q.merge(female[["기준_년분기_코드", "자치구_코드", "자치구_명", "생활이동_도착여성비율"]], on=gcols, how="left")

    q = q.fillna({c: 0 for c in q.columns if c.startswith("생활이동_") and c not in ["생활이동_도착여성비율"]})
    write_csv(q, SPATIAL_DIR / "생활이동_자치구_분기피처.csv")
    return q


def aggregate_real_estate() -> pd.DataFrame:
    path = DATA_DIR / "국토교통부_상업업무용_실거래_서울_202501_202605.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    df["거래금액_만원"] = to_num(df["dealAmount"])
    df["전용면적_m2"] = to_num(df.get("buildingAr", pd.Series(index=df.index)))
    df["기준_년분기_코드"] = df["DEAL_YMD"].map(quarter_code_from_month).astype("Int64")
    df["자치구_코드"] = clean_key(df["LAWD_CD"])
    df["자치구_코드_명"] = df["sggNm"].astype(str).str.strip()
    out = (
        df.groupby(["기준_년분기_코드", "자치구_코드", "자치구_코드_명"], dropna=False)
        .agg(
            실거래_상업업무_거래건수=("거래금액_만원", "count"),
            실거래_상업업무_거래금액_만원_합계=("거래금액_만원", "sum"),
            실거래_상업업무_거래금액_만원_평균=("거래금액_만원", "mean"),
            실거래_상업업무_전용면적_m2_평균=("전용면적_m2", "mean"),
        )
        .reset_index()
    )
    write_csv(out, SPATIAL_DIR / "자치구_상업업무_실거래_분기피처.csv")
    return out


def aggregate_life_population() -> tuple[pd.DataFrame, pd.DataFrame]:
    local = pd.read_csv(PROCESSED_DIR / "행정동_시간대별_생활인구요약.csv", encoding="utf-8-sig", dtype=str)
    local["기준_년분기_코드"] = local["기준월"].map(quarter_code_from_month).astype("Int64")
    local["총생활인구수_합계"] = to_num(local["총생활인구수_합계"])
    local["총생활인구수_평균"] = to_num(local["총생활인구수_평균"])
    local_q = (
        local.groupby(["기준_년분기_코드", "행정동코드"], dropna=False)
        .agg(
            행정동생활인구_총합=("총생활인구수_합계", "sum"),
            행정동생활인구_시간평균=("총생활인구수_평균", "mean"),
            행정동생활인구_관측시간수=("시간대구분", "nunique"),
            행정동생활인구_포함월수=("기준월", "nunique"),
        )
        .reset_index()
    )
    local_q["행정동코드"] = clean_key(local_q["행정동코드"])
    write_csv(local_q, SPATIAL_DIR / "행정동_생활인구_분기피처.csv")

    grid = pd.read_csv(PROCESSED_DIR / "행정동_일자시간_250m생활인구요약.csv", encoding="utf-8-sig", dtype=str)
    grid["기준_년분기_코드"] = grid["일자"].map(quarter_code_from_month).astype("Int64")
    grid["생활인구합계"] = to_num(grid["생활인구합계"])
    grid["격자수"] = to_num(grid["격자수"])
    grid_q = (
        grid.groupby(["기준_년분기_코드", "행정동코드"], dropna=False)
        .agg(
            생활인구250m_총합=("생활인구합계", "sum"),
            생활인구250m_평균격자수=("격자수", "mean"),
            생활인구250m_관측일수=("일자", "nunique"),
        )
        .reset_index()
    )
    grid_q["행정동코드"] = clean_key(grid_q["행정동코드"])
    write_csv(grid_q, SPATIAL_DIR / "행정동_250m생활인구_분기피처.csv")
    return local_q, grid_q


# === [Claude(Opus 4.8) 검증봇 수정 / 2026-06-26] S-DoT 영문→한글 자치구 매핑 재결합 ===
# 근거: 원천 S-DoT(보행/환경)의 자치구·행정동 키가 영문 로마자(예: "Jung-gu", "Myeong-dong")인데
#       상권 기준표는 한글(예: "중구", "명동")이라, 기존 행정동명 조인(자치구_코드_명+행정동_코드_명)이
#       0건 매칭되어 SDOT보행/환경 12개 컬럼이 100% 결측이었다(2차 검수 보고서 3.2절 참조).
# 결정: 행정동 영문↔한글 크로스워크가 코퍼스에 없어 행정동 단위 자동매핑은 미매칭/오매칭 위험이 큼.
#       반면 서울 25개 자치구 표준 로마자 매핑은 100% 신뢰 가능하고, S-DoT는 본래 "센서 관측 보조지표"이며
#       기존 자치구_생활시설_보조지표와 동일 입도다. 따라서 자치구 단위로 가중집계해 재결합한다(행정동 키 미사용).
SDOT_GU_EN_TO_KO = {
    "Jongno-gu": "종로구", "Jung-gu": "중구", "Yongsan-gu": "용산구",
    "Seongdong-gu": "성동구", "Gwangjin-gu": "광진구", "Dongdaemun-gu": "동대문구",
    "Jungnang-gu": "중랑구", "Seongbuk-gu": "성북구", "Gangbuk-gu": "강북구",
    "Dobong-gu": "도봉구", "Nowon-gu": "노원구", "Eunpyeong-gu": "은평구",
    "Seodaemun-gu": "서대문구", "Mapo-gu": "마포구", "Yangcheon-gu": "양천구",
    "Gangseo-gu": "강서구", "Guro-gu": "구로구", "Geumcheon-gu": "금천구",
    "Yeongdeungpo-gu": "영등포구", "Dongjak-gu": "동작구", "Gwanak-gu": "관악구",
    "Seocho-gu": "서초구", "Gangnam-gu": "강남구", "Songpa-gu": "송파구",
    "Gangdong-gu": "강동구",
}


def aggregate_sdot_and_facility_aux() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    walk = pd.read_csv(PROCESSED_DIR / "SDOT_자치구_행정동_보행요약.csv", encoding="utf-8-sig")
    env = pd.read_csv(PROCESSED_DIR / "SDOT_자치구_행정동_환경요약.csv", encoding="utf-8-sig")
    fac = pd.read_csv(PROCESSED_DIR / "자치구_생활시설_보조지표.csv", encoding="utf-8-sig")
    for df in [walk, env]:
        df["자치구"] = df["자치구"].astype(str).str.strip()
        df["행정동"] = df["행정동"].astype(str).str.strip()
    fac["자치구"] = fac["자치구"].astype(str).str.strip()

    # 영문 자치구 → 한글 자치구 매핑(미지의 값은 NaN 처리 후 제외하여 오매칭 방지)
    walk["자치구"] = walk["자치구"].map(SDOT_GU_EN_TO_KO)
    env["자치구"] = env["자치구"].map(SDOT_GU_EN_TO_KO)
    walk = walk[walk["자치구"].notna()]
    env = env[env["자치구"].notna()]

    # 보행: 자치구 단위 합계 + 관측수 가중 방문자수 평균
    walk["관측수"] = pd.to_numeric(walk["관측수"], errors="coerce")
    walk["방문자수_합계"] = pd.to_numeric(walk["방문자수_합계"], errors="coerce")
    walk_g = walk.groupby("자치구", as_index=False).agg(
        관측수=("관측수", "sum"),
        방문자수_합계=("방문자수_합계", "sum"),
    )
    walk_g["방문자수_평균"] = (walk_g["방문자수_합계"] / walk_g["관측수"].replace(0, np.nan)).round(2)

    # 환경: 자치구 단위 관측수 합계 + 관측수 가중평균(온도/습도/소음/오존)
    env_num = ["온도_평균", "습도_평균", "소음_평균", "오존_평균"]
    env["관측수"] = pd.to_numeric(env["관측수"], errors="coerce")
    for c in env_num:
        env[c] = pd.to_numeric(env[c], errors="coerce")
        env[c + "_w"] = env[c] * env["관측수"]
    agg_map: dict[str, tuple[str, str]] = {"관측수": ("관측수", "sum")}
    for c in env_num:
        agg_map[c + "_w"] = (c + "_w", "sum")
    env_g = env.groupby("자치구", as_index=False).agg(**agg_map)
    for c in env_num:
        env_g[c] = (env_g[c + "_w"] / env_g["관측수"].replace(0, np.nan)).round(3)
    env_g = env_g[["자치구", "관측수"] + env_num]

    return walk_g, env_g, fac


def normalize_industry_name(value: object) -> str:
    text = str(value)
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    for token in ["점", "업", "서비스", "전문", "소매", "음식", "음식점"]:
        text = text.replace(token, "")
    return text


def industry_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    lnorm = normalize_industry_name(left)
    rnorm = normalize_industry_name(right)
    if not lnorm or not rnorm:
        return 0.0
    ratio = SequenceMatcher(None, lnorm, rnorm).ratio()
    contains = 1.0 if lnorm in rnorm or rnorm in lnorm else 0.0
    left_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", lnorm))
    right_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", rnorm))
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return round(max(ratio, contains, overlap), 4)


def read_sbdc_industry_table() -> pd.DataFrame:
    files = sorted(DATA_DIR.rglob("*업종분류*연계표*.xlsx"), key=lambda p: (("(1)" in str(p)), len(p.parts), str(p)))
    if not files:
        return pd.DataFrame()
    raw = pd.read_excel(files[0], sheet_name=0, header=1)
    keep = ["대분류코드", "대분류명", "중분류코드", "중분류명", "소분류코드", "소분류명"]
    raw = raw[[c for c in keep if c in raw.columns]].dropna(how="all").copy()
    if "소분류코드" not in raw.columns:
        return pd.DataFrame()
    raw = raw[raw["소분류코드"].notna()].copy()
    for col in keep:
        if col in raw.columns:
            raw[col] = raw[col].astype(str).str.strip()
    write_csv(raw, SPATIAL_DIR / "SBDC_업종분류표_247.csv")
    return raw


def build_industry_code_mapping() -> pd.DataFrame:
    sales = pd.read_csv(PROCESSED_DIR / "상권_업종_분기별_매출요약.csv", encoding="utf-8-sig", usecols=["서비스_업종_코드", "서비스_업종_코드_명"], dtype=str)
    seoul = sales.drop_duplicates().sort_values(["서비스_업종_코드", "서비스_업종_코드_명"]).reset_index(drop=True)
    sbdc = read_sbdc_industry_table()
    rows = []
    if sbdc.empty:
        seoul["업종매핑_검토상태"] = "SBDC 업종분류표 없음"
        write_csv(seoul, SPATIAL_DIR / "업종코드_서울_SBDC_매핑검증.csv")
        return seoul
    candidates = sbdc.to_dict("records")
    for _, srow in seoul.iterrows():
        name = srow["서비스_업종_코드_명"]
        scored = []
        for crow in candidates:
            text = f"{crow.get('대분류명', '')} {crow.get('중분류명', '')} {crow.get('소분류명', '')}"
            scored.append((industry_similarity(name, text), crow))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        status = "자동매칭_강함" if best_score >= 0.82 else "자동매칭_검토필요" if best_score >= 0.55 else "수동매핑필요"
        rows.append(
            {
                "서비스_업종_코드": srow["서비스_업종_코드"],
                "서비스_업종_코드_명": name,
                "SBDC_대분류코드_후보": best.get("대분류코드", ""),
                "SBDC_대분류명_후보": best.get("대분류명", ""),
                "SBDC_중분류코드_후보": best.get("중분류코드", ""),
                "SBDC_중분류명_후보": best.get("중분류명", ""),
                "SBDC_소분류코드_후보": best.get("소분류코드", ""),
                "SBDC_소분류명_후보": best.get("소분류명", ""),
                "업종매핑_유사도": best_score,
                "업종매핑_검토상태": status,
            }
        )
    mapping = pd.DataFrame(rows)
    write_csv(mapping, SPATIAL_DIR / "업종코드_서울_SBDC_매핑검증.csv")
    return mapping


def make_model_base() -> pd.DataFrame:
    sales = pd.read_csv(PROCESSED_DIR / "상권_업종_분기별_매출요약.csv", encoding="utf-8-sig", low_memory=False)
    stores = pd.read_csv(PROCESSED_DIR / "상권_업종_분기별_점포요약.csv", encoding="utf-8-sig", low_memory=False)
    area_tables = [
        "상권_분기별_유동인구요약.csv",
        "상권_분기별_상주인구요약.csv",
        "상권_분기별_직장인구요약.csv",
        "상권_분기별_소비요약.csv",
        "상권_분기별_아파트요약.csv",
        "상권_분기별_집객시설요약.csv",
        "상권_분기별_변화지표요약.csv",
    ]
    for df in [sales, stores]:
        df["기준_년분기_코드"] = pd.to_numeric(df["기준_년분기_코드"], errors="coerce").astype("Int64")
        df["상권_코드"] = clean_key(df["상권_코드"])
        df["서비스_업종_코드"] = clean_key(df["서비스_업종_코드"])
    base = sales.merge(stores, on=["기준_년분기_코드", "상권_코드", "서비스_업종_코드", "상권_코드_명", "서비스_업종_코드_명"], how="left")
    for filename in area_tables:
        df = pd.read_csv(PROCESSED_DIR / filename, encoding="utf-8-sig", low_memory=False)
        df["기준_년분기_코드"] = pd.to_numeric(df["기준_년분기_코드"], errors="coerce").astype("Int64")
        df["상권_코드"] = clean_key(df["상권_코드"])
        drop_names = [c for c in ["상권_코드_명"] if c in df.columns]
        base = base.merge(df.drop(columns=drop_names), on=["기준_년분기_코드", "상권_코드"], how="left")
    area = load_area_master()
    base = base.merge(area, on=["상권_코드", "상권_코드_명"], how="left")
    industry = build_industry_code_mapping()
    base = base.merge(industry, on=["서비스_업종_코드", "서비스_업종_코드_명"], how="left")
    base = base.sort_values(["상권_코드", "서비스_업종_코드", "기준_년분기_코드"]).copy()
    base["다음분기_매출"] = base.groupby(["상권_코드", "서비스_업종_코드"])["당월_매출_금액"].shift(-1)
    base["이전분기_매출"] = base.groupby(["상권_코드", "서비스_업종_코드"])["당월_매출_금액"].shift(1)
    base["매출_전분기_증감률"] = (base["당월_매출_금액"] - base["이전분기_매출"]) / base["이전분기_매출"].replace(0, np.nan)
    base["점포당_매출"] = base["당월_매출_금액"] / base["점포_수"].replace(0, np.nan)
    base["유동인구당_매출"] = base["당월_매출_금액"] / base["총_유동인구_수"].replace(0, np.nan)
    base["상주인구당_소비"] = base["지출_총금액"] / base["총_상주인구_수"].replace(0, np.nan)
    base["라벨_log_다음분기매출"] = np.log1p(base["다음분기_매출"])
    return base


def build_final_feature_mart(spatial: pd.DataFrame, mobility_q: pd.DataFrame) -> pd.DataFrame:
    base = make_model_base()
    real = aggregate_real_estate()
    local_q, grid_q = aggregate_life_population()
    sdot_walk, sdot_env, district_fac = aggregate_sdot_and_facility_aux()

    base_overlap_cols = {
        "상권_코드_명",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
        "엑스좌표_값",
        "와이좌표_값",
        "영역_면적",
    }
    spatial_cols = ["상권_코드"] + [c for c in spatial.columns if c not in base_overlap_cols and c != "상권_코드"]
    final = base.merge(spatial[spatial_cols], on="상권_코드", how="left", suffixes=("", "_공간"))
    final = final.merge(local_q, left_on=["기준_년분기_코드", "행정동_코드"], right_on=["기준_년분기_코드", "행정동코드"], how="left").drop(columns=["행정동코드"], errors="ignore")
    final = final.merge(grid_q, left_on=["기준_년분기_코드", "행정동_코드"], right_on=["기준_년분기_코드", "행정동코드"], how="left").drop(columns=["행정동코드"], errors="ignore")
    # [Claude 검증봇 수정 2026-06-26] S-DoT는 자치구 단위 가중집계로 변경되어 자치구명으로만 조인한다.
    final = final.merge(sdot_walk.add_prefix("SDOT보행_"), left_on="자치구_코드_명", right_on="SDOT보행_자치구", how="left")
    final = final.merge(sdot_env.add_prefix("SDOT환경_"), left_on="자치구_코드_명", right_on="SDOT환경_자치구", how="left")
    final = final.merge(district_fac.add_prefix("자치구시설_"), left_on="자치구_코드_명", right_on="자치구시설_자치구", how="left")
    final = final.merge(real, on=["기준_년분기_코드", "자치구_코드", "자치구_코드_명"], how="left")
    final = final.merge(mobility_q, left_on=["기준_년분기_코드", "자치구_코드", "자치구_코드_명"], right_on=["기준_년분기_코드", "자치구_코드", "자치구_명"], how="left")

    numeric_cols = final.select_dtypes(include=[np.number]).columns
    final[numeric_cols] = final[numeric_cols].replace([np.inf, -np.inf], np.nan)
    final["ReportFacts_공간조인키"] = final["상권_코드"].astype(str)
    final["ReportFacts_행정동조인키"] = final["행정동_코드"].astype(str)
    final["ReportFacts_자치구조인키"] = final["자치구_코드"].astype(str)
    final["데이터_기준기간_텍스트"] = final["기준_년분기_코드"].map(month_text_from_quarter)
    write_csv(final, MODEL_READY_DIR / "서울상권_최종공간OD_FeatureMart.csv")
    try:
        final.to_parquet(MODEL_READY_DIR / "서울상권_최종공간OD_FeatureMart.parquet", index=False)
    except Exception:
        pass

    schema = pd.DataFrame(
        [
            {
                "컬럼명": col,
                "dtype": str(final[col].dtype),
                "결측수": int(final[col].isna().sum()),
                "비결측수": int(final[col].notna().sum()),
                "예시값": "" if final[col].dropna().empty else str(final[col].dropna().iloc[0])[:120],
            }
            for col in final.columns
        ]
    )
    write_csv(schema, MODEL_READY_DIR / "서울상권_최종공간OD_FeatureMart_스키마.csv")
    return final


def build_reportfacts(final: pd.DataFrame) -> None:
    latest = int(final["기준_년분기_코드"].dropna().max())
    latest_df = final[final["기준_년분기_코드"] == latest].copy()
    sample = latest_df.sort_values("당월_매출_금액", ascending=False).head(30)
    fact_rows = []
    for _, row in sample.iterrows():
        rid = f"RF-{int(row['기준_년분기_코드'])}-{row['상권_코드']}-{row['서비스_업종_코드']}"
        fact_rows.append(
            {
                "schema_version": "reportfacts.v2.spatial_od",
                "report_id": rid,
                "request": {
                    "business_type_label": row.get("서비스_업종_코드_명"),
                    "business_code": row.get("서비스_업종_코드"),
                    "location": {
                        "trdar_cd": row.get("상권_코드"),
                        "trdar_name": row.get("상권_코드_명"),
                        "district": row.get("자치구_코드_명"),
                        "dong": row.get("행정동_코드_명"),
                        "centroid_lng": safe_float(row.get("상권_중심경도")),
                        "centroid_lat": safe_float(row.get("상권_중심위도")),
                        "spatial_match_method": "상권 SHP 기준 point-in-polygon 또는 상권코드 직접 매칭",
                    },
                    "period": str(row.get("기준_년분기_코드")),
                },
                "facts": {
                    "sales": {
                        "fact_id": f"{rid}.sales",
                        "amount_krw": safe_float(row.get("당월_매출_금액")),
                        "count": safe_float(row.get("당월_매출_건수")),
                        "avg_ticket_krw": safe_float(row.get("평균_객단가")),
                        "next_quarter_label_krw": safe_float(row.get("다음분기_매출")),
                    },
                    "competition": {
                        "fact_id": f"{rid}.competition",
                        "store_count": safe_float(row.get("점포_수")),
                        "franchise_count": safe_float(row.get("프랜차이즈_점포_수")),
                        "spatial_poi_total": safe_float(row.get("공간POI_총점포수")),
                    },
                    "demand": {
                        "fact_id": f"{rid}.demand",
                        "floating_population": safe_float(row.get("총_유동인구_수")),
                        "resident_population": safe_float(row.get("총_상주인구_수")),
                        "worker_population": safe_float(row.get("총_직장_인구_수")),
                        "dong_life_population_avg": safe_float(row.get("행정동생활인구_시간평균")),
                    },
                    "mobility_od": {
                        "fact_id": f"{rid}.mobility_od",
                        "inflow": safe_float(row.get("생활이동_유입_이동인구_합계")),
                        "outflow": safe_float(row.get("생활이동_유출_이동인구_합계")),
                        "internal": safe_float(row.get("생활이동_내부이동_이동인구_합계")),
                        "net_inflow": safe_float(row.get("생활이동_순유입_이동인구")),
                        "included_months": safe_float(row.get("생활이동_분기_포함월수")),
                    },
                    "spatial_context": {
                        "fact_id": f"{rid}.spatial_context",
                        "trade_area_m2": safe_float(row.get("상권_폴리곤_면적_m2")),
                        "major_place": row.get("대표_주요장소명"),
                        "facility_total": safe_float(row.get("공간시설_총수")),
                    },
                    "real_estate": {
                        "fact_id": f"{rid}.real_estate",
                        "nrg_trade_count": safe_float(row.get("실거래_상업업무_거래건수")),
                        "nrg_trade_avg_10k_krw": safe_float(row.get("실거래_상업업무_거래금액_만원_평균")),
                    },
                },
                "data_quality": [
                    {
                        "source": "서울 상권분석서비스 파일/SHP",
                        "spatial_unit": "상권",
                        "proxy_level": "추정값/집계값",
                        "join_key": "상권_코드",
                    },
                    {
                        "source": "서울 생활이동 자치구 원본 CSV",
                        "spatial_unit": "자치구 OD",
                        "proxy_level": "집계 이동량",
                        "join_key": "기준_년분기_코드 + 자치구_코드",
                    },
                    {
                        "source": "국토교통부 상업업무용 실거래 API 수집본",
                        "spatial_unit": "자치구",
                        "proxy_level": "상업용 매매 거래 프록시",
                        "join_key": "기준_년분기_코드 + 자치구_코드",
                    },
                ],
                "required_warnings": [
                    "서울 추정매출은 개별 매장 실제 매출이 아니다.",
                    "생활이동은 자치구 단위 집계 이동량이며 개별 이동경로가 아니다.",
                    "실거래는 매매 거래 프록시이며 월세나 권리금이 아니다.",
                    "상권 판단은 공공 집계 데이터 기반 참고자료이며 성공을 보장하지 않는다.",
                ],
                "output_contract": {
                    "format": "markdown",
                    "must_cite_fact_ids": True,
                    "forbidden_claims": ["성공 보장", "개별 매장 매출 단정", "출처 없는 숫자 생성"],
                },
            }
        )
    write_json(fact_rows, REPORTFACTS_DIR / "서울상권_ReportFacts_최신분기_샘플.json")

    compact_cols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "자치구_코드_명",
        "행정동_코드_명",
        "당월_매출_금액",
        "점포_수",
        "총_유동인구_수",
        "공간POI_총점포수",
        "공간시설_총수",
        "생활이동_유입_이동인구_합계",
        "생활이동_유출_이동인구_합계",
        "생활이동_순유입_이동인구",
        "실거래_상업업무_거래건수",
        "다음분기_매출",
    ]
    compact_cols = [c for c in compact_cols if c in final.columns]
    write_csv(latest_df[compact_cols], REPORTFACTS_DIR / "서울상권_ReportFacts_최신분기_상권업종.csv")
    schema = {
        "schema_version": "reportfacts.v2.spatial_od",
        "grain": "기준_년분기_코드 + 상권_코드 + 서비스_업종_코드",
        "text_model_role": "검증된 숫자와 출처를 한국어 Markdown 상세리포트로 바꾸는 작성자",
        "must_not_do": ["숫자 생성", "출처 생성", "개별 매장 매출 단정", "성공 보장"],
        "required_sections": [
            "분석 대상과 기준일",
            "데이터 커버리지와 신뢰도",
            "상권/배후지 정의",
            "수요와 유동인구",
            "매출 벤치마크",
            "경쟁/보완 업종",
            "접근성/교통/체류",
            "임대료/예산/손익분기",
            "폐업/변화/규제 리스크",
            "추천 전략과 현장 확인 체크리스트",
            "방법론과 한계",
        ],
    }
    write_json(schema, REPORTFACTS_DIR / "서울상권_ReportFacts_스키마.json")


def safe_float(value):
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def build_manifest(final: pd.DataFrame) -> None:
    outputs = []
    for path in sorted(FINAL_DIR.rglob("*")):
        if path.is_file():
            item = {
                "path": str(path.relative_to(ROOT)),
                "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
            }
            if path.suffix.lower() == ".csv":
                try:
                    item["rows"] = int(sum(1 for _ in path.open("r", encoding="utf-8-sig")) - 1)
                except Exception:
                    pass
            outputs.append(item)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "objective": "공간데이터와 생활이동 OD를 포함한 서울 상권 상세리포트 모델 직전 산출물",
        "feature_mart_rows": int(len(final)),
        "feature_mart_columns": int(final.shape[1]),
        "latest_quarter": int(final["기준_년분기_코드"].dropna().max()),
        "outputs": outputs,
    }
    write_json(manifest, FINAL_DIR / "최종공간OD_산출물_매니페스트.json")


def main() -> None:
    ensure_dirs()
    print("[1/5] 공간 SHP/POI/시설 조인 시작")
    spatial = build_spatial_features()
    print(f"[1/5] 공간 피처 완료: {len(spatial):,}개 상권")

    print("[2/5] 생활이동 OD 전체 집계 시작")
    mobility_q = aggregate_mobility()
    print(f"[2/5] 생활이동 분기 피처 완료: {len(mobility_q):,}행")

    print("[3/5] 최종 Feature Mart 생성")
    final = build_final_feature_mart(spatial, mobility_q)
    print(f"[3/5] Feature Mart 완료: {len(final):,}행 x {final.shape[1]:,}열")

    print("[4/5] ReportFacts 생성")
    build_reportfacts(final)
    print("[4/5] ReportFacts 완료")

    print("[5/5] 매니페스트 생성")
    build_manifest(final)
    print("[5/5] 완료")


if __name__ == "__main__":
    main()
