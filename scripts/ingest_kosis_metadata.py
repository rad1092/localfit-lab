from __future__ import annotations

import json
from datetime import datetime

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_id, write_raw


RUN_DATE = "20260703"
SOURCE_ID = "kosis_population_business_survival"
PROVIDER = "KOSIS"

TARGETS = [
    {
        "name": "KOSIS 통계목록 설명 엑셀",
        "url": "https://kosis.kr/openapi/file/index/openStateExpl.xls",
        "relative_path": f"{RUN_DATE}/kosis/kosis_open_state_expl.xls",
        "notes": "KOSIS OpenAPI 통계목록 이해를 위한 공식 설명 파일이다.",
    },
    {
        "name": "KOSIS OpenAPI 코드목록",
        "url": "https://kosis.kr/openapi/openApiCodeList.do",
        "relative_path": f"{RUN_DATE}/kosis/kosis_open_api_code_list.html",
        "notes": "KOSIS API 코드와 분류 확인용 공식 응답이다.",
    },
]


def main() -> None:
    keys = parse_key_file()
    rid = run_id("kosis_metadata")
    results = []
    for target in TARGETS:
        url = target["url"]
        redacted = redact_url(url)
        try:
            status, body, _headers = http_get(url, timeout=45)
            path = write_raw(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=target["name"],
                body=body,
                relative_path=target["relative_path"],
                request_url_redacted=redacted,
                request_params={"credential_ref": "KOSIS_API_KEY", "key_present": bool(keys.get("kosis_key"))},
                http_status=status,
                provider_result_message="KOSIS 메타데이터 후보 저장",
                spatial_unit="통계표 메타데이터",
                time_unit="수집일",
                quality_notes_ko=target["notes"],
            )
            results.append({"name": target["name"], "status": "success", "path": str(path), "bytes": len(body)})
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=target["name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{target['name']} 수집 실패: {exc}",
                next_action_ko="KOSIS 개발가이드에서 최신 다운로드 URL 또는 인증키 필요 여부를 확인하고 재시도한다.",
                request_url_redacted=redacted,
            )
            results.append({"name": target["name"], "status": "failed", "error": type(exc).__name__})

    summary = {"run_id": rid, "results": results, "created_at": datetime.now().isoformat(timespec="seconds")}
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
