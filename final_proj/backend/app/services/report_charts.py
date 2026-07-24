from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from app.services.report_chart_catalog import CHART_TITLES


DISPLAY_GRADES = ("E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+")
GRADE_VALUE = {grade: index for index, grade in enumerate(DISPLAY_GRADES, start=1)}


def _metric_grade(metric: dict[str, Any] | None) -> str | None:
    if not isinstance(metric, dict):
        return None
    for key in ("display_grade", "grade", "display"):
        text = str(metric.get(key) or "").strip().upper()
        match = re.fullmatch(r"([A-E])\s*(\+)?(?:\s*등급)?", text)
        if match:
            return f"{match.group(1)}{match.group(2) or ''}"
    # 공개 등급은 백엔드가 산정해 전달한 값만 사용한다. 과거 raw 점수만
    # 남은 리포트는 임의 경계로 재등급화하지 않고 표시를 보류한다.
    return None


def _metric_raw_optional(metric: dict[str, Any] | None) -> float | None:
    if not isinstance(metric, dict):
        return None
    raw = metric.get("raw")
    if isinstance(raw, dict):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _safe_name(text: Any) -> str:
    value = str(text or "-")
    return value if len(value) <= 12 else value[:11] + "…"


def _quarter_label(value: Any) -> str:
    """Turn the internal YYYYQ quarter code into reader-facing Korean."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})([1-4])", text)
    if not match:
        return text
    return f"{match.group(1)}년 {match.group(2)}분기"


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Malgun Gothic", "NanumGothic", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _finish_figure(fig, source_note: str) -> None:
    fig.text(0.012, 0.012, source_note, fontsize=7.2, color="#64748b", ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.055, 1, 1))


def _save_barh(
    path: Path,
    title: str,
    labels: list[str],
    values: list[float],
    color: str | list[str] = "#2563eb",
    legend_handles: list[tuple[str, str]] | None = None,
    source_note: str = "",
    xlabel: str = "",
    value_suffix: str = "",
) -> None:
    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(8.4, 4.4), dpi=150)
    if labels:
        ax.barh(labels, values, color=color)
        ax.invert_yaxis()
        ax.margins(x=0.16)
        for idx, value in enumerate(values):
            ax.text(value, idx, f" {value:.1f}{value_suffix}", va="center", fontsize=8)
    else:
        ax.text(
            0.5,
            0.5,
            "표시 가능한 데이터 없음",
            transform=ax.transAxes,
            ha="center",
            va="center",
            color="#64748b",
        )
    ax.set_title(title, fontsize=13, weight="bold")
    if xlabel:
        ax.set_xlabel(xlabel)
    ax.grid(axis="x", alpha=0.25)
    if legend_handles:
        from matplotlib.patches import Patch

        ax.legend(handles=[Patch(facecolor=c, label=t) for t, c in legend_handles], fontsize=8, loc="lower right")
    _finish_figure(fig, source_note)
    fig.savefig(path)
    plt.close(fig)


def _save_grade_barh(
    path: Path,
    title: str,
    labels: list[str],
    grades: list[str],
    color: str | list[str] = "#2563eb",
    legend_handles: list[tuple[str, str]] | None = None,
    source_note: str = "",
) -> None:
    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(8.4, 4.4), dpi=150)
    values = [GRADE_VALUE[grade] for grade in grades]
    if labels:
        ax.barh(labels, values, color=color)
        ax.invert_yaxis()
        for idx, (value, grade) in enumerate(zip(values, grades, strict=False)):
            ax.text(value, idx, f" {grade}", va="center", fontsize=8, weight="bold")
    else:
        ax.text(
            0.5,
            0.5,
            "표시 가능한 등급 없음",
            transform=ax.transAxes,
            ha="center",
            va="center",
            color="#64748b",
        )
    ax.set_xlim(0, len(DISPLAY_GRADES) + 0.8)
    ax.set_xticks(range(1, len(DISPLAY_GRADES) + 1), DISPLAY_GRADES)
    ax.set_title(title, fontsize=13, weight="bold")
    ax.grid(axis="x", alpha=0.25)
    if legend_handles:
        from matplotlib.patches import Patch

        ax.legend(handles=[Patch(facecolor=c, label=t) for t, c in legend_handles], fontsize=8, loc="lower right")
    _finish_figure(fig, source_note)
    fig.savefig(path)
    plt.close(fig)


def _save_line(
    path: Path,
    title: str,
    labels: list[str],
    values: list[float],
    ylabel: str,
    source_note: str = "",
) -> None:
    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(8.4, 4.4), dpi=150)
    if labels:
        ax.plot(labels, values, marker="o", color="#0f766e", linewidth=2)
    else:
        ax.text(
            0.5,
            0.5,
            "표시 가능한 데이터 없음",
            transform=ax.transAxes,
            ha="center",
            va="center",
            color="#64748b",
        )
    ax.set_title(title, fontsize=13, weight="bold")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.25)
    ax.tick_params(axis="x", rotation=35)
    _finish_figure(fig, source_note)
    fig.savefig(path)
    plt.close(fig)


def _save_grade_grouped(
    path: Path,
    title: str,
    labels: list[str],
    score_grades: list[str | None],
    cost_grades: list[str | None],
    highlight_index: int | None = None,
    source_note: str = "",
) -> None:
    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(8.8, 4.6), dpi=150)
    x = list(range(len(labels)))
    width = 0.36
    score_colors = (
        ["#1d4ed8" if i == highlight_index else "#93c5fd" for i in x]
        if highlight_index is not None
        else "#2563eb"
    )
    score_values = [GRADE_VALUE.get(grade or "", math.nan) for grade in score_grades]
    cost_values = [GRADE_VALUE.get(grade or "", math.nan) for grade in cost_grades]
    ax.bar([i - width / 2 for i in x], score_values, width=width, label="입지 등급", color=score_colors)
    ax.bar([i + width / 2 for i in x], cost_values, width=width, label="비용 여건 등급", color="#f97316")
    ax.set_xticks(x)
    tick_labels = list(labels)
    if highlight_index is not None and 0 <= highlight_index < len(tick_labels):
        tick_labels[highlight_index] = f"{tick_labels[highlight_index]}\n(대상)"
    ax.set_xticklabels(tick_labels, rotation=25, ha="right")
    if highlight_index is not None and 0 <= highlight_index < len(x):
        tick = ax.get_xticklabels()[highlight_index]
        tick.set_fontweight("bold")
        tick.set_color("#1d4ed8")
    for idx, (score_value, cost_value, score_grade, cost_grade) in enumerate(
        zip(score_values, cost_values, score_grades, cost_grades, strict=False)
    ):
        missing = []
        if not math.isfinite(score_value):
            missing.append("입지 등급 없음")
        else:
            ax.text(idx - width / 2, score_value, str(score_grade), ha="center", va="bottom", fontsize=7, weight="bold")
        if not math.isfinite(cost_value):
            missing.append("비용 여건 등급 없음")
        else:
            ax.text(idx + width / 2, cost_value, str(cost_grade), ha="center", va="bottom", fontsize=7, weight="bold")
        if missing:
            ax.text(idx, 0.35, "\n".join(missing), ha="center", va="bottom", fontsize=7, color="#64748b")
    ax.set_title(title, fontsize=13, weight="bold")
    ax.set_ylim(0, len(DISPLAY_GRADES) + 0.8)
    ax.set_yticks(range(1, len(DISPLAY_GRADES) + 1), DISPLAY_GRADES)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    _finish_figure(fig, source_note)
    fig.savefig(path)
    plt.close(fig)


def render_report_charts(report_data: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = ((report_data.get("indicator_pack") or {}).get("facts_pack") or {})
    score_block = facts.get("score_block") or {}
    sales_block = facts.get("sales_block") or {}
    demand_block = facts.get("demand_block") or {}
    alternatives = facts.get("alternatives") or []

    paths: dict[str, str] = {}

    axis_scores = score_block.get("axis_scores") or {}
    support = score_block.get("supporting_signals") or {}
    official_color, support_color = "#2563eb", "#94a3b8"
    metric_specs = [
        ("시장성", axis_scores.get("sales"), official_color),
        ("경쟁 구조", axis_scores.get("competition"), official_color),
        ("수요 기반", axis_scores.get("demand"), official_color),
        ("접근·유입", axis_scores.get("accessibility"), official_color),
        ("비용 여건", support.get("cost_risk_score"), support_color),
        ("데이터 신뢰", support.get("data_reliability_score"), support_color),
        ("성장 안정", support.get("growth_potential_score"), support_color),
    ]
    available_metrics = [
        (label, grade, color)
        for label, metric, color in metric_specs
        if (grade := _metric_grade(metric)) is not None
    ]
    path = out_dir / "C1.png"
    if available_metrics:
        _save_grade_barh(
            path,
            CHART_TITLES["C1"],
            [label for label, _, _ in available_metrics],
            [grade for _, grade, _ in available_metrics],
            color=[color for _, _, color in available_metrics],
            legend_handles=[("입지 평가 축", official_color), ("보조 지표", support_color)],
            source_note="출처: 입지분석 모델. 입지 평가는 A+~E 등급으로 표시하며 결측 축은 제외함.",
        )
        paths["C1"] = str(path)
    else:
        path.unlink(missing_ok=True)

    trend = sorted(sales_block.get("sales_trend") or [], key=lambda item: item.get("timestamp", ""))
    available_trend = [
        (_quarter_label(item.get("timestamp")), value / 100_000_000)
        for item in trend
        if (value := _metric_raw_optional(item.get("sales_amount"))) is not None
    ]
    path = out_dir / "C2.png"
    if available_trend:
        _save_line(
            path,
            CHART_TITLES["C2"],
            [label for label, _ in available_trend],
            [value for _, value in available_trend],
            "억원",
            source_note="출처: 서울시 우리마을가게 상권분석서비스 추정매출-상권.",
        )
        paths["C2"] = str(path)
    else:
        path.unlink(missing_ok=True)

    top = sales_block.get("area_top_industries") or []
    available_top = [
        (_safe_name(item.get("industry_name")), value / 100_000_000)
        for item in top
        if (value := _metric_raw_optional(item.get("sales_amount"))) is not None
    ]
    path = out_dir / "C3.png"
    if available_top:
        _save_barh(
            path,
            CHART_TITLES["C3"],
            [label for label, _ in available_top],
            [value for _, value in available_top],
            color="#7c3aed",
            source_note="출처: 서울시 우리마을가게 상권분석서비스 추정매출-상권. 업종 간 단순 매출 비교이며 추천 순위가 아님.",
            xlabel="매출액(억원)",
            value_suffix="억원",
        )
        paths["C3"] = str(path)
    else:
        path.unlink(missing_ok=True)

    target = facts.get("target") or {}
    alt_labels = [_safe_name(target.get("area_name") or "대상")]
    score_grades = [_metric_grade(score_block.get("current_location_score"))]
    cost_grades = [_metric_grade((facts.get("cost_block") or {}).get("cost_risk_score"))]
    for item in alternatives[:5]:
        alt_labels.append(_safe_name(item.get("area_name")))
        score_grades.append(_metric_grade(item.get("current_location_score")))
        cost_grades.append(_metric_grade(item.get("cost_risk_score")))
    path = out_dir / "C4.png"
    _save_grade_grouped(
        path,
        CHART_TITLES["C4"],
        alt_labels,
        score_grades,
        cost_grades,
        highlight_index=0,
        source_note="출처: 입지분석 모델. 입지 평가는 A+~E 등급으로 표시하며 자료가 없으면 막대를 비워 둠.",
    )
    paths["C4"] = str(path)

    floating_daily_metric = demand_block.get("floating_population_daily_average")
    demand_specs = [
        ("상주인구", demand_block.get("resident_population")),
        ("직장인구", demand_block.get("worker_population")),
        (
            "유동인구(일평균)" if floating_daily_metric else "유동인구(분기합계)",
            floating_daily_metric or demand_block.get("floating_population"),
        ),
    ]
    available_demand = [
        (label, value / 10_000)
        for label, metric in demand_specs
        if (value := _metric_raw_optional(metric)) is not None
    ]
    path = out_dir / "C5.png"
    if available_demand:
        _save_barh(
            path,
            CHART_TITLES["C5"],
            [label for label, _ in available_demand],
            [value for _, value in available_demand],
            color="#059669",
            source_note=(
                "출처: 서울시 상권분석서비스. 유동인구는 분기 누계를 해당 분기 일수로 나눈 일평균."
                if floating_daily_metric
                else "출처: 서울시 상권분석서비스. 기존 저장 리포트의 유동인구는 분기 합계."
            ),
            xlabel="인구(만 명)",
            value_suffix="만 명",
        )
        paths["C5"] = str(path)
    else:
        path.unlink(missing_ok=True)

    return paths
