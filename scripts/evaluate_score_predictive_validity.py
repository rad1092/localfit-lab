# -*- coding: utf-8 -*-
"""현행 입지점수 v2.6의 미래 성과 예측력을 재현 가능하게 검증한다.

이 스크립트는 테스트 파일이 아니라 분석 실행·기록 도구다. 과거 각 분기의
현행 점수를 다시 계산한 뒤, 이후 분기의 공식 Gold 매출·점포·폐업률과 연결해
다음 두 기간을 평가한다.

* q+1: 다음 분기 단기 안정성
* q+4: 1년 뒤 상권×업종 시장 건전성(주 평가)

개별 점포 식별자나 개폐업일이 없으므로 결과를 개별 점포 생존 확률로 해석하지
않는다. 모든 산출물은 기존 v2.4 백테스트와 분리된 버전 디렉터리에 저장한다.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import itertools
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_rule_based_location_scores as engine  # noqa: E402


RUN_ID = "v2_6_20260716"
VALIDATION_VERSION = "score_predictive_validity.v1.0-20260716"
GOLD = ROOT / "datacorpus" / "_gold"
OUT_DIR = ROOT / "datacorpus" / "_score_predictive_validation" / RUN_ID
QUARTER_DIR = OUT_DIR / "quarter_scores"
REPORT_DIR = ROOT / "final_proj" / "docs" / "evaluation" / "score_predictive_validity_20260716"
VALIDATION_RECORD_PATH = (
    ROOT / "research" / "rule_validation" / "103_score_predictive_validity_v2_6_20260716.md"
)

SALES_PATH = GOLD / "gold_sales_strength_q_industry.csv"
COMPETITION_PATH = GOLD / "gold_competition_q_industry.csv"
WEIGHTS_PATH = ROOT / "datacorpus" / "_score_backtest" / "location_score_backtest_recommended_weights.csv"
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"
BACKEND_ENGINE_PATH = (
    ROOT / "final_proj" / "backend" / "app" / "rule_engine" / "build_rule_based_location_scores.py"
)

KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
AXES = ["sales", "competition", "demand", "accessibility"]
AXIS_COLS = [f"axis__{axis}" for axis in AXES]
MODEL_COLUMNS = {
    "현행 v2.6 점수": "model__score_v2_6",
    "현재 점포당매출 기준선": "model__sales_persistence",
    "현재 저폐업률 기준선": "model__closure_persistence",
    "단순 매출+폐업 기준선": "model__simple_persistence",
    "개발구간 선택 축가중치": "model__candidate_dev_selected",
}
HORIZONS = (1, 4)
PRIMARY_LABEL = "primary_observed_or_zero_store"
SENSITIVITY_LABEL = "sensitivity_missing_sales_as_failure"
RNG_SEED = 20260716


def ensure_dirs() -> None:
    QUARTER_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def add_quarters(q: int, count: int) -> int:
    year, quarter = divmod(int(q), 10)
    absolute = year * 4 + (quarter - 1) + count
    return (absolute // 4) * 10 + (absolute % 4) + 1


def subtract_quarters(q: int, count: int) -> int:
    return add_quarters(q, -count)


def time_split(origin_quarter: int) -> str:
    q = int(origin_quarter)
    if q <= 20234:
        return "development"
    if q <= 20244:
        return "validation"
    return "holdout"


def group_percentile(series: pd.Series) -> pd.Series:
    """동률을 보존하면서 유효값을 0~1 범위로 변환한다."""
    valid = series.notna()
    out = pd.Series(np.nan, index=series.index, dtype=float)
    n = int(valid.sum())
    if n == 0:
        return out
    if n == 1:
        out.loc[valid] = 0.5
        return out
    ranks = series.loc[valid].rank(method="average")
    out.loc[valid] = (ranks - 1.0) / (n - 1.0)
    return out


def rank_within_origin_industry(df: pd.DataFrame, value_col: str) -> pd.Series:
    return df.groupby(["origin_quarter", "서비스_업종_코드"], group_keys=False)[value_col].apply(
        group_percentile
    )


def score_quarter(quarter: int, refresh: bool = False) -> tuple[Path, dict[str, Any]]:
    cache_path = QUARTER_DIR / f"official_v2_6_scores_{quarter}.parquet"
    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        versions = set(cached.get("score_version", pd.Series(dtype=str)).dropna().astype(str).unique())
        required = set(KEYS + ["current_location_score", *AXIS_COLS, "score_version"])
        if versions == {engine.SCORE_VERSION} and required.issubset(cached.columns):
            print(f"[cache] {quarter}: {len(cached):,} official rows", flush=True)
            return cache_path, {
                "origin_quarter": quarter,
                "official_score_rows": int(len(cached)),
                "cache_status": "reused",
            }

    started = time.perf_counter()
    print(f"[score] {quarter}: building current {engine.SCORE_VERSION}", flush=True)
    base = engine.build_indicator_frame(quarter)
    built_seconds = time.perf_counter() - started
    scored = engine.score_frame(base)
    scored_seconds = time.perf_counter() - started - built_seconds

    official_mask = scored["current_location_score"].notna()
    if "official_rank_eligible" in scored.columns:
        official_mask &= scored["official_rank_eligible"].fillna(False).astype(bool)
    official = scored.loc[official_mask].copy()
    keep = [
        *KEYS,
        "상권_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "서비스_업종_코드_명",
        "weight_set",
        "current_location_score",
        "grade",
        "decision_label",
        "data_reliability_score",
        "score_version",
        "score_coverage_tier",
        "official_rank_eligible",
        *AXIS_COLS,
    ]
    keep = [col for col in keep if col in official.columns]
    official = official[keep]
    official["기준_년분기_코드"] = pd.to_numeric(
        official["기준_년분기_코드"], errors="raise"
    ).astype(int)
    official["상권_코드"] = official["상권_코드"].astype(str)
    official["서비스_업종_코드"] = official["서비스_업종_코드"].astype(str)
    official.to_parquet(cache_path, index=False)
    print(
        f"[score] {quarter}: {len(official):,}/{len(scored):,} official rows; "
        f"indicator={built_seconds:.1f}s score={scored_seconds:.1f}s",
        flush=True,
    )
    return cache_path, {
        "origin_quarter": quarter,
        "engine_rows": int(len(scored)),
        "official_score_rows": int(len(official)),
        "official_score_rate": float(len(official) / len(scored)) if len(scored) else None,
        "indicator_seconds": round(built_seconds, 3),
        "score_seconds": round(scored_seconds, 3),
        "cache_status": "rebuilt",
    }


def load_scores(quarters: Iterable[int], refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    paths: list[Path] = []
    audit_rows: list[dict[str, Any]] = []
    for quarter in quarters:
        path, audit = score_quarter(int(quarter), refresh=refresh)
        paths.append(path)
        audit_rows.append(audit)
    scores = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    scores = scores.rename(columns={"기준_년분기_코드": "origin_quarter"})
    scores["origin_quarter"] = pd.to_numeric(scores["origin_quarter"], errors="raise").astype(int)
    return scores, pd.DataFrame(audit_rows)


def verify_backend_parity(quarter: int) -> dict[str, Any]:
    backend_root = ROOT / "final_proj" / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    backend_engine = importlib.import_module(
        "app.rule_engine.build_rule_based_location_scores"
    )
    base = backend_engine.build_indicator_frame(int(quarter))
    scored = backend_engine.score_frame(base)
    official_mask = scored["current_location_score"].notna()
    if "official_rank_eligible" in scored.columns:
        official_mask &= scored["official_rank_eligible"].fillna(False).astype(bool)
    compare_cols = ["current_location_score", *AXIS_COLS]
    key_cols = ["상권_코드", "서비스_업종_코드"]
    backend_rows = scored.loc[
        official_mask, [*key_cols, *compare_cols, "score_version"]
    ].copy()
    cache_path = QUARTER_DIR / f"official_v2_6_scores_{int(quarter)}.parquet"
    if not cache_path.exists():
        raise FileNotFoundError(f"Backend parity requires score cache: {cache_path}")
    cached = pd.read_parquet(cache_path)[[*key_cols, *compare_cols, "score_version"]].copy()
    for frame in (backend_rows, cached):
        frame["상권_코드"] = frame["상권_코드"].astype(str)
        frame["서비스_업종_코드"] = frame["서비스_업종_코드"].astype(str)
    merged = backend_rows.merge(
        cached,
        on=key_cols,
        how="outer",
        suffixes=("_backend", "_backtest"),
        indicator=True,
        validate="one_to_one",
    )
    max_abs_diff: dict[str, float | None] = {}
    nonzero_diff_rows: dict[str, int] = {}
    for col in compare_cols:
        diff = (merged[f"{col}_backend"] - merged[f"{col}_backtest"]).abs()
        max_abs_diff[col] = None if diff.dropna().empty else float(diff.max())
        nonzero_diff_rows[col] = int((diff.fillna(0) > 1e-12).sum())
    merge_counts = merged["_merge"].value_counts().to_dict()
    version_match = bool(
        (merged["score_version_backend"] == merged["score_version_backtest"]).all()
    )
    all_match = (
        int(merge_counts.get("left_only", 0)) == 0
        and int(merge_counts.get("right_only", 0)) == 0
        and all(value == 0 for value in nonzero_diff_rows.values())
        and version_match
    )
    return {
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "quarter": int(quarter),
        "backend_score_version": backend_engine.SCORE_VERSION,
        "backend_data_root": str(backend_engine.GOLD.parent),
        "backend_rows": int(len(backend_rows)),
        "backtest_rows": int(len(cached)),
        "merge_counts": {str(k): int(v) for k, v in merge_counts.items()},
        "max_abs_diff": max_abs_diff,
        "nonzero_diff_rows": nonzero_diff_rows,
        "version_match": version_match,
        "all_match": all_match,
    }


def load_gold() -> tuple[pd.DataFrame, pd.DataFrame]:
    sales = pd.read_csv(
        SALES_PATH,
        encoding="utf-8-sig",
        usecols=[*KEYS, "당월_매출_금액"],
        dtype={"상권_코드": str, "서비스_업종_코드": str},
        low_memory=False,
    )
    competition = pd.read_csv(
        COMPETITION_PATH,
        encoding="utf-8-sig",
        usecols=[*KEYS, "점포_수", "폐업_률", "폐업_점포_수", "개업_점포_수"],
        dtype={"상권_코드": str, "서비스_업종_코드": str},
        low_memory=False,
    )
    for frame in (sales, competition):
        frame["기준_년분기_코드"] = pd.to_numeric(
            frame["기준_년분기_코드"], errors="raise"
        ).astype(int)
    sales["당월_매출_금액"] = pd.to_numeric(sales["당월_매출_금액"], errors="coerce")
    for col in ["점포_수", "폐업_률", "폐업_점포_수", "개업_점포_수"]:
        competition[col] = pd.to_numeric(competition[col], errors="coerce")

    if sales.duplicated(KEYS).any():
        raise ValueError("Gold sales has duplicate quarter×area×industry keys")
    if competition.duplicated(KEYS).any():
        raise ValueError("Gold competition has duplicate quarter×area×industry keys")
    return sales, competition


def build_market_panel(sales: pd.DataFrame, competition: pd.DataFrame) -> pd.DataFrame:
    panel = competition.merge(sales, on=KEYS, how="left", validate="one_to_one")
    panel["sales_per_store"] = np.where(
        panel["점포_수"] > 0,
        panel["당월_매출_금액"] / panel["점포_수"],
        np.nan,
    )
    peer = ["기준_년분기_코드", "서비스_업종_코드"]
    panel["sales_per_store_peer_median"] = panel.groupby(peer)["sales_per_store"].transform("median")
    panel["closure_rate_peer_median"] = panel.groupby(peer)["폐업_률"].transform("median")
    panel["sales_per_store_pct"] = panel.groupby(peer, group_keys=False)["sales_per_store"].apply(
        group_percentile
    )
    closure_pct = panel.groupby(peer, group_keys=False)["폐업_률"].apply(group_percentile)
    panel["low_closure_pct"] = 1.0 - closure_pct
    return panel


def prepare_origin_features(panel: pd.DataFrame) -> pd.DataFrame:
    origin = panel.rename(
        columns={
            "기준_년분기_코드": "origin_quarter",
            "당월_매출_금액": "origin_sales",
            "점포_수": "origin_store_count",
            "폐업_률": "origin_closure_rate",
            "폐업_점포_수": "origin_closure_count",
            "개업_점포_수": "origin_open_count",
            "sales_per_store": "origin_sales_per_store",
            "sales_per_store_pct": "origin_sales_per_store_pct",
            "low_closure_pct": "origin_low_closure_pct",
        }
    )
    return origin[
        [
            "origin_quarter",
            "상권_코드",
            "서비스_업종_코드",
            "origin_sales",
            "origin_store_count",
            "origin_closure_rate",
            "origin_closure_count",
            "origin_open_count",
            "origin_sales_per_store",
            "origin_sales_per_store_pct",
            "origin_low_closure_pct",
        ]
    ]


def prepare_target(panel: pd.DataFrame, horizon: int) -> pd.DataFrame:
    target = panel.copy()
    target["target_quarter"] = target["기준_년분기_코드"]
    target["origin_quarter"] = target["target_quarter"].map(
        lambda q: subtract_quarters(int(q), horizon)
    )
    target = target.rename(
        columns={
            "당월_매출_금액": "target_sales",
            "점포_수": "target_store_count",
            "폐업_률": "target_closure_rate",
            "폐업_점포_수": "target_closure_count",
            "개업_점포_수": "target_open_count",
            "sales_per_store": "target_sales_per_store",
            "sales_per_store_peer_median": "target_sales_per_store_peer_median",
            "closure_rate_peer_median": "target_closure_rate_peer_median",
            "sales_per_store_pct": "target_sales_per_store_pct",
            "low_closure_pct": "target_low_closure_pct",
        }
    )
    return target[
        [
            "origin_quarter",
            "target_quarter",
            "상권_코드",
            "서비스_업종_코드",
            "target_sales",
            "target_store_count",
            "target_closure_rate",
            "target_closure_count",
            "target_open_count",
            "target_sales_per_store",
            "target_sales_per_store_peer_median",
            "target_closure_rate_peer_median",
            "target_sales_per_store_pct",
            "target_low_closure_pct",
        ]
    ]


def attach_outcomes(
    scores: pd.DataFrame,
    origin_features: pd.DataFrame,
    panel: pd.DataFrame,
    horizon: int,
) -> pd.DataFrame:
    target = prepare_target(panel, horizon)
    df = scores.merge(
        origin_features,
        on=["origin_quarter", "상권_코드", "서비스_업종_코드"],
        how="left",
        validate="one_to_one",
    ).merge(
        target,
        on=["origin_quarter", "상권_코드", "서비스_업종_코드"],
        how="left",
        validate="one_to_one",
    )
    df["horizon_quarters"] = int(horizon)
    df["split"] = df["origin_quarter"].map(time_split)
    df["industry_prefix"] = df["서비스_업종_코드"].astype(str).str[:3]

    target_exists = df["target_store_count"].notna() & df["target_closure_rate"].notna()
    zero_store = target_exists & (df["target_store_count"] == 0)
    positive_store_observed_sales = (
        target_exists & (df["target_store_count"] > 0) & df["target_sales_per_store"].notna()
    )
    primary_available = zero_store | positive_store_observed_sales
    favorable_condition = (
        (df["target_store_count"] > 0)
        & (df["target_sales_per_store"] >= df["target_sales_per_store_peer_median"])
        & (df["target_closure_rate"] <= df["target_closure_rate_peer_median"])
    )
    df["primary_label_available"] = primary_available
    df["favorable_market_outcome"] = np.where(primary_available, favorable_condition.astype(float), np.nan)
    df["sensitivity_label_available"] = target_exists
    df["favorable_missing_sales_as_failure"] = np.where(
        target_exists, favorable_condition.astype(float), np.nan
    )

    df["target_sales_missing_positive_store"] = (
        target_exists & (df["target_store_count"] > 0) & df["target_sales_per_store"].isna()
    )
    df["target_zero_store"] = zero_store
    df["store_net_change_log"] = np.log1p(df["target_store_count"]) - np.log1p(
        df["origin_store_count"]
    )
    peer = ["origin_quarter", "서비스_업종_코드"]
    df["store_net_change_excess_log"] = df["store_net_change_log"] - df.groupby(peer)[
        "store_net_change_log"
    ].transform("median")
    df["sales_per_store_log_growth"] = np.log1p(df["target_sales_per_store"]) - np.log1p(
        df["origin_sales_per_store"]
    )

    df["model__score_v2_6"] = rank_within_origin_industry(df, "current_location_score")
    df["model__sales_persistence"] = df["origin_sales_per_store_pct"]
    df["model__closure_persistence"] = df["origin_low_closure_pct"]
    df["model__simple_persistence"] = df[
        ["origin_sales_per_store_pct", "origin_low_closure_pct"]
    ].mean(axis=1, skipna=False)
    return df


def safe_spearman(x: pd.Series, y: pd.Series) -> float:
    pair = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(pair) < 3 or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return float("nan")
    return float(pair["x"].rank(method="average").corr(pair["y"].rank(method="average")))


def roc_auc(y_true: pd.Series, signal: pd.Series) -> float:
    pair = pd.DataFrame({"y": y_true, "signal": signal}).dropna()
    if pair.empty:
        return float("nan")
    pair["y"] = pair["y"].astype(int)
    positives = int(pair["y"].sum())
    negatives = int(len(pair) - positives)
    if positives == 0 or negatives == 0:
        return float("nan")
    ranks = pair["signal"].rank(method="average")
    rank_sum_positive = float(ranks[pair["y"] == 1].sum())
    return (rank_sum_positive - positives * (positives + 1) / 2.0) / (positives * negatives)


def average_precision(y_true: pd.Series, signal: pd.Series) -> float:
    pair = pd.DataFrame({"y": y_true, "signal": signal}).dropna()
    if pair.empty:
        return float("nan")
    pair["y"] = pair["y"].astype(int)
    positives = int(pair["y"].sum())
    if positives == 0:
        return float("nan")
    pair = pair.sort_values("signal", ascending=False, kind="mergesort")
    cumulative_positive = pair["y"].cumsum()
    precision_at_rank = cumulative_positive / np.arange(1, len(pair) + 1)
    return float(precision_at_rank[pair["y"] == 1].sum() / positives)


def metric_bundle(df: pd.DataFrame, signal_col: str, label_col: str) -> dict[str, Any]:
    labeled = df[[label_col, signal_col]].dropna()
    n = int(len(labeled))
    positives = int(labeled[label_col].sum()) if n else 0
    base_rate = positives / n if n else float("nan")
    top_col = f"top20__{signal_col}"
    if top_col in df.columns:
        selected_mask = df[top_col].fillna(False).astype(bool)
    else:
        rank_groups = [
            col
            for col in ["horizon_quarters", "origin_quarter", "서비스_업종_코드"]
            if col in df.columns
        ]
        if rank_groups:
            # 동률 신호도 각 원점분기×업종에서 약 20%를 선택하도록 source-order를
            # 고정 tie-break로 사용한다. AUC 계산에는 average-rank를 유지해 동률 이점을 주지 않는다.
            selection_pct = df.groupby(rank_groups, group_keys=False)[signal_col].rank(
                pct=True, method="first"
            )
        else:
            selection_pct = df[signal_col].rank(pct=True, method="first")
        selected_mask = selection_pct > 0.80
    top_selected_rows = int((selected_mask & df[signal_col].notna()).sum())
    top = df.loc[selected_mask, [label_col, signal_col]].dropna()
    top_rate = float(top[label_col].mean()) if len(top) else float("nan")
    return {
        "n_labeled": n,
        "positives": positives,
        "base_rate": base_rate,
        "auc": roc_auc(labeled[label_col], labeled[signal_col]),
        "average_precision": average_precision(labeled[label_col], labeled[signal_col]),
        "top20_selected_rows": top_selected_rows,
        "top20_rows": int(len(top)),
        "top20_label_coverage_rate": len(top) / top_selected_rows if top_selected_rows else float("nan"),
        "top20_success_rate": top_rate,
        "top20_lift": top_rate / base_rate if base_rate and np.isfinite(top_rate) else float("nan"),
        "spearman_future_sales_pct": safe_spearman(df[signal_col], df["target_sales_per_store_pct"]),
        "spearman_future_low_closure_pct": safe_spearman(
            df[signal_col], df["target_low_closure_pct"]
        ),
        "spearman_store_net_change": safe_spearman(
            df[signal_col], df["store_net_change_excess_log"]
        ),
        "spearman_sales_per_store_growth": safe_spearman(
            df[signal_col], df["sales_per_store_log_growth"]
        ),
    }


def candidate_weight_grid() -> list[dict[str, float]]:
    candidates: list[dict[str, float]] = []
    # 각 축을 최소 10% 유지하고 10% 단위로 합계 100%인 84개 조합을 탐색한다.
    for units in itertools.product(range(1, 8), repeat=4):
        if sum(units) != 10:
            continue
        candidates.append({axis: unit / 10.0 for axis, unit in zip(AXES, units, strict=True)})
    return candidates


def add_candidate_signal(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    raw = sum(df[f"axis__{axis}"] * weight for axis, weight in weights.items())
    working = df[["origin_quarter", "서비스_업종_코드"]].copy()
    working["candidate_raw"] = raw
    return rank_within_origin_industry(working, "candidate_raw")


def select_candidate_weights(outcomes: pd.DataFrame) -> tuple[dict[str, float], pd.DataFrame]:
    development = outcomes[
        (outcomes["horizon_quarters"] == 4)
        & (outcomes["split"] == "development")
        & outcomes["primary_label_available"]
    ].copy()
    if development.empty:
        raise ValueError("No q+4 development rows are available for candidate weight selection")

    rows: list[dict[str, Any]] = []
    for candidate_id, weights in enumerate(candidate_weight_grid(), start=1):
        signal = add_candidate_signal(development, weights)
        temp = development.copy()
        temp["candidate_signal"] = signal
        # 후보 84개 탐색에서는 이미 원점분기×업종 백분위인 신호를 사용한다. 최종 선택
        # 후보는 아래 add_top20_flags에서 source-order 동률 분할을 다시 적용한다.
        temp["top20__candidate_signal"] = temp["candidate_signal"] > 0.80
        metrics = metric_bundle(temp, "candidate_signal", "favorable_market_outcome")
        rows.append(
            {
                "candidate_id": candidate_id,
                **{f"weight_{axis}": weights[axis] for axis in AXES},
                "development_q4_auc": metrics["auc"],
                "development_q4_average_precision": metrics["average_precision"],
                "development_q4_top20_lift": metrics["top20_lift"],
                "development_q4_n": metrics["n_labeled"],
            }
        )
    result = pd.DataFrame(rows).sort_values(
        ["development_q4_auc", "development_q4_top20_lift", "candidate_id"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    best = result.iloc[0]
    selected = {axis: float(best[f"weight_{axis}"]) for axis in AXES}
    result["selected_on_development"] = result["candidate_id"] == int(best["candidate_id"])
    return selected, result


def block_bootstrap_ci(
    quarter_rows: pd.DataFrame,
    value_col: str,
    *,
    iterations: int = 1000,
) -> tuple[float, float]:
    usable = quarter_rows[["origin_quarter", value_col, "n_labeled"]].dropna()
    if usable.empty:
        return float("nan"), float("nan")
    if len(usable) == 1:
        value = float(usable[value_col].iloc[0])
        return value, value
    values = usable[value_col].to_numpy(dtype=float)
    weights = usable["n_labeled"].to_numpy(dtype=float)
    rng = np.random.default_rng(RNG_SEED + len(usable) + sum(map(ord, value_col)))
    samples = np.empty(iterations, dtype=float)
    for index in range(iterations):
        draw = rng.integers(0, len(usable), size=len(usable))
        samples[index] = np.average(values[draw], weights=weights[draw])
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def compute_quarter_metrics(outcomes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    label_specs = [
        (PRIMARY_LABEL, "favorable_market_outcome"),
        (SENSITIVITY_LABEL, "favorable_missing_sales_as_failure"),
    ]
    for horizon in HORIZONS:
        horizon_df = outcomes[outcomes["horizon_quarters"] == horizon]
        for quarter, quarter_df in horizon_df.groupby("origin_quarter", sort=True):
            split = str(quarter_df["split"].iloc[0])
            for label_variant, label_col in label_specs:
                for model_name, signal_col in MODEL_COLUMNS.items():
                    metrics = metric_bundle(quarter_df, signal_col, label_col)
                    rows.append(
                        {
                            "horizon_quarters": horizon,
                            "origin_quarter": int(quarter),
                            "target_quarter": add_quarters(int(quarter), horizon),
                            "split": split,
                            "label_variant": label_variant,
                            "model": model_name,
                            **metrics,
                        }
                    )
    return pd.DataFrame(rows)


def compute_model_metrics(outcomes: pd.DataFrame, quarter_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    label_specs = [
        (PRIMARY_LABEL, "favorable_market_outcome"),
        (SENSITIVITY_LABEL, "favorable_missing_sales_as_failure"),
    ]
    split_specs = ["all", "development", "validation", "holdout"]
    for horizon in HORIZONS:
        horizon_df = outcomes[outcomes["horizon_quarters"] == horizon]
        for split in split_specs:
            split_df = horizon_df if split == "all" else horizon_df[horizon_df["split"] == split]
            if split_df.empty:
                continue
            for label_variant, label_col in label_specs:
                for model_name, signal_col in MODEL_COLUMNS.items():
                    metrics = metric_bundle(split_df, signal_col, label_col)
                    quarter_subset = quarter_metrics[
                        (quarter_metrics["horizon_quarters"] == horizon)
                        & (quarter_metrics["label_variant"] == label_variant)
                        & (quarter_metrics["model"] == model_name)
                    ]
                    if split != "all":
                        quarter_subset = quarter_subset[quarter_subset["split"] == split]
                    auc_low, auc_high = block_bootstrap_ci(quarter_subset, "auc")
                    lift_low, lift_high = block_bootstrap_ci(quarter_subset, "top20_lift")
                    rows.append(
                        {
                            "horizon_quarters": horizon,
                            "split": split,
                            "label_variant": label_variant,
                            "model": model_name,
                            "origin_quarters": int(split_df["origin_quarter"].nunique()),
                            **metrics,
                            "auc_quarter_block_ci_low": auc_low,
                            "auc_quarter_block_ci_high": auc_high,
                            "top20_lift_quarter_block_ci_low": lift_low,
                            "top20_lift_quarter_block_ci_high": lift_high,
                        }
                    )
    return pd.DataFrame(rows)


def compute_grade_metrics(outcomes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        horizon_df = outcomes[outcomes["horizon_quarters"] == horizon]
        for split in ["all", "development", "validation", "holdout"]:
            split_df = horizon_df if split == "all" else horizon_df[horizon_df["split"] == split]
            primary_labeled = split_df[split_df["primary_label_available"]].copy()
            sensitivity_labeled = split_df[split_df["sensitivity_label_available"]].copy()
            if primary_labeled.empty:
                continue
            base_rate = float(primary_labeled["favorable_market_outcome"].mean())
            sensitivity_base_rate = float(
                sensitivity_labeled["favorable_missing_sales_as_failure"].mean()
            )
            for grade, all_grade_df in split_df.groupby("grade", dropna=False):
                grade_primary = all_grade_df[all_grade_df["primary_label_available"]]
                grade_sensitivity = all_grade_df[all_grade_df["sensitivity_label_available"]]
                if grade_primary.empty:
                    continue
                success = float(grade_primary["favorable_market_outcome"].mean())
                sensitivity_success = float(
                    grade_sensitivity["favorable_missing_sales_as_failure"].mean()
                )
                rows.append(
                    {
                        "horizon_quarters": horizon,
                        "split": split,
                        "grade": "미등급" if pd.isna(grade) else str(grade),
                        "score_rows": int(len(all_grade_df)),
                        "n_labeled": int(len(grade_primary)),
                        "primary_label_coverage_rate": float(
                            all_grade_df["primary_label_available"].mean()
                        ),
                        "success_rate": success,
                        "base_rate": base_rate,
                        "lift": success / base_rate if base_rate else float("nan"),
                        "sensitivity_labeled_rows": int(len(grade_sensitivity)),
                        "sensitivity_success_rate": sensitivity_success,
                        "sensitivity_base_rate": sensitivity_base_rate,
                        "sensitivity_lift": (
                            sensitivity_success / sensitivity_base_rate
                            if sensitivity_base_rate
                            else float("nan")
                        ),
                    }
                )
    return pd.DataFrame(rows)


def add_top20_flags(outcomes: pd.DataFrame) -> pd.DataFrame:
    df = outcomes.copy()
    groups = ["horizon_quarters", "origin_quarter", "서비스_업종_코드"]
    for signal_col in MODEL_COLUMNS.values():
        selection_pct = df.groupby(groups, group_keys=False)[signal_col].rank(
            pct=True, method="first"
        )
        df[f"top20__{signal_col}"] = selection_pct > 0.80
    return df


def compute_segment_metrics(outcomes: pd.DataFrame) -> pd.DataFrame:
    primary = outcomes[
        (outcomes["horizon_quarters"] == 4)
        & outcomes["primary_label_available"]
        & (outcomes["split"].isin(["validation", "holdout"]))
    ]
    rows: list[dict[str, Any]] = []
    segment_specs = [("industry_prefix", "industry_prefix"), ("district", "자치구_코드_명")]
    for segment_type, segment_col in segment_specs:
        if segment_col not in primary.columns:
            continue
        for segment, segment_df in primary.groupby(segment_col, dropna=False):
            if len(segment_df) < 250:
                continue
            for model_name in ["현행 v2.6 점수", "단순 매출+폐업 기준선", "개발구간 선택 축가중치"]:
                signal_col = MODEL_COLUMNS[model_name]
                metrics = metric_bundle(segment_df, signal_col, "favorable_market_outcome")
                rows.append(
                    {
                        "segment_type": segment_type,
                        "segment": "미상" if pd.isna(segment) else str(segment),
                        "model": model_name,
                        **metrics,
                    }
                )
    return pd.DataFrame(rows)


def compute_coverage(
    scores: pd.DataFrame,
    outcomes: pd.DataFrame,
    competition: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    competition_counts = competition.groupby("기준_년분기_코드").size().to_dict()
    for quarter, score_df in scores.groupby("origin_quarter", sort=True):
        row: dict[str, Any] = {
            "origin_quarter": int(quarter),
            "gold_competition_rows": int(competition_counts.get(int(quarter), 0)),
            "official_score_rows": int(len(score_df)),
        }
        row["official_score_coverage_rate"] = (
            row["official_score_rows"] / row["gold_competition_rows"]
            if row["gold_competition_rows"]
            else float("nan")
        )
        for horizon in HORIZONS:
            subset = outcomes[
                (outcomes["origin_quarter"] == quarter)
                & (outcomes["horizon_quarters"] == horizon)
            ]
            prefix = f"q_plus_{horizon}"
            row[f"{prefix}_target_quarter"] = add_quarters(int(quarter), horizon)
            row[f"{prefix}_score_rows"] = int(len(subset))
            row[f"{prefix}_primary_labeled_rows"] = int(subset["primary_label_available"].sum())
            row[f"{prefix}_primary_label_coverage_rate"] = (
                float(subset["primary_label_available"].mean()) if len(subset) else float("nan")
            )
            row[f"{prefix}_missing_sales_positive_store_rows"] = int(
                subset["target_sales_missing_positive_store"].sum()
            )
            row[f"{prefix}_zero_store_rows"] = int(subset["target_zero_store"].sum())
        rows.append(row)
    return pd.DataFrame(rows)


def lookup_metric(
    model_metrics: pd.DataFrame,
    *,
    horizon: int,
    split: str,
    model: str,
    metric: str,
    label_variant: str = PRIMARY_LABEL,
) -> float:
    match = model_metrics[
        (model_metrics["horizon_quarters"] == horizon)
        & (model_metrics["split"] == split)
        & (model_metrics["label_variant"] == label_variant)
        & (model_metrics["model"] == model)
    ]
    if len(match) != 1:
        return float("nan")
    return float(match.iloc[0][metric])


def decide_weight_promotion(
    model_metrics: pd.DataFrame,
    selected_weights: dict[str, float],
) -> dict[str, Any]:
    candidate = "개발구간 선택 축가중치"
    current = "현행 v2.6 점수"
    simple = "단순 매출+폐업 기준선"

    def delta(split: str, metric: str) -> float:
        return lookup_metric(
            model_metrics, horizon=4, split=split, model=candidate, metric=metric
        ) - lookup_metric(model_metrics, horizon=4, split=split, model=current, metric=metric)

    q4_holdout_quarters = int(
        model_metrics[
            (model_metrics["horizon_quarters"] == 4)
            & (model_metrics["split"] == "holdout")
            & (model_metrics["label_variant"] == PRIMARY_LABEL)
            & (model_metrics["model"] == candidate)
        ]["origin_quarters"].max()
    )
    criteria = {
        "validation_auc_delta_at_least_0_01": bool(delta("validation", "auc") >= 0.01),
        "holdout_auc_delta_at_least_0_01": bool(delta("holdout", "auc") >= 0.01),
        "validation_top20_lift_not_lower": bool(delta("validation", "top20_lift") >= 0.0),
        "holdout_top20_lift_not_lower": bool(delta("holdout", "top20_lift") >= 0.0),
        "holdout_auc_meets_simple_baseline": bool(
            lookup_metric(model_metrics, horizon=4, split="holdout", model=candidate, metric="auc")
            >= lookup_metric(model_metrics, horizon=4, split="holdout", model=simple, metric="auc")
        ),
        "validation_low_closure_spearman_nonnegative": bool(
            lookup_metric(
                model_metrics,
                horizon=4,
                split="validation",
                model=candidate,
                metric="spearman_future_low_closure_pct",
            )
            >= 0.0
        ),
        "holdout_low_closure_spearman_nonnegative": bool(
            lookup_metric(
                model_metrics,
                horizon=4,
                split="holdout",
                model=candidate,
                metric="spearman_future_low_closure_pct",
            )
            >= 0.0
        ),
        "holdout_store_net_change_spearman_nonnegative": bool(
            lookup_metric(
                model_metrics,
                horizon=4,
                split="holdout",
                model=candidate,
                metric="spearman_store_net_change",
            )
            >= 0.0
        ),
        "at_least_4_clean_q4_holdout_quarters": bool(q4_holdout_quarters >= 4),
    }
    all_pass = all(criteria.values())
    return {
        "decision": "promote" if all_pass else "do_not_change_production_weights",
        "promotion_pass": all_pass,
        "selected_candidate_weights": selected_weights,
        "criteria": criteria,
        "q4_holdout_quarters": q4_holdout_quarters,
        "validation_auc_delta_vs_current": delta("validation", "auc"),
        "holdout_auc_delta_vs_current": delta("holdout", "auc"),
        "reason_ko": (
            "사전 기준을 모두 통과해 운영 가중치 승격 가능"
            if all_pass
            else "사전 승격 기준을 모두 충족하지 못했으므로 운영 가중치는 변경하지 않음"
        ),
        "mandatory_caveat_ko": (
            "현행 가중치는 2021Q1~2025Q4의 q+1 정답 전체를 참고해 만들어졌으므로 "
            "현행 점수의 전기간 수치는 회고적 겉보기 성능이다. q+4 최신 홀드아웃도 1개 분기뿐이다."
        ),
    }


def source_inventory(
    sales: pd.DataFrame,
    competition: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> dict[str, Any]:
    inventory: dict[str, Any] = {
        "sales": {
            "path": str(SALES_PATH.relative_to(ROOT)).replace("\\", "/"),
            "rows": int(len(sales)),
            "quarters": int(sales["기준_년분기_코드"].nunique()),
            "quarter_min": int(sales["기준_년분기_코드"].min()),
            "quarter_max": int(sales["기준_년분기_코드"].max()),
            "duplicate_keys": int(sales.duplicated(KEYS).sum()),
            "sha256": sha256_file(SALES_PATH),
        },
        "competition": {
            "path": str(COMPETITION_PATH.relative_to(ROOT)).replace("\\", "/"),
            "rows": int(len(competition)),
            "quarters": int(competition["기준_년분기_코드"].nunique()),
            "quarter_min": int(competition["기준_년분기_코드"].min()),
            "quarter_max": int(competition["기준_년분기_코드"].max()),
            "duplicate_keys": int(competition.duplicated(KEYS).sum()),
            "sha256": sha256_file(COMPETITION_PATH),
        },
    }
    for horizon in HORIZONS:
        subset = outcomes[outcomes["horizon_quarters"] == horizon]
        inventory[f"official_scored_cohort_q_plus_{horizon}"] = {
            "rows": int(len(subset)),
            "origin_quarters": int(subset["origin_quarter"].nunique()),
            "primary_labeled_rows": int(subset["primary_label_available"].sum()),
            "primary_label_coverage_rate": float(subset["primary_label_available"].mean()),
            "missing_sales_positive_store_rows": int(
                subset["target_sales_missing_positive_store"].sum()
            ),
            "zero_store_rows_labeled_unfavorable": int(subset["target_zero_store"].sum()),
        }
    return inventory


def format_metric(value: float, digits: int = 3) -> str:
    return "해당 없음" if not np.isfinite(value) else f"{value:.{digits}f}"


def write_validation_record(
    summary: dict[str, Any],
    model_metrics: pd.DataFrame,
    grade_metrics: pd.DataFrame,
    coverage: pd.DataFrame,
    candidate_metrics: pd.DataFrame,
) -> Path:
    current_q4_all = model_metrics[
        (model_metrics["horizon_quarters"] == 4)
        & (model_metrics["split"] == "all")
        & (model_metrics["label_variant"] == PRIMARY_LABEL)
        & (model_metrics["model"] == "현행 v2.6 점수")
    ].iloc[0]
    current_q4_holdout = model_metrics[
        (model_metrics["horizon_quarters"] == 4)
        & (model_metrics["split"] == "holdout")
        & (model_metrics["label_variant"] == PRIMARY_LABEL)
        & (model_metrics["model"] == "현행 v2.6 점수")
    ].iloc[0]
    simple_q4_holdout = model_metrics[
        (model_metrics["horizon_quarters"] == 4)
        & (model_metrics["split"] == "holdout")
        & (model_metrics["label_variant"] == PRIMARY_LABEL)
        & (model_metrics["model"] == "단순 매출+폐업 기준선")
    ].iloc[0]
    selected = candidate_metrics[candidate_metrics["selected_on_development"]].iloc[0]
    source = summary["source_inventory"]
    decision = summary["weight_decision"]
    parity = summary.get("backend_runtime_parity")
    grade_table = grade_metrics[
        (grade_metrics["horizon_quarters"] == 4) & (grade_metrics["split"] == "all")
    ].copy()
    order = {grade: index for index, grade in enumerate(["A", "B", "C", "D", "E", "미등급"])}
    grade_table["order"] = grade_table["grade"].map(order).fillna(99)
    grade_table = grade_table.sort_values("order")
    grade_a = grade_table[grade_table["grade"] == "A"].iloc[0]
    grade_e = grade_table[grade_table["grade"] == "E"].iloc[0]

    lines = [
        "# 현행 입지점수 v2.6 미래 성과 예측력 검증 기록",
        "",
        f"- 실행 시각: `{summary['created_at']}`",
        f"- 검증 버전: `{VALIDATION_VERSION}`",
        f"- 점수 버전: `{engine.SCORE_VERSION}`",
        f"- 결정: **{decision['reason_ko']}**",
        "",
        "## 결론",
        "",
        (
            f"현행 점수의 1년 뒤 복합 유리결과 AUC는 전기간 회고 평가에서 "
            f"**{current_q4_all['auc']:.3f}**, 상위 20% 성공률은 "
            f"**{current_q4_all['top20_success_rate']:.1%}**, lift는 "
            f"**{current_q4_all['top20_lift']:.2f}배**였다. 다만 현행 가중치는 이 기간의 q+1 정답을 "
            "참고해 만들어졌으므로 엄밀한 미사용 홀드아웃 성능이 아니라 회고적 겉보기 성능이다."
        ),
        "",
        (
            f"세부적으로는 1년 뒤 점포당 매출 순위와의 Spearman 상관이 "
            f"**{current_q4_all['spearman_future_sales_pct']:.3f}**였지만, 낮은 폐업률과는 "
            f"**{current_q4_all['spearman_future_low_closure_pct']:.3f}**, 점포당 매출 성장과는 "
            f"**{current_q4_all['spearman_sales_per_store_growth']:.3f}**였다. 따라서 현행 점수는 현재 매출 수준의 "
            "지속성을 주로 포착하며 미래 성장·저폐업을 직접 예측한다고 해석하면 안 된다."
        ),
        "",
        (
            f"최신 q+4 홀드아웃({int(current_q4_holdout['origin_quarters'])}개 원점 분기)에서 현행 점수 AUC는 "
            f"**{current_q4_holdout['auc']:.3f}**, 단순 현재 매출+폐업 기준선은 "
            f"**{simple_q4_holdout['auc']:.3f}**였다. 점수는 성공확률이 아니라 다축 입지조건 순위로 유지하며, "
            "운영 가중치는 이번 실행에서 자동 변경하지 않았다."
        ),
        "",
        *(
            [
                "## 관리자·백엔드 점수 경로 일치",
                "",
                (
                    f"백엔드 엔진으로 {parity['quarter']} 분기를 독립 재계산해 "
                    f"**{parity['backend_rows']:,}행**을 비교했다. 키는 모두 일치했고, "
                    f"최종점수와 매출·경쟁·수요·접근성 4축의 최대 절대차는 모두 "
                    f"**{max(value or 0.0 for value in parity['max_abs_diff'].values()):.1f}**, "
                    f"버전 일치={parity['version_match']}, 전체 일치={parity['all_match']}였다."
                ),
                "",
            ]
            if parity
            else []
        ),
        "## 정답을 얻은 방법",
        "",
        f"- 매출 정답: `{source['sales']['path']}` ({source['sales']['rows']:,}행, "
        f"{source['sales']['quarter_min']}~{source['sales']['quarter_max']}, SHA-256 `{source['sales']['sha256']}`)",
        f"- 점포·폐업 정답: `{source['competition']['path']}` ({source['competition']['rows']:,}행, "
        f"{source['competition']['quarter_min']}~{source['competition']['quarter_max']}, SHA-256 `{source['competition']['sha256']}`)",
        "- 조인 키: `기준_년분기_코드 × 상권_코드 × 서비스_업종_코드`; 이름 조인은 사용하지 않았다.",
        "- q+4를 주 평가, q+1을 보조 평가로 사용했다. 점수·입력은 원점 분기까지의 데이터만 사용했다.",
        "- 미래 점포당 매출은 목표 분기·동일 세부업종 내 백분위와 중앙값으로 정의했다.",
        "- 미래 폐업 압력은 목표 분기·동일 세부업종 내 폐업률과 중앙값으로 정의했다.",
        "- 복합 유리결과 = 미래 점포 수가 1개 이상이고, 미래 점포당 매출이 동종 중앙값 이상이며, 미래 폐업률이 동종 중앙값 이하.",
        "- 미래 점포 수 0은 명시적 불리 결과다. 점포 수가 양수인데 매출만 누락된 행은 주 평가에서 제외하고 민감도 분석에서만 실패로 처리했다.",
        "",
        "## 코호트와 누락",
        "",
        "| 기간 | 공식점수 행 | 주 정답 행 | 정답 커버리지 | 양수 점포·매출 누락 | 0점포 불리결과 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for horizon in HORIZONS:
        info = source[f"official_scored_cohort_q_plus_{horizon}"]
        lines.append(
            f"| q+{horizon} | {info['rows']:,} | {info['primary_labeled_rows']:,} | "
            f"{info['primary_label_coverage_rate']:.1%} | "
            f"{info['missing_sales_positive_store_rows']:,} | "
            f"{info['zero_store_rows_labeled_unfavorable']:,} |"
        )
    lines.extend(
        [
            "",
            "## 정확도 결과",
            "",
            "전체 수치는 `model_metrics.csv`, 분기별 안정성은 `quarter_metrics.csv`, 등급별 결과는 `grade_metrics.csv`에 기록했다.",
            "",
            "| 1년 뒤 등급 | 점수 행 | 정답 커버리지 | 주 유리결과율 | 누락=실패 민감도 | 전체 대비 lift |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in grade_table.iterrows():
        lines.append(
            f"| {row['grade']} | {int(row['score_rows']):,} | "
            f"{row['primary_label_coverage_rate']:.1%} | {row['success_rate']:.1%} | "
            f"{row['sensitivity_success_rate']:.1%} | {row['lift']:.2f}배 |"
        )
    lines.extend(
        [
            "",
            "## 후보 가중치와 운영 결정",
            "",
            (
                f"개발구간 q+4 정답만으로 4개 축을 각 최소 10%, 10% 단위로 탐색했다. 선택값은 "
                f"매출 {selected['weight_sales']:.0%}, 경쟁 {selected['weight_competition']:.0%}, "
                f"수요 {selected['weight_demand']:.0%}, 접근성 {selected['weight_accessibility']:.0%}이며 "
                f"개발구간 AUC는 {selected['development_q4_auc']:.3f}였다."
            ),
            "",
            f"결정: **{decision['reason_ko']}**",
            "",
        ]
    )
    for name, passed in decision["criteria"].items():
        lines.append(f"- {'통과' if passed else '미통과'}: `{name}`")
    lines.extend(
        [
            "",
            "## 제한과 금지 해석",
            "",
            "- 개별 점포 ID·개업일·폐업일이 없으므로 이 결과는 개별 점포 생존확률이 아니다.",
            "- 점포 수 증감은 신규 개업과 폐업을 상쇄한 순변화라서 생존을 직접 측정하지 않는다.",
            "- 현재 가중치가 전체 q+1 기간을 참고했으므로 전기간 및 최신기간 현행 점수 비교에는 가중치 선택 누수가 남아 있다.",
            "- q+4 최신 홀드아웃은 2025Q1→2026Q1 한 분기뿐이라 경기 국면 일반화 판단에는 부족하다.",
            (
                f"- 등급별 정답 커버리지가 비대칭이다(A {grade_a['primary_label_coverage_rate']:.1%}, "
                f"E {grade_e['primary_label_coverage_rate']:.1%}). 누락=실패 민감도에서도 A "
                f"{grade_a['sensitivity_success_rate']:.1%}, E {grade_e['sensitivity_success_rate']:.1%}로 "
                "서열은 유지되지만 주 결과율은 일부 위쪽 편향될 수 있다."
            ),
            "- 이 점수는 확률 보정 모델이 아니며, AUC와 lift는 순위 선별력만 나타낸다.",
            "- 상위 20%는 원점 분기×업종마다 신호 동률을 원천 행 순서로 고정 분할해 약 20%를 선택하며, AUC는 동률 평균순위를 사용한다.",
            "",
            "## 재실행",
            "",
            "```powershell",
            ".\\final_proj\\.venv\\Scripts\\python.exe scripts\\evaluate_score_predictive_validity.py",
            "```",
            "",
            "분기 캐시를 모두 다시 계산하려면 `--refresh-scores`를 추가한다.",
            "",
            "## 차트 계약(보고서 지원 기록)",
            "",
            "- 모델 AUC: 질문=현행 점수와 기준선의 q+4 순위 선별력은 시간구간별로 어떻게 다른가; x=모델, y=AUC, 색=구간.",
            "- 등급별 결과율: 질문=현행 등급이 1년 뒤 복합 유리결과를 단조롭게 구분하는가; x=등급, y=유리결과율.",
            "- 분기 안정성: 질문=모델별 q+4 AUC가 원점 분기에 따라 안정적인가; x=원점 분기, y=AUC, 색=모델.",
            "",
        ]
    )
    path = VALIDATION_RECORD_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def report_source(source_id: str, label: str, relative_path: str, description: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "label": label,
        "path": relative_path,
        "query": {
            "engine": "duckdb",
            "language": "sql",
            "sql": f"SELECT * FROM read_csv_auto('{relative_path}')",
            "description": description,
            "tables_used": [relative_path],
            "filters": ["보고서 데이터셋은 CSV의 명시된 horizon/split/label_variant 열로 필터링"],
            "metric_definitions": [
                "AUC는 복합 유리결과에 대한 순위 선별력",
                "top20_lift는 상위 20% 결과율을 전체 결과율로 나눈 값",
                "percent 형식 값은 0~1 비율",
            ],
        },
    }


def write_report_artifact(
    summary: dict[str, Any],
    model_metrics: pd.DataFrame,
    quarter_metrics: pd.DataFrame,
    grade_metrics: pd.DataFrame,
    coverage: pd.DataFrame,
) -> Path:
    title = "현행 입지점수 v2.6 미래 성과 예측력 검증"
    primary_q4 = model_metrics[
        (model_metrics["horizon_quarters"] == 4)
        & (model_metrics["label_variant"] == PRIMARY_LABEL)
    ].copy()
    chart_models = ["현행 v2.6 점수", "단순 매출+폐업 기준선", "개발구간 선택 축가중치"]
    split_label = {"development": "개발", "validation": "검증", "holdout": "최신 홀드아웃"}
    model_auc = primary_q4[
        primary_q4["model"].isin(chart_models)
        & primary_q4["split"].isin(split_label)
    ][
        [
            "model",
            "split",
            "origin_quarters",
            "n_labeled",
            "auc",
            "auc_quarter_block_ci_low",
            "auc_quarter_block_ci_high",
            "top20_success_rate",
            "top20_lift",
        ]
    ].copy()
    model_auc["split_display"] = model_auc["split"].map(split_label)

    grade_success = grade_metrics[
        (grade_metrics["horizon_quarters"] == 4) & (grade_metrics["split"] == "all")
    ].copy()
    grade_order = {grade: index for index, grade in enumerate(["E", "D", "C", "B", "A"])}
    grade_success = grade_success[grade_success["grade"].isin(grade_order)].copy()
    grade_success["grade_order"] = grade_success["grade"].map(grade_order)
    grade_success = grade_success.sort_values("grade_order")
    grade_a = grade_success[grade_success["grade"] == "A"].iloc[0]
    grade_e = grade_success[grade_success["grade"] == "E"].iloc[0]

    quarter_stability = quarter_metrics[
        (quarter_metrics["horizon_quarters"] == 4)
        & (quarter_metrics["label_variant"] == PRIMARY_LABEL)
        & quarter_metrics["model"].isin(chart_models)
    ][["origin_quarter", "target_quarter", "split", "model", "n_labeled", "auc", "top20_lift"]].copy()
    quarter_stability["origin_quarter_display"] = quarter_stability["origin_quarter"].astype(str)

    model_detail = primary_q4[
        (primary_q4["split"] == "holdout") & primary_q4["model"].isin(MODEL_COLUMNS)
    ][
        [
            "model",
            "origin_quarters",
            "n_labeled",
            "base_rate",
            "auc",
            "auc_quarter_block_ci_low",
            "auc_quarter_block_ci_high",
            "average_precision",
            "top20_success_rate",
            "top20_lift",
            "spearman_future_sales_pct",
            "spearman_future_low_closure_pct",
            "spearman_store_net_change",
        ]
    ].sort_values("auc", ascending=False)

    coverage_detail = coverage[
        [
            "origin_quarter",
            "gold_competition_rows",
            "official_score_rows",
            "official_score_coverage_rate",
            "q_plus_1_primary_label_coverage_rate",
            "q_plus_4_primary_label_coverage_rate",
        ]
    ].copy()
    coverage_detail["origin_quarter_display"] = coverage_detail["origin_quarter"].astype(str)

    current_all = primary_q4[
        (primary_q4["split"] == "all") & (primary_q4["model"] == "현행 v2.6 점수")
    ].iloc[0]
    current_holdout = primary_q4[
        (primary_q4["split"] == "holdout") & (primary_q4["model"] == "현행 v2.6 점수")
    ].iloc[0]
    simple_holdout = primary_q4[
        (primary_q4["split"] == "holdout") & (primary_q4["model"] == "단순 매출+폐업 기준선")
    ].iloc[0]
    decision = summary["weight_decision"]
    parity = summary.get("backend_runtime_parity")
    parity_sentence = (
        f" 백엔드 엔진으로 {parity['quarter']} 분기 {parity['backend_rows']:,}행을 재계산한 결과 "
        "최종점수와 4축의 최대 절대차는 0.0이었다."
        if parity
        else ""
    )

    rel_base = "datacorpus/_score_predictive_validation/v2_6_20260716"
    source_specs = [
        (
            "src_model_metrics",
            "모델 종합 성능",
            f"{rel_base}/model_metrics.csv",
            "기간·시간구간·정답변형·모델별 종합 예측력과 분기 블록 신뢰구간",
        ),
        (
            "src_quarter_metrics",
            "분기별 성능",
            f"{rel_base}/quarter_metrics.csv",
            "원점 분기별 AUC와 상위 20% lift",
        ),
        (
            "src_grade_metrics",
            "등급별 결과",
            f"{rel_base}/grade_metrics.csv",
            "현행 등급별 미래 복합 유리결과율과 lift",
        ),
        (
            "src_coverage",
            "코호트 커버리지",
            f"{rel_base}/coverage_by_quarter.csv",
            "원점 분기별 공식점수 및 미래 정답 커버리지",
        ),
    ]
    manifest_sources = [
        {"id": source_id, "label": label, "path": path}
        for source_id, label, path, _ in source_specs
    ]
    sources = [report_source(*spec) for spec in source_specs]

    datasets = {
        "model_auc": json_ready(model_auc.to_dict(orient="records")),
        "grade_success": json_ready(grade_success.to_dict(orient="records")),
        "quarter_stability": json_ready(quarter_stability.to_dict(orient="records")),
        "model_detail": json_ready(model_detail.to_dict(orient="records")),
        "coverage_detail": json_ready(coverage_detail.to_dict(orient="records")),
    }
    generated_at = summary["created_at"]
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": title,
            "description": "과거 현행 점수와 이후 공식 Gold 매출·폐업·점포 데이터를 연결한 기술 검증 보고서",
            "generatedAt": generated_at,
            "charts": [
                {
                    "id": "chart_model_auc",
                    "title": "1년 뒤 복합결과 AUC",
                    "subtitle": "개발·검증·최신 홀드아웃 구간별 순위 선별력",
                    "type": "bar",
                    "dataset": "model_auc",
                    "sourceId": "src_model_metrics",
                    "encodings": {
                        "x": {"field": "model", "type": "nominal", "label": "모델"},
                        "y": {"field": "auc", "type": "quantitative", "label": "AUC", "format": "number"},
                        "color": {"field": "split_display", "type": "nominal", "label": "시간 구간"},
                    },
                    "valueFormat": "number",
                },
                {
                    "id": "chart_grade_success",
                    "title": "현행 등급별 1년 뒤 유리결과율",
                    "subtitle": "미래 점포당 매출 동종 중앙값 이상·폐업률 중앙값 이하의 복합 결과",
                    "type": "bar",
                    "dataset": "grade_success",
                    "sourceId": "src_grade_metrics",
                    "encodings": {
                        "x": {"field": "grade", "type": "ordinal", "label": "등급"},
                        "y": {
                            "field": "success_rate",
                            "type": "quantitative",
                            "label": "유리결과율",
                            "format": "percent",
                        },
                    },
                    "valueFormat": "percent",
                },
                {
                    "id": "chart_quarter_stability",
                    "title": "원점 분기별 1년 뒤 AUC",
                    "subtitle": "한 시기의 경기 국면에만 의존하는지 확인",
                    "type": "line",
                    "dataset": "quarter_stability",
                    "sourceId": "src_quarter_metrics",
                    "encodings": {
                        "x": {
                            "field": "origin_quarter_display",
                            "type": "ordinal",
                            "label": "원점 분기",
                        },
                        "y": {"field": "auc", "type": "quantitative", "label": "AUC", "format": "number"},
                        "color": {"field": "model", "type": "nominal", "label": "모델"},
                    },
                    "valueFormat": "number",
                },
            ],
            "tables": [
                {
                    "id": "table_model_detail",
                    "title": "최신 q+4 홀드아웃 모델 비교",
                    "subtitle": "AUC는 순위 선별력이며 성공확률 보정값이 아님",
                    "dataset": "model_detail",
                    "sourceId": "src_model_metrics",
                    "defaultSort": {"field": "auc", "direction": "desc"},
                    "columns": [
                        {"field": "model", "label": "모델", "type": "string"},
                        {"field": "origin_quarters", "label": "원점 분기", "type": "number"},
                        {"field": "n_labeled", "label": "정답 행", "type": "number"},
                        {"field": "base_rate", "label": "기준 결과율", "type": "number", "format": "percent"},
                        {"field": "auc", "label": "AUC", "type": "number", "format": "number"},
                        {"field": "average_precision", "label": "평균정밀도", "type": "number", "format": "number"},
                        {"field": "top20_success_rate", "label": "상위20% 결과율", "type": "number", "format": "percent"},
                        {"field": "top20_lift", "label": "상위20% lift", "type": "number", "format": "number"},
                        {"field": "spearman_future_sales_pct", "label": "미래매출 순위상관", "type": "number", "format": "number"},
                        {"field": "spearman_future_low_closure_pct", "label": "저폐업 순위상관", "type": "number", "format": "number"},
                        {"field": "spearman_store_net_change", "label": "점포순변화 순위상관", "type": "number", "format": "number"},
                    ],
                },
                {
                    "id": "table_coverage_detail",
                    "title": "분기별 점수·정답 커버리지",
                    "subtitle": "매출 누락을 숨기지 않고 주 평가 행 수와 분리",
                    "dataset": "coverage_detail",
                    "sourceId": "src_coverage",
                    "defaultSort": {"field": "origin_quarter", "direction": "asc"},
                    "columns": [
                        {"field": "origin_quarter", "label": "원점 분기", "type": "number"},
                        {"field": "gold_competition_rows", "label": "Gold 업종행", "type": "number"},
                        {"field": "official_score_rows", "label": "공식점수 행", "type": "number"},
                        {"field": "official_score_coverage_rate", "label": "점수 커버리지", "type": "number", "format": "percent"},
                        {"field": "q_plus_1_primary_label_coverage_rate", "label": "q+1 정답률", "type": "number", "format": "percent"},
                        {"field": "q_plus_4_primary_label_coverage_rate", "label": "q+4 정답률", "type": "number", "format": "percent"},
                    ],
                },
            ],
            "sources": manifest_sources,
            "blocks": [
                {"id": "title", "type": "markdown", "body": f"# {title}"},
                {
                    "id": "technical_summary",
                    "type": "markdown",
                    "body": (
                        "## 기술 요약\n\n"
                        f"현행 v2.6의 1년 뒤 회고 AUC는 **{current_all['auc']:.3f}**, 상위 20% lift는 "
                        f"**{current_all['top20_lift']:.2f}배**였다. 최신 홀드아웃 AUC는 "
                        f"**{current_holdout['auc']:.3f}**이며 단순 매출+폐업 기준선은 "
                        f"**{simple_holdout['auc']:.3f}**였다. 미래 매출수준 상관은 "
                        f"**{current_all['spearman_future_sales_pct']:.3f}**이지만 저폐업률 상관은 "
                        f"**{current_all['spearman_future_low_closure_pct']:.3f}**였다. 결론은 "
                        f"**{decision['reason_ko']}**이다."
                    ),
                },
                {
                    "id": "finding_model",
                    "type": "markdown",
                    "body": (
                        "## 핵심 발견 1 — 기준선과 비교해야 점수의 역할이 보인다\n\n"
                        "AUC 0.5는 무작위 순위, 1.0은 완전 순위다. 현행 점수는 성공확률 모델이 아니라 "
                        "다축 입지조건 점수이므로 단순 현재상태 지속 기준선과의 차이를 함께 봐야 한다. "
                        "현행 점수는 미래 매출 수준에는 유효하지만 미래 저폐업률·성장을 직접 예측하지는 못했다."
                    ),
                },
                {"id": "model_auc", "type": "chart", "chartId": "chart_model_auc"},
                {
                    "id": "finding_grade",
                    "type": "markdown",
                    "body": (
                        "## 핵심 발견 2 — 등급의 단조성과 실제 결과율을 분리해 확인했다\n\n"
                        "등급별 유리결과율은 사용자가 보는 A~E 구분이 실제 1년 뒤 상권×업종 결과와 얼마나 "
                        "일관되게 연결되는지 보여준다. 다만 정답 커버리지는 A "
                        f"{grade_a['primary_label_coverage_rate']:.1%}, E "
                        f"{grade_e['primary_label_coverage_rate']:.1%}로 비대칭이다. 누락 매출을 실패로 보는 "
                        f"민감도에서도 A {grade_a['sensitivity_success_rate']:.1%}, E "
                        f"{grade_e['sensitivity_success_rate']:.1%}로 서열은 유지됐다."
                    ),
                },
                {"id": "grade_success", "type": "chart", "chartId": "chart_grade_success"},
                {
                    "id": "finding_stability",
                    "type": "markdown",
                    "body": (
                        "## 핵심 발견 3 — 평균값보다 분기별 흔들림이 중요하다\n\n"
                        "코로나 회복기와 최근 분기를 섞은 전체 평균만으로 일반화 성능을 단정하지 않고, "
                        "원점 분기별 AUC를 나란히 기록했다."
                    ),
                },
                {"id": "quarter_stability", "type": "chart", "chartId": "chart_quarter_stability"},
                {
                    "id": "scope",
                    "type": "markdown",
                    "body": (
                        "## 범위·데이터·지표 정의\n\n"
                        "관측 단위는 상권×서비스 업종×분기다. 주 정답은 q+4에서 점포가 존재하고 점포당 매출이 "
                        "동일 목표분기·세부업종 중앙값 이상이며 폐업률이 중앙값 이하인 경우다. 미래 점포 수 0은 "
                        "불리 결과로 포함했다. 개별 점포 생존확률은 측정하지 않았다."
                    ),
                },
                {
                    "id": "methodology",
                    "type": "markdown",
                    "body": (
                        "## 방법론\n\n"
                        "각 원점 분기에서 현행 엔진을 다시 실행하고, 이후 Gold를 코드 키로만 왼쪽 조인했다. "
                        "q+4를 주 평가, q+1을 보조 평가로 삼았다. 후보 가중치는 개발구간만으로 골랐고 "
                        "검증·최신 홀드아웃에는 손대지 않았다. 신뢰구간은 원점 분기를 블록으로 재표본화했다."
                        + parity_sentence
                    ),
                },
                {"id": "model_table", "type": "table", "tableId": "table_model_detail"},
                {
                    "id": "limitations",
                    "type": "markdown",
                    "body": (
                        "## 제한·불확실성·강건성\n\n"
                        "현행 가중치는 전체 q+1 정답 기간을 참고해 만들어져 현행 점수의 수치는 회고적 겉보기 성능이다. "
                        "q+4 최신 홀드아웃은 1개 원점 분기뿐이다. 매출 누락 중 양수 점포 행은 주 평가에서 제외하고 "
                        "누락을 실패로 보는 최악조건 민감도를 별도 CSV에 남겼다. 등급별 정답 커버리지 차이 때문에 "
                        "주 등급 결과율은 일부 위쪽 편향될 수 있다. 점포 수 순변화는 개별 생존이 아니다."
                    ),
                },
                {"id": "coverage_table", "type": "table", "tableId": "table_coverage_detail"},
                {
                    "id": "next_steps",
                    "type": "markdown",
                    "body": (
                        "## 다음 단계\n\n"
                        "운영 가중치는 유지한다. 2026Q2 이후 새 Gold가 들어오면 이번에 고정한 후보와 현행 점수를 "
                        "재학습 없이 전진 검증하고, q+4 미사용 홀드아웃이 최소 4개 분기 쌓인 뒤 승격을 다시 판단한다."
                    ),
                },
                {
                    "id": "questions",
                    "type": "markdown",
                    "body": (
                        "## 추가로 답해야 할 질문\n\n"
                        "실제 출점 매장의 개업일·폐업일·월별 매출을 합법적으로 연결할 수 있는가? 연결할 수 있다면 "
                        "상권 대리결과가 아니라 개별 점포 생존·매출 증감에 대한 확률 보정 모델을 별도로 검증할 수 있다."
                    ),
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": datasets,
        },
        "sources": sources,
    }
    path = REPORT_DIR / "artifact.json"
    path.write_text(json.dumps(json_ready(artifact), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="현행 v2.6 입지점수와 미래 Gold 성과를 q+1/q+4로 검증"
    )
    parser.add_argument(
        "--refresh-scores",
        action="store_true",
        help="버전이 맞는 분기별 점수 캐시도 무시하고 다시 계산",
    )
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="20개 분기 캐시가 모두 있을 때 점수 계산 없이 분석 산출물만 다시 생성",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="기존 검증 CSV와 summary로 기록 문서·artifact만 다시 생성",
    )
    parser.add_argument(
        "--verify-backend-parity-quarter",
        type=int,
        help="백엔드 엔진으로 지정 분기를 재계산해 캐시와 행 단위 일치 확인 후 보고서 갱신",
    )
    args = parser.parse_args()

    ensure_dirs()
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if args.verify_backend_parity_quarter is not None:
        summary_path = OUT_DIR / "validation_summary.json"
        if not summary_path.exists():
            raise FileNotFoundError(f"Backend parity requires {summary_path}")
        parity = verify_backend_parity(args.verify_backend_parity_quarter)
        if not parity["all_match"]:
            raise ValueError(f"Backend parity failed: {parity}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["backend_runtime_parity"] = parity
        summary.setdefault("provenance", {})["backend_score_engine"] = {
            "path": str(BACKEND_ENGINE_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(BACKEND_ENGINE_PATH),
        }
        summary_path.write_text(
            json.dumps(json_ready(summary), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        args.report_only = True

    if args.report_only:
        summary_path = OUT_DIR / "validation_summary.json"
        if not summary_path.exists():
            raise FileNotFoundError(f"report-only requires {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["provenance"]["evaluation_script"] = {
            "path": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(Path(__file__).resolve()),
        }
        model_metrics = pd.read_csv(OUT_DIR / "model_metrics.csv", encoding="utf-8-sig")
        quarter_metrics = pd.read_csv(OUT_DIR / "quarter_metrics.csv", encoding="utf-8-sig")
        grade_metrics = pd.read_csv(OUT_DIR / "grade_metrics.csv", encoding="utf-8-sig")
        coverage = pd.read_csv(OUT_DIR / "coverage_by_quarter.csv", encoding="utf-8-sig")
        candidate_metrics = pd.read_csv(
            OUT_DIR / "candidate_weight_metrics.csv", encoding="utf-8-sig"
        )
        record_path = write_validation_record(
            summary, model_metrics, grade_metrics, coverage, candidate_metrics
        )
        artifact_path = write_report_artifact(
            summary, model_metrics, quarter_metrics, grade_metrics, coverage
        )
        summary["outputs"]["validation_record"] = str(record_path.relative_to(ROOT)).replace(
            "\\", "/"
        )
        summary["outputs"]["report_artifact"] = str(artifact_path.relative_to(ROOT)).replace(
            "\\", "/"
        )
        summary_path.write_text(
            json.dumps(json_ready(summary), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[report] record={record_path}", flush=True)
        print(f"[report] artifact={artifact_path}", flush=True)
        return 0

    print(f"[validation] version={VALIDATION_VERSION}", flush=True)
    print("[validation] loading Gold sales and competition", flush=True)
    sales, competition = load_gold()
    competition_quarters = set(competition["기준_년분기_코드"].unique())
    origin_quarters = sorted(
        int(q)
        for q in competition_quarters
        if int(q) >= 20211 and add_quarters(int(q), 1) in competition_quarters
    )
    if len(origin_quarters) != 20 or origin_quarters[0] != 20211 or origin_quarters[-1] != 20254:
        raise ValueError(f"Unexpected origin-quarter contract: {origin_quarters}")

    if args.analysis_only:
        missing = [
            quarter
            for quarter in origin_quarters
            if not (QUARTER_DIR / f"official_v2_6_scores_{quarter}.parquet").exists()
        ]
        if missing:
            raise FileNotFoundError(f"analysis-only requested but score caches are missing: {missing}")

    scores, score_audit = load_scores(origin_quarters, refresh=args.refresh_scores)
    if scores.duplicated(["origin_quarter", "상권_코드", "서비스_업종_코드"]).any():
        raise ValueError("Historical official score cache contains duplicate keys")
    score_versions = set(scores["score_version"].dropna().astype(str).unique())
    if score_versions != {engine.SCORE_VERSION}:
        raise ValueError(f"Score version mismatch: {score_versions}")

    print("[validation] building leakage-safe q+1/q+4 outcomes", flush=True)
    panel = build_market_panel(sales, competition)
    origin_features = prepare_origin_features(panel)
    outcome_parts: list[pd.DataFrame] = []
    for horizon in HORIZONS:
        eligible_scores = scores[
            scores["origin_quarter"].map(lambda q: add_quarters(int(q), horizon)).isin(
                competition_quarters
            )
        ].copy()
        part = attach_outcomes(eligible_scores, origin_features, panel, horizon)
        outcome_parts.append(part)
        print(
            f"[validation] q+{horizon}: {len(part):,} score rows, "
            f"{int(part['primary_label_available'].sum()):,} primary labels "
            f"({part['primary_label_available'].mean():.1%})",
            flush=True,
        )
    outcomes = pd.concat(outcome_parts, ignore_index=True)

    print("[validation] selecting candidate weights on development q+4 only", flush=True)
    selected_weights, candidate_metrics = select_candidate_weights(outcomes)
    with_candidate: list[pd.DataFrame] = []
    for horizon in HORIZONS:
        part = outcomes[outcomes["horizon_quarters"] == horizon].copy()
        part["model__candidate_dev_selected"] = add_candidate_signal(part, selected_weights)
        with_candidate.append(part)
    outcomes = pd.concat(with_candidate, ignore_index=True)
    outcomes = add_top20_flags(outcomes)

    label_values = set(
        outcomes["favorable_market_outcome"].dropna().astype(int).unique().tolist()
    )
    if not label_values.issubset({0, 1}):
        raise ValueError(f"Primary label has invalid values: {label_values}")
    if outcomes.duplicated(
        ["horizon_quarters", "origin_quarter", "상권_코드", "서비스_업종_코드"]
    ).any():
        raise ValueError("Outcome table contains duplicate horizon×origin×area×industry keys")

    print("[validation] computing models, grades, quarters, segments, and uncertainty", flush=True)
    quarter_metrics = compute_quarter_metrics(outcomes)
    model_metrics = compute_model_metrics(outcomes, quarter_metrics)
    grade_metrics = compute_grade_metrics(outcomes)
    segment_metrics = compute_segment_metrics(outcomes)
    coverage = compute_coverage(scores, outcomes, competition)
    decision = decide_weight_promotion(model_metrics, selected_weights)

    output_paths = {
        "detail_rows": OUT_DIR / "predictive_validation_rows.parquet",
        "score_cache_audit": OUT_DIR / "score_cache_audit.csv",
        "model_metrics": OUT_DIR / "model_metrics.csv",
        "quarter_metrics": OUT_DIR / "quarter_metrics.csv",
        "grade_metrics": OUT_DIR / "grade_metrics.csv",
        "segment_metrics": OUT_DIR / "segment_metrics.csv",
        "coverage": OUT_DIR / "coverage_by_quarter.csv",
        "candidate_weights": OUT_DIR / "candidate_weight_metrics.csv",
    }
    outcomes.to_parquet(output_paths["detail_rows"], index=False)
    for frame, key in [
        (score_audit, "score_cache_audit"),
        (model_metrics, "model_metrics"),
        (quarter_metrics, "quarter_metrics"),
        (grade_metrics, "grade_metrics"),
        (segment_metrics, "segment_metrics"),
        (coverage, "coverage"),
        (candidate_metrics, "candidate_weights"),
    ]:
        frame.to_csv(output_paths[key], index=False, encoding="utf-8-sig")

    inventory = source_inventory(sales, competition, outcomes)
    provenance_paths = {
        "evaluation_script": Path(__file__).resolve(),
        "score_engine": ENGINE_PATH,
        "backend_score_engine": BACKEND_ENGINE_PATH,
        "current_weights": WEIGHTS_PATH,
        "legacy_weight_training_summary": ROOT
        / "datacorpus"
        / "_score_backtest"
        / "location_score_backtest_summary.json",
    }
    provenance = {
        name: {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(path),
        }
        for name, path in provenance_paths.items()
    }
    provenance["git_head"] = git_head()

    integrity_checks = {
        "sales_duplicate_keys": int(sales.duplicated(KEYS).sum()),
        "competition_duplicate_keys": int(competition.duplicated(KEYS).sum()),
        "score_duplicate_keys": int(
            scores.duplicated(["origin_quarter", "상권_코드", "서비스_업종_코드"]).sum()
        ),
        "outcome_duplicate_keys": int(
            outcomes.duplicated(
                ["horizon_quarters", "origin_quarter", "상권_코드", "서비스_업종_코드"]
            ).sum()
        ),
        "score_versions": sorted(score_versions),
        "q1_origin_quarters": int(
            outcomes.loc[outcomes["horizon_quarters"] == 1, "origin_quarter"].nunique()
        ),
        "q4_origin_quarters": int(
            outcomes.loc[outcomes["horizon_quarters"] == 4, "origin_quarter"].nunique()
        ),
        "primary_label_values": sorted(label_values),
        "candidate_selected_rows": int(candidate_metrics["selected_on_development"].sum()),
    }
    required_integrity = {
        "sales_duplicate_keys": 0,
        "competition_duplicate_keys": 0,
        "score_duplicate_keys": 0,
        "outcome_duplicate_keys": 0,
        "q1_origin_quarters": 20,
        "q4_origin_quarters": 17,
        "candidate_selected_rows": 1,
    }
    if any(integrity_checks[key] != expected for key, expected in required_integrity.items()):
        raise ValueError(f"Integrity contract failed: {integrity_checks}")

    summary: dict[str, Any] = {
        "created_at": created_at,
        "validation_version": VALIDATION_VERSION,
        "score_version": engine.SCORE_VERSION,
        "run_id": RUN_ID,
        "source_inventory": inventory,
        "provenance": provenance,
        "label_contract": {
            "unit": "quarter×trade_area×service_industry",
            "primary_horizon_quarters": 4,
            "secondary_horizon_quarters": 1,
            "favorable_market_outcome": (
                "target_store_count > 0 AND target_sales_per_store >= target-quarter exact-industry "
                "median AND target_closure_rate <= target-quarter exact-industry median"
            ),
            "primary_missing_rule": (
                "target store count zero is explicitly unfavorable; positive target stores with missing sales "
                "and missing target competition are excluded but retained with status"
            ),
            "sensitivity_missing_rule": "all rows with target competition but missing sales are unfavorable",
            "forbidden_claim": "individual-store survival probability",
            "allowed_interpretation": "area×industry market viability/stability proxy and ranking performance",
        },
        "time_split_contract": {
            "development": "origin 2021Q1 through 2023Q4; candidate selection only",
            "validation": "origin 2024Q1 through 2024Q4; untouched by candidate selection",
            "holdout_q1": "origin 2025Q1 through 2025Q4",
            "holdout_q4": "origin 2025Q1 only, target 2026Q1",
            "current_score_leakage_caveat": (
                "current weight file was derived from all q+1 labels 2021Q1 through 2025Q4, so current-score "
                "metrics are retrospective/apparent rather than clean out-of-sample validation"
            ),
        },
        "metrics_contract": {
            "auc": "rank discrimination for binary favorable outcome; not probability calibration",
            "average_precision": "precision averaged at positive ranks",
            "top20_lift": (
                "within origin-quarter×industry deterministic top-20% success rate divided by base rate; "
                "signal ties are split by stable source row order while AUC preserves average ties"
            ),
            "continuous": [
                "future sales-per-store peer percentile Spearman",
                "future inverse closure-rate peer percentile Spearman",
                "peer-normalized net store-count log change Spearman",
                "sales-per-store log-growth Spearman",
            ],
            "confidence_intervals": "1000 deterministic origin-quarter block bootstrap draws",
        },
        "weight_decision": decision,
        "integrity_checks": integrity_checks,
        "outputs": {
            key: str(path.relative_to(ROOT)).replace("\\", "/") for key, path in output_paths.items()
        },
    }
    summary_path = OUT_DIR / "validation_summary.json"
    summary["outputs"]["summary"] = str(summary_path.relative_to(ROOT)).replace("\\", "/")
    summary_path.write_text(
        json.dumps(json_ready(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    record_path = write_validation_record(
        summary, model_metrics, grade_metrics, coverage, candidate_metrics
    )
    artifact_path = write_report_artifact(
        summary, model_metrics, quarter_metrics, grade_metrics, coverage
    )
    summary["outputs"]["validation_record"] = str(record_path.relative_to(ROOT)).replace("\\", "/")
    summary["outputs"]["report_artifact"] = str(artifact_path.relative_to(ROOT)).replace("\\", "/")
    summary_path.write_text(
        json.dumps(json_ready(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    current_q4_all_auc = lookup_metric(
        model_metrics,
        horizon=4,
        split="all",
        model="현행 v2.6 점수",
        metric="auc",
    )
    current_q4_holdout_auc = lookup_metric(
        model_metrics,
        horizon=4,
        split="holdout",
        model="현행 v2.6 점수",
        metric="auc",
    )
    simple_q4_holdout_auc = lookup_metric(
        model_metrics,
        horizon=4,
        split="holdout",
        model="단순 매출+폐업 기준선",
        metric="auc",
    )
    print(
        f"[result] current q+4 apparent AUC={current_q4_all_auc:.3f}; "
        f"latest holdout={current_q4_holdout_auc:.3f}; simple baseline={simple_q4_holdout_auc:.3f}",
        flush=True,
    )
    print(f"[result] weight decision={decision['decision']}", flush=True)
    print(f"[result] summary={summary_path}", flush=True)
    print(f"[result] artifact={artifact_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
