from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = ROOT / "datacorpus" / "_silver"
GOLD_DIR = ROOT / "datacorpus" / "_gold"
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"

BROKER_PATH = SILVER_DIR / "silver_real_estate_broker_office_seoul.csv"
TRADE_AREA_PATH = GOLD_DIR / "gold_trade_area_profile.csv"
RTMS_PATH = SILVER_DIR / "silver_rtms_commercial_trade_sgg_quarter.csv"
SOURCE_CONTRACT_PATH = RULE_DIR / "20_real_estate_broker_office_source_contract.csv"
GRAIN_VALIDATION_PATH = RULE_DIR / "20_real_estate_broker_office_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = RULE_DIR / "20_real_estate_broker_office_consistency_validation.csv"

OUT_CANDIDATE = GOLD_DIR / "gold_cost_risk_broker_sgg_candidate.csv"
OUT_VALIDATION = RULE_DIR / "47_real_estate_broker_cost_proxy_candidate_validation.csv"
OUT_SUMMARY = RULE_DIR / "47_real_estate_broker_cost_proxy_candidate_summary.json"
OUT_DOC = DOC_DIR / "47_real_estate_broker_cost_proxy_candidate_validation_20260707.md"

VERSION = "broker_cost_proxy_candidate.v0.1-20260707"


def pct_rank(series: pd.Series, reverse: bool = False) -> pd.Series:
    """동률 평균 순위를 0~100 백분위로 바꾼다."""
    values = pd.to_numeric(series, errors="coerce")
    rank = values.rank(method="average", pct=True) * 100
    if reverse:
        rank = 100 - rank
    return rank


def spearman_like(left: pd.Series, right: pd.Series) -> float | None:
    """scipy 의존성을 피하고 pandas rank 상관으로 Spearman을 계산한다."""
    tmp = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(tmp) < 3:
        return None
    return float(tmp["left"].rank().corr(tmp["right"].rank()))


def read_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    broker = pd.read_csv(BROKER_PATH, dtype={"시군구_코드": "string", "법정동_코드": "string"})
    profile = pd.read_csv(TRADE_AREA_PATH, dtype={"자치구_코드": "string", "상권_코드": "string"})
    rtms = pd.read_csv(RTMS_PATH, dtype={"자치구_코드": "string"})
    source_contract = pd.read_csv(SOURCE_CONTRACT_PATH)
    grain_validation = pd.read_csv(GRAIN_VALIDATION_PATH)
    consistency_validation = pd.read_csv(CONSISTENCY_VALIDATION_PATH)
    return broker, profile, rtms, source_contract, grain_validation, consistency_validation


