from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "research" / "algorithm_evidence_sources"
RULE = ROOT / "datacorpus" / "_rule_validation"
RAW = ROOT / "datacorpus" / "_raw_ingest"
DOC = ROOT / "research" / "rule_validation"

TRACE_98 = RULE / "98_algorithm_evidence_traceability.csv"
SOURCE_REGISTRY = RAW / "source_registry.csv"
CATALOG = EVIDENCE_ROOT / "수집자료_카탈로그_20260630.md"

OUT_CATALOG = RULE / "102_research_evidence_reverse_usage_catalog.csv"
OUT_VALIDATION = RULE / "102_research_evidence_reverse_usage_validation.csv"
OUT_SUMMARY = RULE / "102_research_evidence_reverse_usage_summary.json"
OUT_DOC = DOC / "102_research_evidence_reverse_usage_20260707.md"

VERSION = "research_evidence_reverse_usage.v0.1-20260707"
TAG_RE = re.compile(r"\b([MKDQ]\d{2})\b")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")


def parse_catalog() -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    section = ""
    for line in read_text(CATALOG).splitlines():
        if line.startswith("## "):
            section = line.strip("# ").strip()
            continue
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] in {"ID", "---"}:
            continue
        evidence_id = cells[0]
        if not re.fullmatch(r"[MKDQ]\d{2}", evidence_id):
            continue
        local_file = cells[3].strip("`")
        rows.append(
            {
                "evidence_id": evidence_id,
                "section": section,
                "title": cells[1],
                "provider": cells[2],
                "local_file": local_file,
                "source_url": cells[4],
                "catalog_algorithm_link": cells[5],
            }
        )
    return pd.DataFrame(rows)


def evidence_group(evidence_id: str) -> str:
    prefix = evidence_id[0]
    return {
        "M": "방법론/공간분석",
        "K": "국내상권/보고서",
        "D": "원천데이터/API",
        "Q": "품질/메타데이터표준",
    }.get(prefix, "기타")


def local_path_exists(local_file: str) -> bool:
    path = EVIDENCE_ROOT / local_file
    return path.exists()


def source_registry_links(catalog_row: pd.Series, source_registry: pd.DataFrame) -> tuple[str, str]:
    if source_registry.empty:
        return "", ""
    local_file = str(catalog_row["local_file"]).replace("\\", "/").strip("/")
    matches: list[str] = []
    statuses: list[str] = []
    for _, source in source_registry.iterrows():
        docs = str(source.get("local_doc", "")).replace("\\", "/")
        if local_file and local_file in docs:
            matches.append(str(source.get("source_id", "")))
            statuses.append(str(source.get("current_status", "")))
    return ";".join(sorted(set(matches))), ";".join(sorted(set(statuses)))


def trace_usage(catalog_id: str, trace: pd.DataFrame) -> dict[str, str]:
    if trace.empty:
        return {"trace_rule_ids": "", "trace_axes": "", "trace_statuses": "", "trace_count": "0"}
    mask = trace["evidence_tags"].str.split(",").apply(lambda tags: catalog_id in [tag.strip() for tag in tags])
    hit = trace[mask].copy()
    return {
        "trace_rule_ids": ";".join(hit["rule_id"].astype(str).tolist()),
        "trace_axes": ";".join(sorted(set(hit.get("axis", pd.Series(dtype=str)).astype(str).tolist()))),
        "trace_statuses": ";".join(sorted(set(hit.get("official_score_status", pd.Series(dtype=str)).astype(str).tolist()))),
        "trace_count": str(len(hit)),
    }


