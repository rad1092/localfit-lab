from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_id, write_raw


RUN_DATE = "20260703"
SOURCE_ID = "kosis_population_business_survival"
PROVIDER = "KOSIS"
LIST_URL = "https://kosis.kr/openapi/statisticsList.do"
OUTPUT = RAW_ROOT / RUN_DATE / "kosis" / "kosis_candidate_tables_population_business_survival.csv"

FIELDS = [
    "priority",
    "matched_keyword",
    "parent_list_id",
    "depth",
    "org_id",
    "tbl_id",
    "stat_id",
    "table_name",
    "list_id",
    "list_name",
    "send_de",
    "reason_ko",
    "raw_json_path",
]

ROOT_LIST_IDS = [
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "M",
    "N",
    "O",
    "P",
    "Q",
]

KEYWORD_REASONS = {
    "주민등록인구": "거주 수요와 배후 인구 보정에 사용한다.",
    "인구수": "거주 수요와 배후 인구 보정에 사용한다.",
    "읍면동": "행정동 단위 보정과 상권 경계 매칭에 사용한다.",
    "사업체": "상권의 업종 기반과 경제활동 밀도 보정에 사용한다.",
    "종사자": "직장 수요와 업무지구 성격 보정에 사용한다.",
    "기업생멸": "창업/폐업/생존 리스크의 외부 벤치마크로 사용한다.",
    "생존": "업종/지역 안정성의 외부 벤치마크로 사용한다.",
    "창업": "창업 진입과 업종 생태계 보정에 사용한다.",
    "폐업": "폐업 위험과 상권 안정성 보정에 사용한다.",
    "소상공인": "소상공인 업종/지역 보정에 사용한다.",
    "자영업": "자영업 업종/지역 보정에 사용한다.",
}


def request_list(key: str, parent: str) -> tuple[bytes, list[dict]]:
    params = {
        "method": "getList",
        "apiKey": key,
        "vwCd": "MT_ZTITLE",
        "format": "json",
        "jsonVD": "Y",
        "parentListId": parent,
    }
    url = LIST_URL + "?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=60)
    text = body.decode("utf-8", errors="replace")
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise RuntimeError(f"KOSIS statisticsList unexpected response for {parent}: {text[:200]}")
    return body, data


def match_keyword(name: str) -> tuple[str, str] | None:
    for keyword, reason in KEYWORD_REASONS.items():
        if keyword in name:
            return keyword, reason
    return None


def main() -> None:
    keys = parse_key_file()
    key = keys["kosis_key"]
    rid = run_id("kosis_table_search")
    queue: list[tuple[str, int]] = [(root, 0) for root in ROOT_LIST_IDS]
    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    raw_index: dict[str, str] = {}

    while queue and len(seen) < 1200:
        parent, depth = queue.pop(0)
        if parent in seen or depth > 6:
            continue
        seen.add(parent)
        url = LIST_URL + "?" + urllib.parse.urlencode(
            {
                "method": "getList",
                "apiKey": key,
                "vwCd": "MT_ZTITLE",
                "format": "json",
                "jsonVD": "Y",
                "parentListId": parent,
            }
        )
        try:
            body, rows = request_list(key, parent)
            raw_path = write_raw(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"KOSIS 통계목록 parentListId={parent}",
                body=body,
                relative_path=f"{RUN_DATE}/kosis/statistics_list/{parent}.json",
                request_url_redacted=redact_url(url, extra_values=[key]),
                request_params={
                    "method": "getList",
                    "vwCd": "MT_ZTITLE",
                    "parentListId": parent,
                    "apiKey": "<redacted>",
                },
                http_status=200,
                provider_result_code="statisticsList",
                provider_result_message=f"{len(rows)} rows",
                spatial_unit="통계목록",
                time_unit="수집일",
                quality_notes_ko="통계표 후보를 찾기 위한 KOSIS 공식 통계목록 원응답이다.",
            )
            raw_index[parent] = str(raw_path.relative_to(Path(__file__).resolve().parents[1]))
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"KOSIS 통계목록 parentListId={parent}",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"KOSIS 통계목록 parentListId={parent} 수집 실패: {exc}",
                next_action_ko="parentListId가 유효한지, KOSIS 호출 제한 또는 응답 형식 변경 여부를 확인한다.",
                request_url_redacted=redact_url(url, extra_values=[key]),
            )
            continue

        for item in rows:
            list_name = item.get("LIST_NM", "")
            table_name = item.get("TBL_NM", "")
            name = table_name or list_name
            match = match_keyword(name)
            if match:
                keyword, reason = match
                priority = "P1" if keyword in {"주민등록인구", "인구수", "읍면동", "사업체", "종사자", "기업생멸", "생존"} else "P2"
                candidates.append(
                    {
                        "priority": priority,
                        "matched_keyword": keyword,
                        "parent_list_id": parent,
                        "depth": str(depth),
                        "org_id": item.get("ORG_ID", ""),
                        "tbl_id": item.get("TBL_ID", ""),
                        "stat_id": item.get("STAT_ID", ""),
                        "table_name": table_name,
                        "list_id": item.get("LIST_ID", ""),
                        "list_name": list_name,
                        "send_de": item.get("SEND_DE", ""),
                        "reason_ko": reason,
                        "raw_json_path": raw_index.get(parent, ""),
                    }
                )

            child = item.get("LIST_ID")
            if child and child not in seen:
                queue.append((child, depth + 1))
        time.sleep(0.03)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(candidates)

    summary = {
        "run_id": rid,
        "seen_parent_list_ids": len(seen),
        "candidate_rows": len(candidates),
        "output": str(OUTPUT),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