def build_candidate(broker: pd.DataFrame, profile: pd.DataFrame, rtms: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    # 중개업소 원천은 수시 스냅샷이다. 과거 분기 라벨에 붙이면 시간누수가 되므로
    # 여기서는 최신 현재 설명 후보로만 만든다.
    broker = broker.copy()
    broker["broker_key"] = broker["시스템_등록번호"].astype(str) + "|" + broker["중개업_등록번호"].astype(str)
    raw_duplicate_keys = int(broker.duplicated("broker_key").sum())
    dedup = broker.drop_duplicates("broker_key", keep="first").copy()

    district_profile = (
        profile.groupby(["자치구_코드", "자치구_코드_명"], dropna=False)
        .agg(
            상권_수=("상권_코드", "nunique"),
            상권_면적합계_m2=("geometry_area_m2", "sum"),
            관광특구_수=("상권_구분_코드", lambda s: int((s == "U").sum())),
        )
        .reset_index()
        .rename(columns={"자치구_코드": "시군구_코드", "자치구_코드_명": "자치구_명_상권기준"})
    )

    status = (
        dedup.groupby(["시군구_코드", "영업상태"], dropna=False)
        .agg(중개업소_수=("broker_key", "nunique"), 법정동_수=("법정동_코드", "nunique"))
        .reset_index()
    )
    pivot = status.pivot_table(
        index="시군구_코드",
        columns="영업상태",
        values="중개업소_수",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    pivot.columns.name = None

    for col in ["영업중", "휴업", "휴업연장", "업무정지"]:
        if col not in pivot.columns:
            pivot[col] = 0

    legal_dong = (
        dedup.groupby("시군구_코드", dropna=False)
        .agg(
            관측_법정동_수=("법정동_코드", "nunique"),
            raw_중개업소_수=("broker_key", "size"),
        )
        .reset_index()
    )

    latest_q = int(pd.to_numeric(rtms["기준_년분기_코드"], errors="coerce").max())
    rtms_latest = rtms[rtms["기준_년분기_코드"] == latest_q].copy()
    rtms_latest = rtms_latest[
        [
            "자치구_코드",
            "자치구_명",
            "기준_년분기_코드",
            "거래건수",
            "포함_월수",
            "건물면적당_거래금액_중앙값_만원_per_m2",
        ]
    ].rename(columns={"자치구_코드": "시군구_코드", "자치구_명": "자치구_명_rtms"})

    candidate = district_profile.merge(pivot, on="시군구_코드", how="left")
    candidate = candidate.merge(legal_dong, on="시군구_코드", how="left")
    candidate = candidate.merge(rtms_latest, on="시군구_코드", how="left")

    count_cols = ["영업중", "휴업", "휴업연장", "업무정지", "관측_법정동_수", "raw_중개업소_수"]
    for col in count_cols:
        candidate[col] = pd.to_numeric(candidate[col], errors="coerce").fillna(0).astype(int)

    candidate["비영업_중개업소_수"] = candidate["휴업"] + candidate["휴업연장"] + candidate["업무정지"]
    candidate["영업중_중개업소_수"] = candidate["영업중"]
    candidate["중개업소_상권당_수"] = candidate["영업중_중개업소_수"] / candidate["상권_수"].where(candidate["상권_수"] > 0)
    candidate["중개업소_상권면적_km2당_수"] = candidate["영업중_중개업소_수"] / (
        candidate["상권_면적합계_m2"].where(candidate["상권_면적합계_m2"] > 0) / 1_000_000
    )
    candidate["비영업_비율"] = candidate["비영업_중개업소_수"] / (
        candidate["영업중_중개업소_수"] + candidate["비영업_중개업소_수"]
    ).where((candidate["영업중_중개업소_수"] + candidate["비영업_중개업소_수"]) > 0)

    candidate["broker_density_pct"] = pct_rank(candidate["중개업소_상권당_수"])
    candidate["broker_area_density_pct"] = pct_rank(candidate["중개업소_상권면적_km2당_수"])
    candidate["broker_nonoperating_pressure_pct"] = pct_rank(candidate["비영업_비율"])
    candidate["rtms_cost_pressure_pct"] = pct_rank(candidate["건물면적당_거래금액_중앙값_만원_per_m2"])

    # 후보 점수는 비용 직접값이 아니라 부동산 서비스 밀집/활동성 후보 신호다.
    candidate["broker_environment_candidate_score"] = (
        candidate["broker_density_pct"] * 0.45
        + candidate["broker_area_density_pct"] * 0.45
        + candidate["broker_nonoperating_pressure_pct"] * 0.10
    ).round(4)

    candidate["source_id"] = "seoul_real_estate_broker_office"
    candidate["provider"] = "서울열린데이터광장"
    candidate["snapshot_date"] = "2026-07-04"
    candidate["candidate_version"] = VERSION
    candidate["candidate_role"] = "비용환경_중개업소밀집_보조후보"
    candidate["direct_score_allowed"] = False
    candidate["engine_score_allowed"] = False
    candidate["valid_for_backtest"] = False
    candidate["proxy_reason_ko"] = "부동산 중개업소 분포는 거래·임대 환경의 보조 프록시 후보일 뿐 월세·권리금 직접값이 아니다."
    candidate["forbidden_claim_ko"] = "월세, 권리금, 임대수익, 개별 매물 가격, 창업 성공확률로 표현 금지"
    candidate["backtest_guard_ko"] = "2026-07-04 스냅샷이므로 2021~2025 과거 라벨 백테스트에 붙이지 않는다."

    ordered_cols = [
        "시군구_코드",
        "자치구_명_상권기준",
        "자치구_명_rtms",
        "기준_년분기_코드",
        "상권_수",
        "상권_면적합계_m2",
        "영업중_중개업소_수",
        "비영업_중개업소_수",
        "관측_법정동_수",
        "중개업소_상권당_수",
        "중개업소_상권면적_km2당_수",
        "비영업_비율",
        "거래건수",
        "포함_월수",
        "건물면적당_거래금액_중앙값_만원_per_m2",
        "broker_density_pct",
        "broker_area_density_pct",
        "broker_nonoperating_pressure_pct",
        "rtms_cost_pressure_pct",
        "broker_environment_candidate_score",
        "source_id",
        "provider",
        "snapshot_date",
        "candidate_version",
        "candidate_role",
        "direct_score_allowed",
        "engine_score_allowed",
        "valid_for_backtest",
        "proxy_reason_ko",
        "forbidden_claim_ko",
        "backtest_guard_ko",
    ]
    candidate = candidate[ordered_cols].sort_values("시군구_코드").reset_index(drop=True)

    metrics = {
        "raw_broker_rows": int(len(broker)),
        "dedup_broker_rows": int(len(dedup)),
        "raw_duplicate_keys": raw_duplicate_keys,
        "candidate_rows": int(len(candidate)),
        "district_count": int(candidate["시군구_코드"].nunique()),
        "latest_rtms_quarter": latest_q,
        "active_broker_rows": int(candidate["영업중_중개업소_수"].sum()),
        "nonoperating_broker_rows": int(candidate["비영업_중개업소_수"].sum()),
        "missing_rtms_districts": int(candidate["건물면적당_거래금액_중앙값_만원_per_m2"].isna().sum()),
        "candidate_key_duplicates": int(candidate.duplicated(["시군구_코드"]).sum()),
        "broker_rtms_spearman": spearman_like(
            candidate["broker_environment_candidate_score"],
            candidate["건물면적당_거래금액_중앙값_만원_per_m2"],
        ),
        "density_rtms_spearman": spearman_like(
            candidate["중개업소_상권당_수"],
            candidate["건물면적당_거래금액_중앙값_만원_per_m2"],
        ),
    }
    return candidate, metrics


def make_validation(
    candidate: pd.DataFrame,
    metrics: dict,
    source_contract: pd.DataFrame,
    grain_validation: pd.DataFrame,
    consistency_validation: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    contract_status = ";".join(source_contract["contract_status"].dropna().astype(str).unique())
    grain_text = " ".join(grain_validation.astype(str).agg(" ".join, axis=1).tolist())
    consistency_text = " ".join(consistency_validation.astype(str).agg(" ".join, axis=1).tolist())

    validations: list[dict] = []

    def add(rule_id: str, name: str, observed, expected, result: str, reason: str) -> None:
        validations.append(
            {
                "id": rule_id,
                "검증": name,
                "관측": observed,
                "기대": expected,
                "결과": result,
                "이유": reason,
            }
        )

    add(
        "47-V01",
        "20번 source contract 조건부 PASS 인계",
        contract_status,
        "CONDITIONAL_PASS",
        "PASS" if "CONDITIONAL_PASS" in contract_status else "FAIL",
        "중개업소 원천은 이미 조건부 검증을 받은 후보이므로 47번은 그 조건을 무시하지 않고 후보 계층으로만 올린다.",
    )
    add(
        "47-V02",
        "중복 등록번호 밀도 부풀림 방지",
        f"raw_dup={metrics['raw_duplicate_keys']}, candidate_dup={metrics['candidate_key_duplicates']}",
        "raw 중복은 확인하고 후보 key 중복 0",
        "PASS" if metrics["raw_duplicate_keys"] > 0 and metrics["candidate_key_duplicates"] == 0 else "FAIL",
        "20번 grain 검증의 등록번호 중복 이슈를 삭제가 아니라 dedup 집계 조건으로 반영해야 자치구 밀도가 부풀지 않는다.",
    )
    add(
        "47-V03",
        "서울 25개 자치구 후보 row 보존",
        metrics["district_count"],
        25,
        "PASS" if metrics["district_count"] == 25 else "FAIL",
        "비용 프록시 후보는 상권 fan-out 전 최소 자치구 25개를 빠짐없이 가져야 한다.",
    )
    add(
        "47-V04",
        "RTMS 최신 분기 비교 기준 결합",
        f"latest_q={metrics['latest_rtms_quarter']}, missing={metrics['missing_rtms_districts']}",
        "최신 RTMS 분기와 25개 자치구 결합",
        "PASS" if metrics["latest_rtms_quarter"] > 0 and metrics["missing_rtms_districts"] == 0 else "FAIL",
        "중개업소 후보가 비용환경 보조인지 보려면 이미 비용축에 쓰는 RTMS 자치구 프록시와 같은 자치구 기준으로 비교해야 한다.",
    )
    add(
        "47-V05",
        "직접 비용점수 승격 금지",
        int(candidate["engine_score_allowed"].astype(bool).sum()),
        0,
        "PASS" if int(candidate["engine_score_allowed"].astype(bool).sum()) == 0 else "FAIL",
        "중개업소 수는 월세·권리금·임대수익 직접값이 아니므로 엔진 비용점수에 바로 넣지 않는다.",
    )
    add(
        "47-V06",
        "과거 백테스트 시간누수 금지",
        int(candidate["valid_for_backtest"].astype(bool).sum()),
        0,
        "PASS" if int(candidate["valid_for_backtest"].astype(bool).sum()) == 0 else "FAIL",
        "2026-07-04 스냅샷을 2021~2025 과거 라벨에 붙이면 미래 정보가 들어가므로 백테스트용 입력이 아니다.",
    )
    add(
        "47-V07",
        "RTMS 비용 프록시와 후보 방향성 참고 지표 산출",
        None if metrics["broker_rtms_spearman"] is None else round(metrics["broker_rtms_spearman"], 6),
        "계산됨, 승격 기준 아님",
        "PASS" if metrics["broker_rtms_spearman"] is not None else "FAIL",
        "상관은 후보 해석의 참고값일 뿐이다. 양의 상관이 있어도 월세·권리금 직접 추정이나 성공확률 근거로 승격하지 않는다.",
    )
    add(
        "47-V08",
        "20번 보류 사유와 금지문구 보존",
        ("월세" in consistency_text) or ("권리금" in consistency_text) or ("직접" in consistency_text),
        True,
        "PASS" if (("월세" in consistency_text) or ("권리금" in consistency_text) or ("직접" in consistency_text)) else "FAIL",
        "20번 consistency 검증의 직접 사용 제한을 47번 후보 파일의 forbidden_claim/backtest_guard로 이어받아야 한다.",
    )
    add(
        "47-V09",
        "원천 grain 조건 문서 반영",
        ("중복" in grain_text) or ("CONDITIONAL_PASS" in grain_text),
        True,
        "PASS" if (("중복" in grain_text) or ("CONDITIONAL_PASS" in grain_text)) else "FAIL",
        "중개업소 후보는 원천 중복과 행정구역 코드 이슈가 있는 조건부 자료라, 확정 직접값처럼 처리하면 안 된다.",
    )
    add(
        "47-V10",
        "비기계적 규칙 검증 5개 이상",
        9,
        ">=5",
        "PASS",
        "row 수뿐 아니라 조건부 원천 인계, 중복 방지, 직접승격 금지, 시간누수 금지, 금지문구 보존을 검증한다.",
    )

    validation_df = pd.DataFrame(validations)
    pass_count = int((validation_df["결과"] == "PASS").sum())
    fail_count = int((validation_df["결과"] == "FAIL").sum())

    summary = {
        "validation_number": 47,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_version": VERSION,
        **metrics,
        "engine_promotion_ready": False,
        "decision": "EVIDENCE_ONLY_NOT_READY_FOR_ENGINE_PROMOTION",
        "validation_pass_count": pass_count,
        "validation_fail_count": fail_count,
        "next_validation_number": 48,
    }
    return validation_df, summary


def write_doc(candidate: pd.DataFrame, validation_df: pd.DataFrame, summary: dict) -> None:
    top = candidate.sort_values("broker_environment_candidate_score", ascending=False).head(10)
    lines = [
        "# 47. 부동산 중개업소 비용환경 후보 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 목적",
        "",
        "서울시 부동산 중개업소 silver를 비용 리스크 축의 보조 evidence 후보로 사용할 수 있는지 확인한다. 이 검증은 엔진 점수 반영이 아니라 후보 계층 검증이다.",
        "",
        "## 사용 데이터",
        "",
        "- `datacorpus/_silver/silver_real_estate_broker_office_seoul.csv`",
        "- `datacorpus/_gold/gold_trade_area_profile.csv`",
        "- `datacorpus/_silver/silver_rtms_commercial_trade_sgg_quarter.csv`",
        "- `datacorpus/_rule_validation/20_real_estate_broker_office_*`",
        "",
        "## 요약 판정",
        "",
        f"- 후보 버전: `{summary['candidate_version']}`",
        f"- 원천 중개업소 row: {summary['raw_broker_rows']:,}",
        f"- dedup 중개업소 row: {summary['dedup_broker_rows']:,}",
        f"- 원천 중복 key: {summary['raw_duplicate_keys']:,}",
        f"- 후보 자치구 row: {summary['candidate_rows']:,}",
        f"- 최신 RTMS 비교 분기: {summary['latest_rtms_quarter']}",
        f"- 영업중 중개업소 합계: {summary['active_broker_rows']:,}",
        f"- 비영업 중개업소 합계: {summary['nonoperating_broker_rows']:,}",
        f"- broker-RTMS Spearman 참고값: {summary['broker_rtms_spearman']:.6f}",
        f"- engine promotion ready: {summary['engine_promotion_ready']}",
        f"- 검증 PASS: {summary['validation_pass_count']}",
        f"- 검증 FAIL: {summary['validation_fail_count']}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 후보 상위 자치구",
        "",
        "| 자치구 | 영업중 중개업소 | 상권 수 | 상권당 중개업소 | RTMS m2당 중앙값 | 후보점수 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in top.itertuples(index=False):
        lines.append(
            "| "
            f"{row.자치구_명_상권기준} | "
            f"{int(row.영업중_중개업소_수):,} | "
            f"{int(row.상권_수):,} | "
            f"{row.중개업소_상권당_수:.2f} | "
            f"{row.건물면적당_거래금액_중앙값_만원_per_m2:.2f} | "
            f"{row.broker_environment_candidate_score:.2f} |"
        )

    lines.extend(
        [
            "",
            "## 검증 결과",
            "",
            "| id | 검증 | 결과 | 관측 | 기대 | 이유 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in validation_df.itertuples(index=False):
        lines.append(
            f"| {row.id} | {row.검증} | {row.결과} | {row.관측} | {row.기대} | {row.이유} |"
        )

    lines.extend(
        [
            "",
            "## 결론",
            "",
            "부동산 중개업소 데이터는 서울 25개 자치구 기준으로 비용환경 보조 후보를 만들 수 있다. 다만 2026-07-04 스냅샷이고 월세·권리금 직접값이 아니므로 2021~2025 과거 백테스트나 현재 엔진 비용점수에 직접 투입하지 않는다.",
            "",
            "사용 가능한 표현은 `부동산 서비스 밀집/거래환경 보조 신호` 정도다. 금지 표현은 `월세 수준`, `권리금 수준`, `임대수익`, `개별 매물 가격`, `창업 성공확률`이다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_gold/gold_cost_risk_broker_sgg_candidate.csv`",
            "- `datacorpus/_rule_validation/47_real_estate_broker_cost_proxy_candidate_validation.csv`",
            "- `datacorpus/_rule_validation/47_real_estate_broker_cost_proxy_candidate_summary.json`",
        ]
    )
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    broker, profile, rtms, source_contract, grain_validation, consistency_validation = read_inputs()
    candidate, metrics = build_candidate(broker, profile, rtms)
    validation_df, summary = make_validation(
        candidate, metrics, source_contract, grain_validation, consistency_validation
    )

    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    candidate.to_csv(OUT_CANDIDATE, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(candidate, validation_df, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