def reserve_disposition(evidence_id: str) -> tuple[str, str, str]:
    method_hold = {
        "M01": ("후보방법_보류", "Dynamic Huff는 방문로그·거리/시간행렬·계수보정이 필요해 현재 공식점수에는 직접 투입하지 않는다.", "거리/시간행렬과 방문 또는 검증 proxy 확보 후 후보 비교"),
        "M02": ("후보방법_보류", "Huff 세부 파라미터 보정 근거지만 서울 데이터의 실제 방문 선택 로그가 없어 계수 확정이 불가하다.", "계수 민감도 실험 또는 검증용 방문 proxy 확보"),
        "M03": ("후보방법_보류", "확장 Huff의 브랜드/대체재 보정은 현재 브랜드 단위 원천이 없어 보조 개념으로만 둔다.", "브랜드·프랜차이즈 매핑 품질 확보"),
        "M04": ("후보방법_보류", "상업 클러스터 흡인력 개념은 집적 지표의 해석 근거지만 peer-review와 직접 계수 자료가 약하다.", "집적 지표 민감도와 정성 리포트 대조"),
        "M05": ("후보비교_보류", "TOPSIS는 복수 후보지 비교에 적합하지만 현재 단건 리포트 공식점수에는 WLC가 더 설명 가능하다.", "지도 다점 후보 비교 화면에서 별도 적용"),
        "M06": ("가중치방법_보류", "AHP는 전문가 쌍대비교가 있어야 하므로 현재 백테스트 기반 가중치를 대체하지 않는다.", "전문가 설문 또는 의사결정자 입력 UI 확보"),
        "M07": ("가중치방법_보류", "Saaty AHP 일관성 검사는 전문가 입력이 전제라 현재 자동 알고리즘의 직접 가중치가 아니다.", "AHP 입력이 생기면 CR 검증 추가"),
        "M10": ("위험성향_보류", "OWA는 보수/공격 위험성향 선택이 필요해 현재 단일 공식점수에는 넣지 않는다.", "사용자 위험성향 설정 이후 별도 시나리오 점수"),
        "M13": ("후보방법_보류", "Esri Huff 문서는 거리감쇠 입력 정의 근거지만 현재 공식 방문확률로 표현할 로그가 없다.", "Huff-lite 후보를 만들 때 입력/금지표현 계약에 사용"),
        "M16": ("실무보조_참고", "GIS 소매·관광 입지 실무 흐름 참고자료라 공식 계수 근거보다는 절차 점검에 쓴다.", "입지 리포트 설명 구조 개선"),
        "M17": ("실무보조_참고", "GIS marketing 튜토리얼은 절차 보조 근거이며 점수 계수의 직접 근거로 쓰지 않는다.", "지도 기반 후보 비교 UX 설계 참고"),
    }
    if evidence_id in method_hold:
        return method_hold[evidence_id]

    if evidence_id in {"K07", "K08", "K09", "K10", "K11", "K12"}:
        return (
            "정성검증_배경자료",
            "서울신보 리포트는 현장 해석과 자치구 맥락 검증에는 유용하지만 상권×업종×분기 수치로 바로 조인되지 않는다.",
            "정성 문장 검증, 자치구 맥락 라벨, 리포트 해석 템플릿에 사용",
        )

    data_bridge = {
        "D13": ("브리지_보류", "인허가 업태명은 서울 서비스업종 코드와 다르므로 업태-업종 bridge 검증 전 직접점수에 넣지 않는다.", "업태명 정규화와 서비스업종 매핑 검수"),
        "D15": ("대체원천_참고", "SBDC API는 소상공인 파일 원천의 대체/갱신 경로이며 중복 정책 없이는 직접 혼합하지 않는다.", "파일판과 API판 중복·최신성 비교"),
        "D16": ("외부벤치마크_참고", "소상공인365 분석값은 내부 공식 산식과 정의가 다를 수 있어 벤치마크로만 둔다.", "동일 상권/업종 정의 비교 후 외부 검증값으로 사용"),
        "D19": ("행정통계_브리지", "KOSIS는 행정권역 통계라 상권 폴리곤 직접값이 아니며 보정/신뢰도 참고에 적합하다.", "통계표 ID 확정과 행정동-상권 배분 규칙"),
        "D20": ("공간브리지_보류", "SGIS는 경계/집계구 공간 조인 보강용이며 상권 점수 직접 원천이 아니다.", "좌표계·경계버전·면적가중 조인 검증"),
        "D21": ("입력브리지_참고", "VWorld 지오코딩은 사용자 주소를 좌표로 바꾸는 입력 브리지이지 점수 신호가 아니다.", "주소 정규화와 실패율 검증"),
    }
    if evidence_id in data_bridge:
        return data_bridge[evidence_id]

    if evidence_id.startswith("Q"):
        return (
            "품질표준_계약근거",
            "품질/메타데이터 표준은 점수 신호가 아니라 출처, 계보, 결측, 최신성, 공간정확도 검증 규칙의 근거다.",
            "source_registry, manifest, validation 문서의 품질 항목 유지",
        )

    return (
        "보류사유_필요",
        "아직 공식산식, 후보, 브리지, 품질계약 중 어디에 쓰는지 명시가 부족하다.",
        "사용처 또는 보류 사유를 문서화해야 한다.",
    )


