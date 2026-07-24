# -*- coding: utf-8 -*-
"""
64. R-ONE/RTMS 비용 프록시 권역-상권 매핑 검증.

목적:
  - RTMS 상업·업무용 매매 실거래 프록시가 자치구 grain으로 상권에 fan-out된 사실을 검증한다.
  - R-ONE 상가 임대/공실/권리금 통계는 서울/권역/상권명 후보 기준선으로만 보존하고,
    월세·권리금 직접값 또는 개별 점포 수익성으로 승격하지 않는다.
  - 검증 결과와 후보 매핑 산출물을 전처리 전 기록으로 남긴다.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_OUT = ROOT / "datacorpus" / "_rule_validation"
MD_OUT = ROOT / "research" / "rule_validation"
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"

VERSION = "cost_proxy_area_mapping.v0.1-20260707"
CANDIDATE_VERSION = "rone_region_trade_area_candidate.v0.1-20260707"
RUN_DATE = "2026-07-07"


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def row(validation_id: str, name: str, observed: Any, expected: Any, ok: bool, reason_ko: str) -> dict[str, Any]:
    return {
        "validation_id": validation_id,
        "validation_name": name,
        "observed": clean_value(observed),
        "expected": expected,
        "result": "PASS" if ok else "FAIL",
        "reason_ko": reason_ko,
    }


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, item in df.iterrows():
        values = []
        for col in cols:
            text = "" if pd.isna(item[col]) else str(item[col])
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def normalize_name(value: Any) -> str:
    text = "" if value is None or pd.isna(value) else str(value)
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[\s·ㆍ\-_]", "", text)
    return text.lower()


def terminal_region(full_region: str) -> str:
    return str(full_region).split(">")[-1].strip()


def terminal_terms(name: str) -> list[str]:
    parts = re.split(r"[/,·ㆍ]", str(name))
    out = []
    for part in parts:
        norm = normalize_name(part)
        if len(norm) >= 2 and norm not in {"서울", "기타", "도심", "강남", "전체"}:
            out.append(norm)
    return sorted(set(out))


def load_engine():
    spec = importlib.util.spec_from_file_location("build_rule_based_location_scores", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"엔진 모듈을 불러올 수 없습니다: {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_rone_candidate(profile: pd.DataFrame, rone: pd.DataFrame) -> pd.DataFrame:
    profile_small = profile[
        ["상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드_명"]
    ].drop_duplicates("상권_코드").copy()
    profile_small["_area_norm"] = profile_small["상권_코드_명"].map(normalize_name)
    profile_small["_dong_norm"] = profile_small["행정동_코드_명"].map(normalize_name)

    rone = rone.copy()
    rone["rone_terminal_region"] = rone["지역_전체명"].map(terminal_region)
    rows: list[pd.DataFrame] = []

    # 서울 전체 기준선: 전체 상권에 붙일 수 있지만 공간해상도는 매우 낮다.
    seoul_rows = rone[rone["지역_전체명"] == "서울"].copy()
    if not seoul_rows.empty:
        seoul_join = profile_small.merge(seoul_rows, how="cross")
        seoul_join["mapping_scope"] = "seoul_baseline_reference"
        seoul_join["mapping_method"] = "서울 전체 기준선 fan-out"
        seoul_join["mapping_confidence"] = "low"
        rows.append(seoul_join)

    # 3단계 R-ONE 지역명 후보: 상권명 또는 행정동명에 말단 지역명이 포함될 때만 후보로 둔다.
    level3 = rone[(rone["지역_레벨"] == "3단계") & rone["지역_전체명"].astype(str).str.startswith("서울>")].copy()
    candidate_frames: list[pd.DataFrame] = []
    for _, region_row in level3.drop_duplicates("지역_전체명").iterrows():
        terminal = terminal_region(region_row["지역_전체명"])
        terms = terminal_terms(terminal)
        if not terms:
            continue
        mask = pd.Series(False, index=profile_small.index)
        for term in terms:
            mask = mask | profile_small["_area_norm"].str.contains(term, regex=False, na=False)
            mask = mask | profile_small["_dong_norm"].str.contains(term, regex=False, na=False)
        matched_profile = profile_small[mask].copy()
        if matched_profile.empty:
            continue
        region_values = level3[level3["지역_전체명"] == region_row["지역_전체명"]].copy()
        matched = matched_profile.merge(region_values, how="cross")
        matched["mapping_scope"] = "rone_level3_name_match_candidate"
        matched["mapping_method"] = f"상권명/행정동명 말단지역 후보매칭: {terminal}"
        matched["mapping_confidence"] = "candidate_review_required"
        candidate_frames.append(matched)
    if candidate_frames:
        rows.append(pd.concat(candidate_frames, ignore_index=True))

    if not rows:
        return pd.DataFrame()

    out = pd.concat(rows, ignore_index=True)
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = True
    out["engine_promotion_ready"] = False
    out["candidate_version"] = CANDIDATE_VERSION
    out["mapping_use_note_ko"] = (
        "R-ONE 통계는 서울/권역/상권명 후보 기준선이다. 개별 상권 월세·권리금 직접값이나 수익성 확정값이 아니다."
    )
    keep = [
        "상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드_명",
        "mapping_scope", "mapping_method", "mapping_confidence",
        "selection_group", "STATBL_ID", "STATBL_NM", "상가유형", "DTACYCLE_CD",
        "WRTTIME_IDTFR_ID", "WRTTIME_DESC", "기준_년분기_코드", "기준_연도",
        "지역_전체명", "지역_레벨", "rone_terminal_region", "CLS_NM", "ITM_NM", "DTA_VAL", "UI_NM",
        "selection_reason_ko", "directness_level", "forbidden_claim_ko", "source_id",
        "direct_score_allowed", "proxy_score_allowed", "engine_promotion_ready",
        "candidate_version", "mapping_use_note_ko",
    ]
    return out[[c for c in keep if c in out.columns]].drop_duplicates()


def main() -> int:
    RULE_OUT.mkdir(parents=True, exist_ok=True)
    MD_OUT.mkdir(parents=True, exist_ok=True)

    profile = pd.read_csv(GOLD / "gold_trade_area_profile.csv", encoding="utf-8-sig", dtype=str, low_memory=False)
    cost = pd.read_csv(GOLD / "gold_cost_risk_q_area.csv", encoding="utf-8-sig", dtype=str, low_memory=False)
    rtms = pd.read_csv(SILVER / "silver_rtms_commercial_trade_sgg_quarter.csv", encoding="utf-8-sig", dtype=str, low_memory=False)
    rone = pd.read_csv(SILVER / "silver_reb_rone_seoul_cost_proxy_latest.csv", encoding="utf-8-sig", dtype=str, low_memory=False)

    numeric_cols = [
        "거래건수", "포함_월수", "거래금액_중앙값_만원", "거래금액_평균_만원",
        "건물면적당_거래금액_중앙값_만원_per_m2", "건물면적당_거래금액_평균_만원_per_m2",
    ]
    for frame in [cost, rtms]:
        for col in numeric_cols:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")

    rone_candidate = build_rone_candidate(profile, rone)
    candidate_path = GOLD / "gold_cost_risk_rone_region_trade_area_candidate.csv"
    rone_candidate.to_csv(candidate_path, index=False, encoding="utf-8-sig")

    validations: list[dict[str, Any]] = []
    area_count = profile["상권_코드"].nunique()
    quarter_count = rtms["기준_년분기_코드"].nunique()
    expected_cost_rows = area_count * quarter_count
    validations.append(row(
        "64-V01",
        "RTMS 상권 fan-out 커버리지",
        len(cost),
        expected_cost_rows,
        len(cost) == expected_cost_rows and cost["상권_코드"].nunique() == area_count,
        "RTMS 비용 gold는 상권 직접값이 아니라 자치구 분기값을 전체 상권에 펼친 구조여야 한다.",
    ))

    cost_unique = cost.drop_duplicates(["기준_년분기_코드", "자치구_코드"])[
        ["기준_년분기_코드", "자치구_코드", "건물면적당_거래금액_중앙값_만원_per_m2", "거래건수"]
    ]
    rtms_key = rtms[
        ["기준_년분기_코드", "자치구_코드", "건물면적당_거래금액_중앙값_만원_per_m2", "거래건수"]
    ].rename(columns={
        "건물면적당_거래금액_중앙값_만원_per_m2": "rtms_건물면적당_거래금액_중앙값_만원_per_m2",
        "거래건수": "rtms_거래건수",
    })
    cmp = cost_unique.merge(rtms_key, on=["기준_년분기_코드", "자치구_코드"], how="outer", indicator=True)
    missing_map = int((cmp["_merge"] != "both").sum())
    value_diff = (
        cmp["건물면적당_거래금액_중앙값_만원_per_m2"] -
        cmp["rtms_건물면적당_거래금액_중앙값_만원_per_m2"]
    ).abs()
    max_value_diff = float(value_diff.dropna().max()) if value_diff.notna().any() else 0.0
    validations.append(row(
        "64-V02",
        "RTMS silver-gold 자치구값 일치",
        f"missing={missing_map}; max_diff={round(max_value_diff, 8)}",
        "missing=0; max_diff=0",
        missing_map == 0 and max_value_diff <= 1e-8,
        "gold_cost_risk_q_area는 silver_rtms_commercial_trade_sgg_quarter의 자치구 분기값을 변형 없이 참조해야 한다.",
    ))

    fanout_unique = (
        cost.groupby(["기준_년분기_코드", "자치구_코드"])["건물면적당_거래금액_중앙값_만원_per_m2"]
        .nunique(dropna=False)
    )
    max_unique = int(fanout_unique.max()) if len(fanout_unique) else 0
    validations.append(row(
        "64-V03",
        "자치구 fan-out grain 보존",
        max_unique,
        1,
        max_unique == 1,
        "같은 분기·자치구의 모든 상권은 같은 RTMS 비용 프록시값을 가져야 한다. 달라지면 상권 직접값처럼 오염된 것이다.",
    ))

    direct_flags = sorted(cost["direct_score_allowed"].astype(str).str.lower().unique().tolist())
    proxy_flags = sorted(cost["proxy_score_allowed"].astype(str).str.lower().unique().tolist())
    validations.append(row(
        "64-V04",
        "RTMS 직접값/프록시 플래그",
        f"direct={direct_flags}; proxy={proxy_flags}",
        "direct all false; proxy all true",
        direct_flags == ["false"] and proxy_flags == ["true"],
        "RTMS 매매 실거래는 비용 압력 프록시일 뿐 월세·권리금 직접값이 아니다.",
    ))

    forbidden_join = " ".join(cost["forbidden_claim_ko"].dropna().astype(str).unique().tolist())
    validations.append(row(
        "64-V05",
        "RTMS 금지문구 명시",
        forbidden_join[:120],
        "임대료/권리금 직접값 아님",
        "임대료" in forbidden_join and "권리금" in forbidden_join and "직접값" in forbidden_join,
        "비용 리스크가 수익성 확정 표현으로 번지지 않게 금지문구를 gold에 남긴다.",
    ))

    rone_levels = sorted(rone["지역_레벨"].dropna().unique().tolist())
    rone_groups = sorted(rone["selection_group"].dropna().unique().tolist())
    rone_forbidden = " ".join(rone["forbidden_claim_ko"].dropna().astype(str).unique().tolist())
    validations.append(row(
        "64-V06",
        "R-ONE 권역/상가유형 기준선 존재",
        f"levels={rone_levels}; groups={len(rone_groups)}",
        "1단계/2단계/3단계 및 다중 selection_group",
        all(x in rone_levels for x in ["1단계", "2단계", "3단계"]) and len(rone_groups) >= 4,
        "R-ONE은 자치구 직접값이 아니라 서울/권역/상권분류·상가유형 기준선으로 봐야 한다.",
    ))

    validations.append(row(
        "64-V07",
        "R-ONE 금지문구 명시",
        rone_forbidden[:120],
        "개별 점포 월세/권리금/수익성 확정값 아님",
        "개별 점포" in rone_forbidden and "월세" in rone_forbidden and "권리금" in rone_forbidden and "수익성" in rone_forbidden,
        "R-ONE도 임대료·권리금 참고선일 뿐 개별 점포 수익성 판단으로 쓰면 안 된다.",
    ))

    candidate_scope_counts = rone_candidate["mapping_scope"].value_counts(dropna=False).to_dict() if not rone_candidate.empty else {}
    candidate_direct = sorted(rone_candidate["direct_score_allowed"].astype(str).str.lower().unique().tolist()) if not rone_candidate.empty else []
    candidate_promotion = sorted(rone_candidate["engine_promotion_ready"].astype(str).str.lower().unique().tolist()) if not rone_candidate.empty else []
    validations.append(row(
        "64-V08",
        "R-ONE 후보 매핑 산출",
        f"rows={len(rone_candidate)}; scopes={candidate_scope_counts}",
        "서울기준선과 3단계명 후보매칭 존재",
        len(rone_candidate) > 0
        and "seoul_baseline_reference" in candidate_scope_counts
        and "rone_level3_name_match_candidate" in candidate_scope_counts
        and candidate_direct == ["false"]
        and candidate_promotion == ["false"],
        "R-ONE 매핑은 후보 evidence로만 만들고 직접점수/엔진승격 플래그를 false로 고정한다.",
    ))

    level2_rows = rone[rone["지역_레벨"] == "2단계"].shape[0]
    level2_candidate_rows = int((rone_candidate["지역_레벨"] == "2단계").sum()) if not rone_candidate.empty else 0
    validations.append(row(
        "64-V09",
        "R-ONE 2단계 권역 자동매핑 금지",
        f"source_level2={level2_rows}; candidate_level2={level2_candidate_rows}",
        "candidate_level2=0",
        level2_rows > 0 and level2_candidate_rows == 0,
        "강남/도심/기타 같은 2단계 권역은 자치구 경계와 1:1이 아니므로 수동 권역표 전에는 상권에 자동 배분하지 않는다.",
    ))

    itaewon_rows = rone_candidate[
        rone_candidate["상권_코드_명"].astype(str).str.contains("이태원", na=False)
        & rone_candidate["지역_전체명"].astype(str).str.endswith(">이태원")
    ]
    validations.append(row(
        "64-V10",
        "R-ONE 3단계 상권명 후보 샘플",
        len(itaewon_rows),
        ">0",
        len(itaewon_rows) > 0,
        "상권명 후보매칭은 최소한 명확한 3단계 지역명 예시를 잡아야 한다. 그래도 수동검토 후보로만 둔다.",
    ))

    engine = load_engine()
    indicator_names = set(engine.INDICATORS.keys())
    rone_indicator = [name for name in indicator_names if "R_ONE" in name or "임대" in name or "권리금" in name]
    validations.append(row(
        "64-V11",
        "엔진 공식 지표 R-ONE 직접투입 없음",
        ";".join(rone_indicator) if rone_indicator else "none",
        "none",
        not rone_indicator,
        "R-ONE은 evidence-only 참고선이어야 하며 공식 비용축 지표로 바로 들어가면 안 된다.",
    ))

    source_docs = [
        ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "data_go_kr_molit_commercial_real_estate_trade_api.html",
        ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "data_go_kr_kab_small_shop_rent.html",
        ROOT / "research" / "rule_validation" / "12_real_estate_cost_proxy_silver_validation_20260703.md",
        ROOT / "research" / "rule_validation" / "47_real_estate_broker_cost_proxy_candidate_validation_20260707.md",
    ]
    missing_docs = [str(p.relative_to(ROOT)) for p in source_docs if not p.exists()]
    validations.append(row(
        "64-V12",
        "비용 프록시 근거문서 존재",
        ";".join(missing_docs) if missing_docs else "none",
        "none",
        not missing_docs,
        "비용 프록시 규칙은 research에 모은 공식 문서와 기존 검증문서를 근거로 추적되어야 한다.",
    ))

    validation_df = pd.DataFrame(validations)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = "COST_PROXY_AREA_MAPPING_PASS_NOT_PROMOTED" if fail_count == 0 else "COST_PROXY_AREA_MAPPING_FAIL"

    scope_summary = (
        rone_candidate.groupby(["mapping_scope", "mapping_confidence"], dropna=False)
        .agg(
            rows=("상권_코드", "size"),
            trade_area_count=("상권_코드", "nunique"),
            rone_region_count=("지역_전체명", "nunique"),
            item_count=("ITM_NM", "nunique"),
        )
        .reset_index()
        if not rone_candidate.empty
        else pd.DataFrame()
    )

    sample_rows = rone_candidate[
        ["상권_코드", "상권_코드_명", "자치구_코드_명", "mapping_scope", "mapping_confidence",
         "지역_전체명", "ITM_NM", "DTA_VAL", "UI_NM", "direct_score_allowed", "engine_promotion_ready"]
    ].head(100) if not rone_candidate.empty else pd.DataFrame()

    validation_path = RULE_OUT / "64_cost_proxy_area_mapping_validation.csv"
    scope_path = RULE_OUT / "64_cost_proxy_rone_mapping_scope_summary.csv"
    sample_path = RULE_OUT / "64_cost_proxy_rone_mapping_sample_rows.csv"
    summary_path = RULE_OUT / "64_cost_proxy_area_mapping_summary.json"
    md_path = MD_OUT / "64_cost_proxy_area_mapping_validation_20260707.md"

    validation_df.to_csv(validation_path, index=False, encoding="utf-8-sig")
    scope_summary.to_csv(scope_path, index=False, encoding="utf-8-sig")
    sample_rows.to_csv(sample_path, index=False, encoding="utf-8-sig")

    summary = {
        "validation_number": 64,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "candidate_version": CANDIDATE_VERSION,
        "trade_area_count": int(area_count),
        "rtms_quarter_count": int(quarter_count),
        "rtms_cost_gold_rows": int(len(cost)),
        "rone_latest_rows": int(len(rone)),
        "rone_candidate_rows": int(len(rone_candidate)),
        "rone_candidate_trade_area_count": int(rone_candidate["상권_코드"].nunique()) if not rone_candidate.empty else 0,
        "rone_scope_counts": {str(k): int(v) for k, v in candidate_scope_counts.items()},
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "next_step": "optional_engine_evidence_loader_for_rone_candidate_or_sgis_kosis_grain_penalty",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# 64. R-ONE/RTMS 비용 프록시 권역-상권 매핑 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "RTMS와 R-ONE 비용 관련 원천을 상권 분석에 쓸 때, 월세·권리금·수익성 직접값처럼 오해하지 않도록 매핑 단위와 후보 사용 범위를 검증했다.",
        "",
        "## 핵심 결과",
        "",
        f"- validation version: `{VERSION}`",
        f"- candidate version: `{CANDIDATE_VERSION}`",
        f"- trade area count: {summary['trade_area_count']:,}",
        f"- RTMS quarter count: {summary['rtms_quarter_count']:,}",
        f"- RTMS cost gold rows: {summary['rtms_cost_gold_rows']:,}",
        f"- R-ONE latest rows: {summary['rone_latest_rows']:,}",
        f"- R-ONE candidate rows: {summary['rone_candidate_rows']:,}",
        f"- R-ONE candidate trade area count: {summary['rone_candidate_trade_area_count']:,}",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- decision: `{decision}`",
        "",
        "## 해석",
        "",
        "- RTMS는 자치구 분기 단위 상업·업무용 매매가격 기반 비용 압력 프록시다.",
        "- `gold_cost_risk_q_area.csv`는 RTMS 자치구값을 상권에 fan-out한 것이며 상권별 월세나 권리금 직접값이 아니다.",
        "- R-ONE은 서울/권역/상권명 후보 기준선으로 별도 후보 gold를 만들었지만 직접점수와 엔진승격은 false다.",
        "- R-ONE 2단계 권역은 자치구 경계와 1:1이 아니므로 자동 배분하지 않았다.",
        "- 3단계 지역명은 상권명/행정동명 후보매칭으로만 남겼고 수동검토 대상이다.",
        "",
        "## 검증 결과",
        "",
        dataframe_to_markdown(validation_df),
        "",
        "## 후보 매핑 범위 요약",
        "",
        dataframe_to_markdown(scope_summary) if not scope_summary.empty else "후보 매핑 없음",
        "",
        "## 산출물",
        "",
        f"- `{candidate_path.relative_to(ROOT)}`",
        f"- `{validation_path.relative_to(ROOT)}`",
        f"- `{scope_path.relative_to(ROOT)}`",
        f"- `{sample_path.relative_to(ROOT)}`",
        f"- `{summary_path.relative_to(ROOT)}`",
        "",
        "## 다음 2보 전진 1보 후퇴",
        "",
        "1. 전진: RTMS 자치구 프록시가 상권에 fan-out된 구조를 검증했다.",
        "2. 전진: R-ONE 서울 기준선과 3단계 상권명 후보매칭을 candidate gold로 보존했다.",
        "3. 후퇴: R-ONE 2단계 권역은 수동 권역표 없이 자동 매핑하지 않았다.",
        "4. 후퇴: 어떤 비용 원천도 월세·권리금 직접값이나 개별 점포 수익성으로 승격하지 않았다.",
        "5. 후퇴: 이번 판정은 `PASS_NOT_PROMOTED`이며, 엔진 공식 비용점수 산식은 변경하지 않았다.",
    ]
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
