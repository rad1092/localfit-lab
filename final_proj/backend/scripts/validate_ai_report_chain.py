from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.repositories.commercial_area import CommercialAreaRepository  # noqa: E402
from app.schemas.commercial_area import ChatState  # noqa: E402
from app.services.chatbot_parser import extract_slots_llm, merge_state  # noqa: E402
from app.services.commercial_area import CommercialAreaService  # noqa: E402
from app.services.interpretive_report import SPEC_VERSION, _section_targets_for_violations  # noqa: E402
from app.services.report_critic import RAW_FLOAT_PATTERN  # noqa: E402
from main import app  # noqa: E402


CHART_IDS = {"C1", "C2", "C3", "C4", "C5"}

REPORT_CASES = [
    ("itaewon_korean", "3001491", "CS100001"),
    ("myeongdong_coffee", "3001492", "CS100010"),
    ("low_score_yukryu", "3130277", "CS300007"),
]

SLOT_CASES = [
    ("명동에서 카페 하려는데 5천이면 돼?", {"area_text": "명동", "industry_text": "카페", "budget": 5000}),
    ("강남역이랑 홍대 중에 어디가 나아? 커피로", {"area_text": "강남역, 홍대", "industry_text": "커피"}),
    ("1억", {"budget": 10000}),
    ("아니 업종을 분식으로 바꿀래", {"industry_text": "분식"}),
    ("거기 유동인구는 어때?", {"intent": "question"}),
    ("ㅎㅇ", {"intent": "smalltalk"}),
    ("이태원 관광특구 한식 예산 1억으로 봐줘", {"area_text": "이태원 관광특구", "industry_text": "한식", "budget": 10000}),
    ("명동 커피 8천만원", {"area_text": "명동", "industry_text": "커피", "budget": 8000}),
    ("잠실 관광특구 편의점 2억", {"area_text": "잠실 관광특구", "industry_text": "편의점", "budget": 20000}),
    ("강남역 네일샵 자본 7000", {"area_text": "강남역", "industry_text": "네일샵", "budget": 7000}),
    ("홍대입구에서 양식음식점 할래", {"area_text": "홍대입구", "industry_text": "양식음식점"}),
    ("연남동 분식 5000만", {"area_text": "연남동", "industry_text": "분식", "budget": 5000}),
    ("성수동 미용실 1.5억", {"area_text": "성수동", "industry_text": "미용실", "budget": 15000}),
    ("을지로 호프 6천만", {"area_text": "을지로", "industry_text": "호프", "budget": 6000}),
    ("마포역 부동산중개업 예산 9000만원", {"area_text": "마포역", "industry_text": "부동산중개업", "budget": 9000}),
    ("여의도 한식 vs 중식 비교해줘", {"area_text": "여의도", "industry_text": "한식, 중식", "intent": "compare"}),
    ("카페로 바꿔", {"industry_text": "카페"}),
    ("예산은 3천", {"budget": 3000}),
    ("명동 말고 이태원으로", {"area_text": "이태원"}),
    ("커피 말고 제과점", {"industry_text": "제과점"}),
    ("예산 12000만원", {"budget": 12000}),
    ("CS100010로 봐줘", {"industry_text": "CS100010"}),
    ("3001491 한식 10000", {"area_text": "3001491", "industry_text": "한식", "budget": 10000}),
    ("거기 경쟁은?", {"intent": "question"}),
    ("뭐 할 수 있어?", {"intent": "question"}),
    ("아무거나 추천", {"intent": "question"}),
]


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def validate_reports(
    client: TestClient,
    failures: list[str],
    *,
    require_provider_usage: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, area_code, industry_code in REPORT_CASES:
        started = time.time()
        response = client.post("/api/reports/single/generate", json={"area_code": area_code, "business_type": industry_code})
        row: dict[str, Any] = {
            "case": name,
            "status": response.status_code,
            "elapsed_sec": round(time.time() - started, 2),
        }
        _assert(response.status_code == 200, f"{name}: expected HTTP 200, got {response.status_code}", failures)
        if response.status_code == 200:
            data = response.json()
            markdown = data.get("markdown_body") or ""
            display = _json(data.get("facts_pack_display") or {})
            visualization = _json(data.get("visualization_data") or [])
            facts_pack = ((data.get("indicator_pack") or {}).get("facts_pack") or {})
            sales_trend_count = len(((facts_pack.get("sales_block") or {}).get("sales_trend") or []))
            area_top_count = len(((facts_pack.get("sales_block") or {}).get("area_top_industries") or []))
            alternatives_count = len(facts_pack.get("alternatives") or [])
            evidence_frame_count = len(data.get("evidence_frames") or [])
            chart_refs = sorted(set(re.findall(r"\[CHART:(C[1-5])\]", markdown)))
            news_evidence = data.get("news_evidence") or []
            narrative_text = " ".join(
                [
                    data.get("trend_analysis") or "",
                    data.get("user_fit") or "",
                    " ".join(data.get("risk_factors") or []),
                    " ".join(data.get("action_plan") or []),
                ]
            )
            narrative_news_markers = {int(value) for value in re.findall(r"\[NEWS:(\d+)\]", narrative_text)}
            markdown_news_markers = {int(value) for value in re.findall(r"\[근거 (\d+)\]", markdown)}
            row.update(
                {
                    "quality": data.get("quality_status"),
                    "validation_issues": data.get("validation_issues"),
                    "industry": data.get("industry_name"),
                    "score": (data.get("header_block") or {}).get("score"),
                    "percentile": (data.get("header_block") or {}).get("percentile"),
                    "chart_refs": chart_refs,
                    "markdown_raw_float_hits": RAW_FLOAT_PATTERN.findall(markdown),
                    "display_raw_float_hits": RAW_FLOAT_PATTERN.findall(display),
                    "visualization_raw_float_hits": RAW_FLOAT_PATTERN.findall(visualization),
                    "limitations_count": len(data.get("limitations") or []),
                    "chart_source_counts": {
                        "sales_trend": sales_trend_count,
                        "area_top_industries": area_top_count,
                        "alternatives": alternatives_count,
                    },
                    "evidence_frame_count": evidence_frame_count,
                    "condition_evidence_count": len(news_evidence),
                    "condition_evidence_marker_leaks": {
                        "narrative": sorted(narrative_news_markers),
                        "markdown": sorted(markdown_news_markers),
                    },
                    "token_usage": data.get("token_usage"),
                    "cache_meta": data.get("cache_meta"),
                }
            )
            _assert(data.get("quality_status") == "pass", f"{name}: quality_status is not pass", failures)
            _assert(not data.get("validation_issues"), f"{name}: validation issues exist", failures)
            _assert(set(chart_refs) == CHART_IDS, f"{name}: missing chart refs {sorted(CHART_IDS - set(chart_refs))}", failures)
            _assert(not row["markdown_raw_float_hits"], f"{name}: raw float in markdown", failures)
            _assert(not row["display_raw_float_hits"], f"{name}: raw float in facts display", failures)
            _assert(not row["visualization_raw_float_hits"], f"{name}: raw float in visualization data", failures)
            _assert(row["limitations_count"] >= 1, f"{name}: limitations section is empty", failures)
            _assert(bool(facts_pack), f"{name}: indicator_pack.facts_pack is missing", failures)
            _assert(sales_trend_count > 0, f"{name}: chart C2 source sales trend is empty", failures)
            _assert(area_top_count > 0, f"{name}: chart C3 source top industries is empty", failures)
            _assert(alternatives_count >= 2, f"{name}: chart C4 source alternatives are insufficient", failures)
            _assert(evidence_frame_count > 0, f"{name}: evidence frames are missing", failures)
            _assert("[NEWS:" not in markdown, f"{name}: internal news marker leaked into markdown", failures)
            _assert(
                not narrative_news_markers,
                f"{name}: internal condition-evidence marker leaked into the narrative",
                failures,
            )
            _assert(
                not markdown_news_markers,
                f"{name}: visible evidence-number annotation leaked into PDF markdown",
                failures,
            )
            if news_evidence:
                _assert(
                    all(item.get("condition_fit") and item.get("decision_use") for item in news_evidence),
                    f"{name}: condition evidence metadata is incomplete",
                    failures,
                )
                _assert(
                    all(item.get("structured_score_impact") == "none" for item in news_evidence),
                    f"{name}: unstructured evidence changed structured score role",
                    failures,
                )
                _assert("조건 맞춤 외부 자료" in markdown, f"{name}: condition evidence section missing", failures)
            token_usage = data.get("token_usage") or {}
            if require_provider_usage:
                _assert(token_usage.get("estimated") is False, f"{name}: provider token usage was not captured", failures)
                _assert("cache_read_tokens" in token_usage, f"{name}: cache_read_tokens missing", failures)
            else:
                _assert(
                    token_usage.get("model") == "gpt-5.4-mini",
                    f"{name}: fallback model contract mismatch",
                    failures,
                )
            _assert(int(token_usage.get("total_tokens") or 0) > 0, f"{name}: total token usage missing", failures)
        rows.append(row)

    response = client.post("/api/reports/single/generate", json={"area_code": "3001491", "business_type": "CS100001"})
    cache = response.json() if response.status_code == 200 else {}
    rows.append({"case": "cache_repeat", "status": response.status_code, "cache_meta": cache.get("cache_meta"), "token_usage": cache.get("token_usage")})
    _assert(response.status_code == 200, "cache repeat: HTTP failure", failures)
    cache_hit = (cache.get("cache_meta") or {}).get("cache_hit") is True
    if require_provider_usage:
        _assert(cache_hit, "cache repeat: cache_meta.cache_hit is not true", failures)
        _assert((cache.get("token_usage") or {}).get("cache_hit") is True, "cache repeat: token_usage.cache_hit is not true", failures)
        _assert((cache.get("token_usage") or {}).get("estimated") is False, "cache repeat: provider token usage missing", failures)
    else:
        _assert(not cache_hit, "cache repeat: fallback result must not be cached", failures)
    return rows


def validate_artifacts(client: TestClient, failures: list[str]) -> dict[str, Any]:
    email = f"codex_chain_{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!"
    register = client.post("/api/auth/register", json={"email": email, "password": password, "nickname": "codex-chain"})
    login = client.post("/api/auth/login", data={"username": email, "password": password})
    _assert(register.status_code == 200, f"register failed: {register.status_code}", failures)
    _assert(login.status_code == 200, f"login failed: {login.status_code}", failures)
    if login.status_code != 200:
        return {"register": register.status_code, "login": login.status_code}

    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    data = client.post("/api/reports/single/generate", json={"area_code": "3001491", "business_type": "CS100001"}).json()
    save = client.post("/api/reports/save", json={"report_data": data}, headers=headers)
    out: dict[str, Any] = {"register": register.status_code, "login": login.status_code, "save": save.status_code}
    _assert(save.status_code == 200, f"save failed: {save.status_code}", failures)
    if save.status_code != 200:
        return out

    saved = save.json()
    report_id = saved["id"]
    artifacts = (saved["report_data"] or {}).get("artifact_paths") or {}
    out["report_id"] = report_id
    out["artifact_paths"] = artifacts

    for fmt in ["pdf"]:
        download = client.get(f"/api/reports/{report_id}/download?format={fmt}", headers=headers)
        out[f"download_{fmt}"] = {
            "status": download.status_code,
            "content_type": download.headers.get("content-type"),
            "bytes": len(download.content),
        }
        _assert(download.status_code == 200, f"download {fmt} failed: {download.status_code}", failures)
        _assert(len(download.content) > 1000, f"download {fmt} too small", failures)

    chart_info = {}
    for chart_id, chart_path in (artifacts.get("chart_paths") or {}).items():
        path = Path(chart_path)
        _assert(path.exists(), f"chart missing: {chart_id}", failures)
        if path.exists():
            with Image.open(path) as image:
                chart_info[chart_id] = {"bytes": path.stat().st_size, "size": list(image.size)}
                _assert(image.size[0] >= 1000 and image.size[1] >= 600, f"chart too small: {chart_id} {image.size}", failures)
    out["charts"] = chart_info
    _assert(set(chart_info) == CHART_IDS, "not all chart PNGs were generated", failures)
    return out


def validate_chatbot(client: TestClient, failures: list[str], run_slot_suite: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    message = "이태원 관광특구에서 한식음식점을 예산 1억으로 시작하면 괜찮을까?"
    response = client.post("/api/chatbot/chat", json={"message": message})
    summary["chat_route_status"] = response.status_code
    _assert(response.status_code == 200, f"chat route failed: {response.status_code}", failures)
    if response.status_code == 200:
        data = response.json()
        state = data.get("state") or {}
        conversational_text = data.get("text") or ""
        summary["conversation_type"] = data.get("type")
        summary["conversation_text"] = conversational_text
        summary["state"] = state
        _assert(data.get("type") == "text", "chat route skipped the conversation-first response", failures)
        _assert(len(conversational_text.strip()) >= 20, "conversation-first response is empty or too short", failures)
        _assert(state.get("area_code") == "3001491", "chat state area_code mismatch", failures)
        _assert(state.get("industry_code") == "CS100001", "chat state industry_code mismatch", failures)
        _assert(state.get("budget") == 10000, "chat state budget mismatch", failures)

        report_response = client.post(
            "/api/chatbot/chat",
            json={
                "message": "이 조건으로 상세 리포트 만들어줘",
                "state": state,
                "history": [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": conversational_text},
                ],
            },
        )
        summary["report_route_status"] = report_response.status_code
        _assert(report_response.status_code == 200, f"explicit chatbot report failed: {report_response.status_code}", failures)
        if report_response.status_code == 200:
            report_data = report_response.json()
            report = (((report_data.get("report") or {}).get("compact_response") or {}).get("quick_judgement")) or ""
            detailed = ((report_data.get("report") or {}).get("compact_response") or {}).get("ai_explanation") or ""
            facts = ((report_data.get("report") or {}).get("condition") or {})
            saved_report = (report_data.get("report") or {}).get("compact_response") or {}
            summary["report_type"] = report_data.get("type")
            summary["quick_sentence_count"] = len(re.findall(r"(?:다|요|죠|까)\.", report))
            summary["quick_judgement"] = report
            _assert(report_data.get("type") == "report", "explicit chatbot request did not return a report", failures)
            _assert(2 <= summary["quick_sentence_count"] <= 6, "compact report response has an unexpected length", failures)
            _assert(not RAW_FLOAT_PATTERN.findall(report + detailed + _json(facts) + _json(saved_report)), "compact response contains raw float", failures)

    if run_slot_suite:
        db = SessionLocal()
        try:
            service = CommercialAreaService(CommercialAreaRepository(db))
            matched = 0
            slot_rows = []
            base_state = ChatState(area_code="3001492", area_name="명동 남대문 북창동 다동 무교동 관광특구", industry_code="CS100010", business_type="커피-음료")
            for message, expected in SLOT_CASES:
                state_for_llm = base_state if message in {"1억", "거기 유동인구는 어때?", "거기 경쟁은?", "카페로 바꿔", "예산은 3천"} else None
                slots = extract_slots_llm(message, state_for_llm)
                slot_data = slots.model_dump() if slots else {}
                ok = True
                for key, expected_value in expected.items():
                    actual = slot_data.get(key)
                    if key in {"area_text", "industry_text"} and expected_value:
                        ok = ok and actual is not None and str(expected_value).replace(" ", "") in str(actual).replace(" ", "")
                    else:
                        ok = ok and actual == expected_value
                if ok:
                    matched += 1
                slot_rows.append({"message": message, "expected": expected, "actual": slot_data, "ok": ok})
            accuracy = matched / len(SLOT_CASES)
            summary["slot_accuracy"] = accuracy
            summary["slot_matched"] = matched
            summary["slot_total"] = len(SLOT_CASES)
            summary["slot_failures"] = [row for row in slot_rows if not row["ok"]]
            _assert(accuracy >= 0.90, f"slot extraction accuracy below 90%: {accuracy:.1%}", failures)

            merged, missing, options, pending, _slots = merge_state("이태원 관광특구 한식 예산 1억", service, None)
            summary["merge_state_smoke"] = merged.model_dump()
            _assert(merged.area_code == "3001491", "merge_state area_code mismatch", failures)
            _assert(merged.industry_code == "CS100001", "merge_state industry_code mismatch", failures)
            _assert(merged.budget == 10000, "merge_state budget mismatch", failures)
            _assert(not missing and not options and pending is None, "merge_state unexpectedly incomplete", failures)
        finally:
            db.close()

    return summary


def validate_section_repair_routing(failures: list[str]) -> dict[str, Any]:
    cases = {
        "axis": _section_targets_for_violations(
            ["[AXIS_NO_EVIDENCE] [field=axis_interpretations] 축별 해석 4개 미만"]
        ),
        "citation": _section_targets_for_violations(
            ["[FAKE_CITATION] [field=axis_interpretations[0].frame_citations] 존재하지 않는 각주 번호: 9"]
        ),
        "alternative": _section_targets_for_violations(
            ["[MISSING_ALTERNATIVES] [field=alternatives] 대안 상권 2개 미만"]
        ),
        "user": _section_targets_for_violations(
            ["[MISSING_USER_COND] [field=user_fit] 예산 조건 미등장: 1억"]
        ),
        "format": _section_targets_for_violations(
            [
                "[FORMAT] [field=executive_interpretation] raw float 노출",
                "[FORMAT] [field=axis_interpretations[0].meaning] raw float 노출",
                "[FORMAT] [field=trend_analysis] raw float 노출",
                "[FORMAT] [field=user_fit] raw float 노출",
            ]
        ),
    }
    _assert("axis" in cases["axis"], "section repair routing: AXIS_NO_EVIDENCE does not target axis", failures)
    _assert("axis" in cases["citation"], "section repair routing: FAKE_CITATION does not target axis", failures)
    _assert("trend_alternatives" in cases["alternative"], "section repair routing: MISSING_ALTERNATIVES does not target alternatives", failures)
    _assert("user_risk" in cases["user"], "section repair routing: MISSING_USER_COND does not target user_risk", failures)
    _assert({"header", "axis", "trend_alternatives", "user_risk"}.issubset(set(cases["format"])), "section repair routing: FORMAT does not target all narrative sections", failures)
    return {"spec_version": SPEC_VERSION, "routing": cases}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot-suite", action="store_true", help="Run 26 LLM slot extraction cases.")
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Validate the deterministic fallback path without requiring provider usage metadata.",
    )
    args = parser.parse_args()

    failures: list[str] = []
    client = TestClient(app)
    result = {
        "reports": validate_reports(
            client,
            failures,
            require_provider_usage=not args.allow_fallback,
        ),
        "artifacts": validate_artifacts(client, failures),
        "chatbot": validate_chatbot(client, failures, args.slot_suite),
        "section_repair": validate_section_repair_routing(failures),
        "failures": failures,
    }
    print(_json(result))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