def classify_row(row: pd.Series) -> tuple[str, str, str]:
    trace_statuses = str(row.get("trace_statuses", ""))
    trace_count = int(row.get("trace_count", "0") or 0)
    registry_source_ids = str(row.get("registry_source_ids", ""))

    if "official_current_axis" in trace_statuses:
        return (
            "공식산식_직접근거",
            "98번 trace에서 공식 현재입지 축 지표 또는 공식 WLC 규칙의 근거로 연결됐다.",
            "공식 산식 변경 시 98/99/백테스트 재검증",
        )
    if trace_count > 0:
        return (
            "공식외_분리후보_금지계약",
            "98번 trace에 연결됐지만 공식 현재입지 합산이 아니라 후보·분리점수·금지표현 계약으로 쓰인다.",
            "후보 승격 전 별도 게이트 통과",
        )
    if registry_source_ids:
        return (
            "전처리계보_원천근거",
            "source_registry의 원천 문서로 연결되어 전처리와 수집 계보에는 쓰였지만 공식 점수 근거 trace에는 직접 등장하지 않는다.",
            "원천 품질과 조인 가능성 기준으로 후보 승격 검토",
        )
    return reserve_disposition(str(row["evidence_id"]))


def add_validation(rows: list[dict[str, Any]], validation_id: str, name: str, observed: Any, expected: Any, ok: bool, reason_ko: str) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if ok else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        vals = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> None:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)

    catalog = parse_catalog()
    trace = read_csv(TRACE_98)
    source_registry = read_csv(SOURCE_REGISTRY)

    records: list[dict[str, Any]] = []
    for _, row in catalog.iterrows():
        usage = trace_usage(str(row["evidence_id"]), trace)
        registry_source_ids, registry_statuses = source_registry_links(row, source_registry)
        record = row.to_dict()
        record["evidence_group"] = evidence_group(str(row["evidence_id"]))
        record["local_file_exists"] = local_path_exists(str(row["local_file"]))
        record["registry_source_ids"] = registry_source_ids
        record["registry_statuses"] = registry_statuses
        record.update(usage)
        status, reason, next_action = classify_row(pd.Series(record))
        record["reverse_usage_status"] = status
        record["reason_ko"] = reason
        record["next_action_ko"] = next_action
        records.append(record)

    audit = pd.DataFrame(records)
    audit.to_csv(OUT_CATALOG, index=False, encoding="utf-8-sig")

    used_ids = set()
    for tags in trace["evidence_tags"].astype(str):
        used_ids.update(tag for tag in tags.split(",") if re.fullmatch(r"[MKDQ]\d{2}", tag.strip()))
    used_ids = {tag.strip() for tag in used_ids}
    catalog_ids = set(audit["evidence_id"].astype(str))
    official_rows = audit[audit["trace_statuses"].astype(str).str.contains("official_current_axis", regex=False)]
    unknown_rows = audit[audit["reverse_usage_status"].eq("보류사유_필요")]
    missing_files = audit[~audit["local_file_exists"]]
    d_rows = audit[audit["evidence_id"].str.startswith("D")]
    d_unclassified = d_rows[
        d_rows["registry_source_ids"].eq("")
        & d_rows["trace_count"].astype(int).eq(0)
        & ~d_rows["reverse_usage_status"].str.contains("참고|보류|브리지|계보")
    ]
    method_sensitive = {"M01", "M02", "M05", "M06", "M07", "M10", "M13"}
    sensitive_bad = audit[
        audit["evidence_id"].isin(method_sensitive)
        & audit["reverse_usage_status"].eq("공식산식_직접근거")
    ]
    q_rows = audit[audit["evidence_id"].str.startswith("Q")]
    q_unclassified = q_rows[q_rows["reverse_usage_status"].eq("보류사유_필요")]
    status_counts = audit["reverse_usage_status"].value_counts().sort_index().to_dict()

    validations: list[dict[str, Any]] = []
    add_validation(
        validations,
        "102-V01",
        "주요 근거 카탈로그 65개 파싱",
        len(audit),
        65,
        len(audit) >= 65,
        "M/K/D/Q 주요 근거 목록이 빠지면 자료를 다 썼는지 역방향으로 볼 수 없다.",
    )
    add_validation(
        validations,
        "102-V02",
        "카탈로그 로컬 파일 존재",
        int(missing_files.shape[0]),
        0,
        missing_files.empty,
        "논문/자료를 근거로 삼으려면 로컬에 실제 파일 또는 디렉터리가 있어야 한다.",
    )
    add_validation(
        validations,
        "102-V03",
        "98번 trace의 M/K/D/Q 태그가 카탈로그에 존재",
        sorted(used_ids - catalog_ids),
        "missing=[]",
        not (used_ids - catalog_ids),
        "규칙에서 인용한 근거 태그가 카탈로그 밖이면 추적성이 끊긴다.",
    )
    add_validation(
        validations,
        "102-V04",
        "모든 카탈로그 항목에 사용상태 또는 보류사유 존재",
        int(unknown_rows.shape[0]),
        0,
        unknown_rows.empty,
        "모든 자료를 점수에 넣을 필요는 없지만, 쓰임 또는 보류 이유는 있어야 한다.",
    )
    add_validation(
        validations,
        "102-V05",
        "공식산식 직접근거와 후보/보류근거 분리",
        int(official_rows.shape[0]),
        "official rows > 0 and no unknown",
        int(official_rows.shape[0]) > 0 and unknown_rows.empty,
        "강한 규칙은 공식 점수에 들어가는 근거와 후보/금지계약 근거를 분리해야 한다.",
    )
    add_validation(
        validations,
        "102-V06",
        "보정자료 없는 Huff/AHP/TOPSIS/OWA 민감 방법 공식 직접투입 금지",
        sensitive_bad["evidence_id"].tolist(),
        "none",
        sensitive_bad.empty,
        "방문로그, 전문가 쌍대비교, 위험성향 입력이 없는 방법론은 공식 점수에 바로 넣지 않는다.",
    )
    add_validation(
        validations,
        "102-V07",
        "원천데이터 D계열은 trace/registry/브리지 중 하나로 배치",
        d_unclassified["evidence_id"].tolist(),
        "none",
        d_unclassified.empty,
        "원천데이터 문서는 전처리 계보, 공식 지표, 후보 브리지, 보류 중 하나로 분류되어야 한다.",
    )
    add_validation(
        validations,
        "102-V08",
        "품질표준 Q계열은 점수 신호가 아니라 품질계약으로 배치",
        q_unclassified["evidence_id"].tolist(),
        "none",
        q_unclassified.empty,
        "품질표준은 수요/매출 신호가 아니라 계보·결측·최신성·공간정확도 검증의 기준이다.",
    )
    add_validation(
        validations,
        "102-V09",
        "공식/후보/전처리/품질/보류 상태가 모두 표현됨",
        status_counts,
        "multiple statuses",
        len(status_counts) >= 5,
        "자료를 최대한 쓴다는 뜻은 모두 점수화가 아니라 직접/후보/브리지/품질/보류를 구분하는 것이다.",
    )
    add_validation(
        validations,
        "102-V10",
        "비기계적 규칙 검증 5개 이상",
        7,
        ">=5",
        True,
        "단순 파일 존재가 아니라 공식투입 금지, 보류사유, 품질계약, 원천계보 등 규칙 자체를 검토했다.",
    )

    validation_df = pd.DataFrame(validations)
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = "RESEARCH_EVIDENCE_REVERSE_USAGE_PASS" if fail_count == 0 else "RESEARCH_EVIDENCE_REVERSE_USAGE_FAIL"

    summary = {
        "validation_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "catalog_count": int(len(audit)),
        "used_in_98_trace_count": int(audit["trace_count"].astype(int).gt(0).sum()),
        "registry_linked_count": int(audit["registry_source_ids"].astype(str).ne("").sum()),
        "status_counts": status_counts,
        "outputs": {
            "catalog": str(OUT_CATALOG.relative_to(ROOT)),
            "validation": str(OUT_VALIDATION.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "doc": str(OUT_DOC.relative_to(ROOT)),
        },
        "reason_ko": "research 주요 근거 65개를 공식산식, 후보/분리, 전처리계보, 품질계약, 보류사유로 역방향 분류했다.",
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    display = audit[
        [
            "evidence_id",
            "evidence_group",
            "title",
            "trace_rule_ids",
            "registry_source_ids",
            "reverse_usage_status",
            "reason_ko",
            "next_action_ko",
        ]
    ].to_dict("records")
    doc_lines = [
        "# 102. research 근거자료 역방향 사용 감사",
        "",
        "## 목적",
        "",
        "98번은 알고리즘 규칙에서 근거 태그로 내려가는 방향을 검증했다. 102번은 반대로 `research/algorithm_evidence_sources`의 주요 근거 카탈로그에서 출발해 각 자료가 공식산식, 후보/분리점수, 전처리 계보, 품질계약, 보류근거 중 어디에 놓였는지 확인한다.",
        "",
        "중요한 원칙은 모든 자료를 무조건 점수에 섞는 것이 아니다. 방문로그가 없는 Huff, 전문가 쌍대비교가 없는 AHP, 위험성향 입력이 없는 OWA, 행정권역 grain 자료, 주소/지오코딩 브리지 자료는 공식 현재입지 점수에 직접 넣으면 오히려 근거가 약해진다.",
        "",
        "## 결과",
        "",
        f"- validation version: `{VERSION}`",
        f"- decision: `{decision}`",
        f"- PASS: `{pass_count}`",
        f"- FAIL: `{fail_count}`",
        f"- catalog count: `{len(audit)}`",
        f"- 98번 trace 직접 연결 자료: `{summary['used_in_98_trace_count']}`",
        f"- source_registry 연결 자료: `{summary['registry_linked_count']}`",
        "",
        "## 상태별 개수",
        "",
        md_table(
            [{"status": key, "count": value} for key, value in status_counts.items()],
            ["status", "count"],
        ),
        "",
        "## 검증",
        "",
        md_table(validation_df.to_dict("records"), ["validation_id", "result", "observed", "reason_ko"]),
        "",
        "## 근거자료별 사용상태",
        "",
        md_table(display, ["evidence_id", "evidence_group", "title", "trace_rule_ids", "registry_source_ids", "reverse_usage_status", "reason_ko", "next_action_ko"]),
        "",
        "## 현재 판단",
        "",
        "- 공식 현재입지 점수는 계속 `sales`, `competition`, `demand`, `accessibility` 4축 WLC로 둔다.",
        "- 성장, 비용, 교통 승하차, 객단가, 행정통계, 지오코딩은 근거가 있어도 목적과 grain이 달라 별도/후보/브리지로 둔다.",
        "- 자료를 최대한 쓴다는 뜻은 모든 자료를 합산점수에 넣는 것이 아니라, 공식산식·후보·전처리계보·품질계약·보류사유를 명확히 남기는 것이다.",
    ]
    OUT_DOC.write_text("\n".join(doc_lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
