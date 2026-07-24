from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any, Iterable

from app.core.settings import DATA_ROOT, WORKSPACE_ROOT
from app.database import SessionLocal
from app.models.commercial_area import ExternalAPILog
from app.services.indicator_pack import DB_PATH


NEWS_EVIDENCE_PATH = DATA_ROOT / "_silver" / "silver_news_evidence.csv"
RETRIEVAL_VERSION = "condition-evidence.v9-two-tier-budget-scope-copy-bounded"
DECISION_SUPPORT_TIER = "decision_support"
REFERENCE_MONITORING_TIER = "reference_monitoring"
NAVER_NEWS_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/news"
NAVER_SIGNAL_KEYWORDS = {
    "development": ("개발", "도시계획", "정비사업", "재개발", "재건축", "착공", "준공", "공급공고"),
    "transport": ("교통", "지하철", "GTX", "버스", "도로", "보행", "역세권", "환승"),
    "commercial": ("상권", "점포", "매출", "골목", "전통시장", "상가", "임대", "공실"),
    "small_business_policy": ("소상공인", "자영업", "창업", "중소벤처", "지원사업", "정책자금"),
    "tourism_event": ("관광", "축제", "행사", "문화", "공연", "박람회"),
    "risk": ("폐업", "침체", "통제", "화재", "재난", "공사", "위험", "규제", "폭염", "침수"),
}
BLOCKED_NEWS_DOMAINS = {
    "blog.naver.com",
    "cafe.naver.com",
    "post.naver.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "facebook.com",
    "x.com",
}
DISTRICTS = (
    "강남구",
    "강동구",
    "강북구",
    "강서구",
    "관악구",
    "광진구",
    "구로구",
    "금천구",
    "노원구",
    "도봉구",
    "동대문구",
    "동작구",
    "마포구",
    "서대문구",
    "서초구",
    "성동구",
    "성북구",
    "송파구",
    "양천구",
    "영등포구",
    "용산구",
    "은평구",
    "종로구",
    "중구",
    "중랑구",
)

AREA_STOPWORDS = {
    "관광특구",
    "골목상권",
    "발달상권",
    "전통시장",
    "상권",
    "서울",
    "서울시",
    "일대",
    "출구",
}
INDUSTRY_ALIASES = {
    "커피": {"커피", "카페", "음료", "카페·음료"},
    "음료": {"커피", "카페", "음료", "카페·음료"},
    "한식": {"한식", "한식음식점"},
    "중식": {"중식", "중식음식점", "중국집"},
    "일식": {"일식", "일식음식점"},
    "양식": {"양식", "양식음식점", "레스토랑"},
    "제과": {"제과", "제빵", "베이커리", "빵집"},
    "반찬": {"반찬", "반찬가게"},
    "편의점": {"편의점"},
    "숙박": {"숙박", "호텔"},
    "의류": {"의류", "패션"},
    "미용": {"미용", "헤어", "네일"},
    "교육": {"교육", "학원", "교습"},
    "호프": {"호프", "주점", "야장", "먹자골목"},
    "간이주점": {"호프", "주점", "야장", "먹자골목"},
}
STRONG_INDUSTRY_SPECIFIC_TERMS = {
    "먹자골목",
    "야장",
    "외식업",
    "식음료",
    "베이커리",
    "카페",
    "음식점",
    "주점",
    "의원",
    "병원",
    "한의원",
    "치과",
    "의류",
    "패션",
    "미용실",
    "세탁소",
    "학원",
    "교습",
    "슈퍼마켓",
    "편의점",
    "부동산중개",
}
SIGNAL_LABELS = {
    "development": "개발·정비",
    "transport": "교통·접근",
    "commercial": "상권 변화",
    "small_business_policy": "소상공인 정책",
    "tourism_event": "관광·행사",
    "risk": "위험 요인",
    "general": "일반 이슈",
}
LOCATION_SIGNAL_TYPES = set(SIGNAL_LABELS) - {"general"}
BUDGET_TERMS = {
    "정책자금",
    "지원금",
    "보조금",
    "융자",
    "대출",
    "보증",
    "금리",
    "자부담",
    "임대료",
    "임차료",
    "월세",
    "보증금",
    "권리금",
    "시설비",
    "공사비",
    "창업비",
    "운영비",
    "자금",
}
BUSINESS_POLICY_TERMS = {
    "소상공인",
    "자영업",
    "사업자",
    "상인",
    "중소기업",
    "예비창업",
    "창업가",
    "창업기업",
    "창업 희망",
    "창업 지원",
}
LOW_VALUE_EVIDENCE_TERMS = {"주간구인", "구인정보", "채용공고", "인사발령", "부고"}
SENSITIVE_STORY_TERMS = {"유족", "추모", "희생자", "참사"}
CURRENT_OPERATION_TERMS = {"영업", "통제", "폐업", "공사", "규제", "운영", "재개", "중단", "지원"}
INDUSTRY_INDEPENDENT_SIGNALS = {"development", "transport"}
PERSISTENT_LOCATION_TERMS = {
    "개발",
    "공사",
    "착공",
    "준공",
    "정비",
    "개선",
    "신설",
    "확장",
    "개통",
    "도로",
    "교통",
    "보행",
    "동선",
    "지하철",
    "정류장",
    "환승",
}
TRANSIENT_OR_SINGLE_BUSINESS_TERMS = {
    "개점",
    "오픈",
    "들어선다",
    "폐점",
    "폐업",
    "휴업",
    "집회",
    "시위",
    "무정차",
}
POLITICAL_PROMISE_TERMS = {
    "후보",
    "예비후보",
    "당선인",
    "선거",
    "공약",
    "재선",
    "탈환",
    "민선 9기",
    "민선9기",
}
POLITICAL_TITLE_PATTERNS = (
    re.compile(
        r"구청장.{0,40}(청사진|비전|공약|둘 것|할 것|챙길 것|하겠다|챙기겠다|속도전|신속 추진)"
    ),
    re.compile(
        r"(청사진|비전|공약|속도전|신속 추진).{0,40}(발표|공개|제시|하겠다|할 것|챙길 것)"
    ),
)
NON_SEOUL_REGION_TERMS = {
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기도",
    "강원도",
    "충청북도",
    "충청남도",
    "전라북도",
    "전북특별자치도",
    "전라남도",
    "경상북도",
    "경상남도",
    "제주도",
    "제주특별자치도",
}
CONCRETE_LOCATION_CHANGE_TERMS = {
    "착공",
    "준공",
    "개통",
    "공사",
    "정비사업",
    "개선 사업",
    "재개발",
    "재건축",
    "도시계획",
    "지구단위계획",
    "신설",
    "확장",
    "도로",
    "보행로",
    "환승",
}
NEGATIVE_TERMS = {"폐업", "침체", "공실", "통제", "공사", "지연", "위험", "규제", "감소", "중단", "축소"}
POSITIVE_TERMS = {"지원", "개통", "준공", "개장", "유치", "확대", "활성화", "개선", "선정", "증가"}
SCOPE_LABELS = {
    "exact_area": "선택 상권 직접 일치",
    "nearby": "인접 생활권 일치",
    "district": "자치구 범위 일치",
    "seoul": "서울 전역 적용",
    "national": "전국 정책 적용",
}
DECISION_LABELS = {
    "cost_policy": "비용·자금 계획",
    "risk": "운영 위험",
    "development": "개발·공사 일정",
    "accessibility": "접근·유입 변화",
    "competition_market": "상권·경쟁 변화",
    "demand": "수요의 지속성",
}
DECISION_USE_TERMS = {
    "cost_policy": (
        "지원사업",
        "정책자금",
        "자부담",
        "신청",
        "지원",
        "대출",
        "보증",
        "임차",
        "임대",
    ),
    "risk": ("영업 차질", "침수", "화재", "재난", "통제", "공사", "지연", "규제", "중단"),
    "development": (
        "도시계획",
        "정비사업",
        "재개발",
        "재건축",
        "지하도로",
        "착공",
        "준공",
        "공사",
        "개발",
        "정비",
        "신설",
        "확장",
        "도로",
    ),
    "accessibility": (
        "지하도로",
        "지하철",
        "정류장",
        "무정차",
        "교통",
        "버스",
        "도로",
        "보행",
        "동선",
        "환승",
        "개통",
        "운영",
    ),
    "competition_market": (
        "신규 점포",
        "전통시장",
        "폐점",
        "폐업",
        "개점",
        "오픈",
        "공실",
        "임대",
        "점포",
        "상권",
        "매출",
    ),
    "demand": ("관광", "축제", "행사", "공연", "수요", "유동"),
}


@dataclass(frozen=True)
class LocationContext:
    area_name: str
    district: str
    exact_terms: tuple[str, ...]
    nearby_terms: tuple[str, ...]


class NaverNewsConnectionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int = 502,
        log_status: int = 502,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.log_status = log_status


def _clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _valid_secret(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and not text.startswith("<") and "입력" not in text and "발급" not in text)


def _naver_credentials() -> tuple[str, str, str]:
    module_path = WORKSPACE_ROOT / "scripts" / "ingest_common.py"
    if not module_path.exists():
        raise NaverNewsConnectionError("NAVER 연결 설정을 읽을 수 없습니다.", http_status=424, log_status=424)
    try:
        spec = importlib.util.spec_from_file_location("_localfit_news_ingest_common", module_path)
        if not spec or not spec.loader:
            raise RuntimeError("설정 모듈을 불러올 수 없습니다.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        keys = module.parse_key_file()
    except Exception as error:
        raise NaverNewsConnectionError(
            "NAVER 연결 설정을 읽을 수 없습니다.", http_status=424, log_status=424
        ) from error

    endpoint = str(keys.get("naver_api_hub_endpoint") or NAVER_NEWS_ENDPOINT).strip()
    api_key_id = str(
        keys.get("naver_api_hub_client_id") or keys.get("naver_api_hub_api_key_id") or ""
    ).strip()
    api_key = str(
        keys.get("naver_api_hub_client_secret") or keys.get("naver_api_hub_api_key") or ""
    ).strip()
    if not (_valid_secret(api_key_id) and _valid_secret(api_key)):
        raise NaverNewsConnectionError(
            "NAVER 뉴스 API 키가 설정되지 않았습니다.", http_status=424, log_status=424
        )
    return endpoint, api_key_id, api_key


def _redacted_endpoint(endpoint: str) -> str:
    parts = urllib.parse.urlsplit(endpoint)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _log_naver_call(endpoint: str, status_code: int, response_time_ms: int) -> None:
    db = SessionLocal()
    try:
        db.add(
            ExternalAPILog(
                api_name="NAVER News Search",
                endpoint=_redacted_endpoint(endpoint),
                status_code=status_code,
                response_time_ms=response_time_ms,
                call_type="GET",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _request_naver_news(
    query: str,
    *,
    display: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], int, int]:
    started = time.perf_counter()
    endpoint = NAVER_NEWS_ENDPOINT
    log_status = 502
    try:
        endpoint, api_key_id, api_key = _naver_credentials()
        url = f"{endpoint}?{urllib.parse.urlencode({'query': query, 'display': display, 'start': 1, 'sort': 'date', 'format': 'json'})}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "LocalFitLab/1.0",
                "X-NCP-APIGW-API-KEY-ID": api_key_id,
                "X-NCP-APIGW-API-KEY": api_key,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            log_status = int(response.getcode() or 200)
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            log_status = 502
            raise NaverNewsConnectionError("NAVER 뉴스 응답 형식이 올바르지 않습니다.")
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        return payload, log_status, elapsed_ms
    except NaverNewsConnectionError as error:
        log_status = error.log_status
        raise
    except urllib.error.HTTPError as error:
        log_status = int(error.code or 502)
        raise NaverNewsConnectionError(
            f"NAVER 뉴스 API가 HTTP {log_status}을 반환했습니다.",
            http_status=502,
            log_status=log_status,
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        log_status = 504
        raise NaverNewsConnectionError(
            "NAVER 뉴스 API에 연결하지 못했습니다.", http_status=504, log_status=504
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        log_status = 502
        raise NaverNewsConnectionError("NAVER 뉴스 응답을 해석하지 못했습니다.") from error
    finally:
        _log_naver_call(
            endpoint,
            log_status,
            max(1, int((time.perf_counter() - started) * 1000)),
        )


def _naver_published_date(value: str) -> str:
    try:
        return parsedate_to_datetime(str(value or "")).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(value or "").strip()[:10]


def _live_signal_types(text: str) -> str:
    signals = [label for label, terms in NAVER_SIGNAL_KEYWORDS.items() if any(term in text for term in terms)]
    return ";".join(signals or ["general"])


def _live_news_query(payload: dict[str, Any]) -> str:
    area_name = str(payload.get("area_name") or "").strip()
    return f"{area_name or '서울'} 상권"


def fetch_live_naver_news(
    payload: dict[str, Any],
    *,
    display: int = 20,
    timeout_seconds: float = 5.0,
) -> list[dict[str, str]]:
    query = _live_news_query(payload)
    response, _, _ = _request_naver_news(
        query,
        display=max(1, min(display, 100)),
        timeout_seconds=timeout_seconds,
    )
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: dict[str, dict[str, str]] = {}
    for item in response.get("items") or []:
        title = _clean_text(item.get("title"))[:320]
        summary = _clean_text(item.get("description"))[:700]
        original_url = str(item.get("originallink") or item.get("link") or "").strip()
        parts = urllib.parse.urlsplit(original_url)
        domain = parts.netloc.lower().removeprefix("www.")
        if not title or parts.scheme not in {"http", "https"} or not domain or domain in BLOCKED_NEWS_DOMAINS:
            continue
        published_date = _naver_published_date(str(item.get("pubDate") or ""))
        if not published_date:
            continue
        searchable = f"{title} {summary}"
        region_hints = [district for district in DISTRICTS if district in searchable]
        if "서울" in searchable:
            region_hints.append("서울특별시")
        evidence_id = hashlib.sha256(
            f"{original_url}|{title}|{published_date}".encode("utf-8")
        ).hexdigest()[:24]
        records[evidence_id] = {
            "evidence_id": evidence_id,
            "source_id": "naver_api_hub_news",
            "source_group": "news_search",
            "source_grade": "B",
            "provider": domain,
            "dataset_name": "NAVER 뉴스 검색",
            "title": title,
            "summary": summary,
            "original_url": original_url,
            "published_date": published_date,
            "collected_at": collected_at,
            "region_hints": ";".join(dict.fromkeys(region_hints)),
            "industry_hints": "",
            "signal_types": _live_signal_types(searchable),
            "query_text": query,
            "content_sha256": hashlib.sha256(f"{title}\n{summary}".encode("utf-8")).hexdigest(),
            "raw_path": "",
            "score_role": "evidence_only",
            "usage_note": "정량 점수에는 반영하지 않고 상세 리포트 생성 시점의 정성 근거로만 사용",
        }
    return list(records.values())


def check_naver_news_connection() -> dict[str, Any]:
    payload, status_code, response_time_ms = _request_naver_news(
        "서울 상권",
        display=1,
        timeout_seconds=5.0,
    )
    return {
        "status": "healthy",
        "http_status": status_code,
        "response_time_ms": response_time_ms,
        "item_count": len(payload.get("items") or []),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _parse_date(value: str) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@lru_cache(maxsize=8)
def _load_rows(path_text: str, mtime_ns: int) -> tuple[dict[str, str], ...]:
    path = Path(path_text)
    if not path.exists():
        return tuple()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def _rows() -> tuple[dict[str, str], ...]:
    if not NEWS_EVIDENCE_PATH.exists():
        return tuple()
    return _load_rows(str(NEWS_EVIDENCE_PATH), NEWS_EVIDENCE_PATH.stat().st_mtime_ns)


def _row_identity(row: dict[str, str]) -> str:
    url = str(row.get("original_url") or "").strip()
    if url:
        parts = urllib.parse.urlsplit(url)
        normalized_url = urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, "")
        )
        if normalized_url:
            return f"url:{normalized_url}"
    evidence_id = str(row.get("evidence_id") or "").strip()
    if evidence_id:
        return f"id:{evidence_id}"
    return "text:" + hashlib.sha256(
        f"{_clean_text(row.get('title'))}|{row.get('published_date', '')}".encode("utf-8")
    ).hexdigest()


def merge_news_evidence_rows(extra_rows: Iterable[dict[str, str]] = ()) -> tuple[dict[str, str], ...]:
    """Combine durable official/news corpus rows with live search rows and remove duplicates."""

    merged: dict[str, dict[str, str]] = {}
    grade_rank = {"A": 3, "B": 2, "C": 1}
    for source_row in (*_rows(), *tuple(extra_rows)):
        row = dict(source_row)
        identity = _row_identity(row)
        current = merged.get(identity)
        candidate_rank = (
            grade_rank.get(str(row.get("source_grade") or "").upper(), 0),
            int(row.get("source_group") != "news_search"),
            len(str(row.get("summary") or "")),
        )
        current_rank = (
            grade_rank.get(str((current or {}).get("source_grade") or "").upper(), 0),
            int(bool(current) and current.get("source_group") != "news_search"),
            len(str((current or {}).get("summary") or "")),
        )
        if current is None or candidate_rank > current_rank:
            merged[identity] = row
    return tuple(merged.values())


def _area_terms(area_name: str) -> set[str]:
    def usable(term: str) -> bool:
        compact_term = re.sub(r"\s+", "", term)
        return bool(
            len(compact_term) >= 2
            and compact_term not in AREA_STOPWORDS
            and not re.fullmatch(r"\d+번(?:출구)?", compact_term)
        )

    cleaned = re.sub(r"[·,/()\[\]-]+", " ", area_name or "")
    terms = {part for part in cleaned.split() if usable(part)}
    without_kind = re.sub(r"관광특구|골목상권|발달상권|전통시장", " ", cleaned)
    terms.update(part for part in without_kind.split() if usable(part))
    compact = re.sub(r"\s+", "", cleaned)
    if usable(compact):
        terms.add(compact)
    terms.update(
        token
        for token in re.findall(r"[0-9A-Za-z가-힣]{2,}?(?:동|가|역|로|길)", compact)
        if usable(token)
    )
    return terms


@lru_cache(maxsize=2048)
def _location_context(area_code: str, fallback_area_name: str) -> LocationContext:
    area_name = fallback_area_name
    district = ""
    lookup_text = ""
    if area_code and Path(DB_PATH).exists():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    """
                    SELECT area_name, district_name, display_label, search_text
                    FROM location_lookup
                    WHERE area_code = ?
                    LIMIT 1
                    """,
                    (area_code,),
                ).fetchone()
            if row:
                area_name = str(row[0] or fallback_area_name)
                district = str(row[1] or "")
                lookup_text = f"{row[2] or ''} {row[3] or ''}"
        except sqlite3.Error:
            pass

    exact_terms = _area_terms(area_name)
    nearby_terms: set[str] = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", lookup_text):
        if len(token) < 2 or token.isdigit() or token in AREA_STOPWORDS or token == district:
            continue
        if token in exact_terms or token == area_code:
            continue
        if token.endswith(("동", "가", "역", "로", "길")):
            nearby_terms.add(token)
    return LocationContext(
        area_name=area_name,
        district=district,
        exact_terms=tuple(sorted(exact_terms, key=lambda value: (-len(value), value))),
        nearby_terms=tuple(sorted(nearby_terms, key=lambda value: (-len(value), value))),
    )


def _industry_terms(industry_name: str) -> set[str]:
    cleaned = re.sub(r"[·,/()\[\]-]+", " ", industry_name or "")
    terms = {part for part in cleaned.split() if len(part) >= 2 and part not in {"업", "업종", "서비스업"}}
    for key, aliases in INDUSTRY_ALIASES.items():
        if key in cleaned:
            terms.update(aliases)
    return terms


def _age_days(value: str) -> int:
    published = _parse_date(value)
    if not published:
        return 9999
    return max(0, (date.today() - published).days)


def _signal_set(row: dict[str, str]) -> set[str]:
    return {item for item in re.split(r"[;|]", row.get("signal_types", "")) if item}


def _budget_value(payload: dict[str, Any]) -> int | None:
    user_condition = payload.get("user_condition") or {}
    raw = user_condition.get("budget") or payload.get("budget")
    if raw in (None, "", 0, "0"):
        return None
    try:
        value = int(float(str(raw).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _budget_display(value: int | None) -> str:
    if not value:
        return "예산 미입력"
    if value >= 10000:
        eok = value / 10000
        return f"예산 {eok:g}억원"
    return f"예산 {value:,}만원"


def _location_match(row: dict[str, str], context: LocationContext) -> tuple[str, str]:
    title = _clean_text(row.get("title"))
    region_hints = _clean_text(row.get("region_hints"))
    source_group = str(row.get("source_group") or "")
    provider = _clean_text(row.get("provider"))
    provider_district = next((district for district in DISTRICTS if provider.startswith(district)), "")

    if source_group == "seoul_district_official":
        if "서울시" in title or "서울특별시" in title:
            return "seoul", "서울특별시"
        if provider_district == context.district:
            return "district", context.district
        if provider_district and not (
            "서울시" in title or "서울특별시" in title
        ):
            return "", ""
        title_scope = title
    else:
        title_scope = f"{title} {region_hints}"

    # 위치는 제목·구조화 지역 힌트로만 판정한다. 본문 요약에 언급된 홍보관·비교 지역이
    # 선택 상권의 기사처럼 승격되는 오탐을 막기 위해 summary는 업종·예산 판정에만 쓴다.
    content_scope = title_scope
    for term in context.exact_terms:
        if term in content_scope:
            return "exact_area", term
    for term in context.nearby_terms:
        if term in content_scope:
            return "nearby", term
    district_scope = (
        f"{title} {provider_district}"
        if source_group == "seoul_district_official"
        else f"{title} {region_hints} {provider_district}"
    )
    if context.district and context.district in district_scope:
        return "district", context.district
    city_scope = f"{title} {region_hints}"
    if "서울특별시" in city_scope or "서울시" in city_scope or "서울 " in f"{city_scope} ":
        return "seoul", "서울특별시"
    if source_group == "government_official":
        return "national", "전국"
    return "", ""


def _industry_match(row: dict[str, str], industry_name: str) -> tuple[bool, list[str]]:
    if not industry_name or industry_name in {"상권 종합", "상권 맥락", "미입력"}:
        return False, []
    haystack = " ".join(
        [
            _clean_text(row.get("title")),
            _clean_text(row.get("summary")),
            _clean_text(row.get("industry_hints")),
        ]
    )
    matched = sorted(term for term in _industry_terms(industry_name) if term in haystack)
    return bool(matched), matched[:4]


def _row_has_industry_specificity(row: dict[str, str]) -> bool:
    if _clean_text(row.get("industry_hints")):
        return True
    searchable = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    return any(term in searchable for term in STRONG_INDUSTRY_SPECIFIC_TERMS)


def _budget_relevance(row: dict[str, str], budget: int | None, signals: set[str]) -> tuple[str, list[str]]:
    if not budget:
        return "not_provided", []
    haystack = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    matched = sorted(term for term in BUDGET_TERMS if term in haystack)
    if matched:
        return "direct", matched[:4]
    if signals & {"development", "commercial", "risk"}:
        return "context", []
    return "none", []


def _decision_area(signals: set[str], budget_relevance: str) -> str:
    if budget_relevance == "direct" or "small_business_policy" in signals:
        return "cost_policy"
    if "risk" in signals:
        return "risk"
    if "development" in signals:
        return "development"
    if "transport" in signals:
        return "accessibility"
    if "commercial" in signals:
        return "competition_market"
    return "demand"


def _decision_role(row: dict[str, str]) -> str:
    haystack = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    negative = any(term in haystack for term in NEGATIVE_TERMS)
    positive = any(term in haystack for term in POSITIVE_TERMS)
    if negative and not positive:
        return "risk"
    if positive and not negative:
        return "opportunity"
    return "watch"


def _source_bounded_decision_use(row: dict[str, str], decision_area: str) -> str:
    searchable = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    matched_terms: list[str] = []
    for term in DECISION_USE_TERMS[decision_area]:
        if term not in searchable:
            continue
        if any(term in existing or existing in term for existing in matched_terms):
            continue
        matched_terms.append(term)
    if not matched_terms:
        return "기사에 명시된 변화 내용과 대상 범위를 확인"
    return f"기사에 명시된 {'·'.join(matched_terms[:3])}의 대상 범위와 시점을 확인"


def _is_persistent_location_context(row: dict[str, str], signals: set[str]) -> bool:
    searchable = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    if any(term in searchable for term in TRANSIENT_OR_SINGLE_BUSINESS_TERMS):
        return False
    return bool(
        signals & INDUSTRY_INDEPENDENT_SIGNALS
        and any(term in searchable for term in PERSISTENT_LOCATION_TERMS)
    )


def _has_concrete_location_change(
    row: dict[str, str],
    signals: set[str],
    *,
    title_only: bool = False,
) -> bool:
    searchable = _clean_text(row.get("title"))
    if not title_only:
        searchable = f"{searchable} {_clean_text(row.get('summary'))}"
    return bool(
        _is_persistent_location_context(row, signals)
        and any(term in searchable for term in CONCRETE_LOCATION_CHANGE_TERMS)
    )


def _has_disqualifying_context(
    row: dict[str, str],
    *,
    scope: str,
) -> bool:
    searchable = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    if any(term in searchable for term in POLITICAL_PROMISE_TERMS):
        return True
    title = _clean_text(row.get("title"))
    if any(pattern.search(title) for pattern in POLITICAL_TITLE_PATTERNS):
        return True
    if scope in {"exact_area", "nearby", "district"} and any(
        term in searchable for term in NON_SEOUL_REGION_TERMS
    ):
        return True
    return False


def _location_is_primary(
    row: dict[str, str],
    *,
    scope: str,
    matched_location: str,
) -> bool:
    """Reject location tokens that appear only in a body byline or incidental list."""
    if scope not in {"exact_area", "nearby", "district"}:
        return True
    location = re.sub(r"\s+", "", _clean_text(matched_location))
    if not location:
        return False
    title = re.sub(r"\s+", "", _clean_text(row.get("title")))
    return location in title


def _eligible(
    *,
    row: dict[str, str],
    scope: str,
    matched_location: str,
    signals: set[str],
    industry_required: bool,
    industry_match: bool,
    budget: int | None,
    budget_relevance: str,
    business_policy: bool,
) -> bool:
    if not scope or not (signals - {"general"}):
        return False
    if _has_disqualifying_context(row, scope=scope):
        return False

    official = row.get("source_grade") == "A" and row.get("source_group") != "news_search"
    if scope in {"exact_area", "nearby"}:
        location_primary = _location_is_primary(
            row,
            scope=scope,
            matched_location=matched_location,
        )
        return bool(
            location_primary
            and (industry_match or _has_concrete_location_change(row, signals))
        )
    if scope == "district":
        location_primary = _location_is_primary(
            row,
            scope=scope,
            matched_location=matched_location,
        )
        return bool(
            location_primary
            and industry_match
            and business_policy
        )
    if scope == "seoul":
        if industry_required and not industry_match:
            return False
        if budget:
            return bool(
                budget_relevance == "direct"
                and (
                    industry_match and bool(signals & {"commercial", "tourism_event"})
                    or official and business_policy
                )
            )
        return official and industry_match and business_policy
    if scope == "national":
        return bool(
            official
            and budget
            and (industry_match or not industry_required)
            and budget_relevance == "direct"
            and "small_business_policy" in signals
            and business_policy
        )
    return False


def _score_row(
    row: dict[str, str],
    *,
    context: LocationContext,
    industry_name: str,
    budget: int | None,
    max_age_days: int,
) -> tuple[int, dict[str, Any] | None]:
    age = _age_days(row.get("published_date", ""))
    if age > max_age_days or age == 9999:
        return 0, None

    scope, matched_location = _location_match(row, context)
    title = _clean_text(row.get("title"))
    target_stations = {term for term in context.exact_terms if term.endswith("역")}
    title_stations = set(re.findall(r"[0-9A-Za-z가-힣]{2,}역", title))
    if target_stations and title_stations and title_stations.isdisjoint(target_stations):
        return 0, None
    industry_matched, matched_industry_terms = _industry_match(row, industry_name)
    signals = _signal_set(row)
    searchable = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    title = _clean_text(row.get("title"))
    if any(term in searchable for term in LOW_VALUE_EVIDENCE_TERMS):
        return 0, None
    if any(term in title for term in SENSITIVE_STORY_TERMS) and not any(
        term in title for term in CURRENT_OPERATION_TERMS
    ):
        return 0, None
    budget_relevance, matched_budget_terms = _budget_relevance(row, budget, signals)
    business_policy = any(term in searchable for term in BUSINESS_POLICY_TERMS)
    if not industry_matched and not business_policy:
        budget_relevance = "none"
        matched_budget_terms = []
    industry_required = bool(industry_name and industry_name not in {"상권 종합", "상권 맥락", "미입력"})
    if not _eligible(
        row=row,
        scope=scope,
        matched_location=matched_location,
        signals=signals,
        industry_required=industry_required,
        industry_match=industry_matched,
        budget=budget,
        budget_relevance=budget_relevance,
        business_policy=business_policy,
    ):
        return 0, None

    scope_score = {"exact_area": 45, "nearby": 38, "district": 28, "seoul": 12, "national": 8}[scope]
    score = scope_score + (20 if industry_matched else 0)
    score += 14 if budget_relevance == "direct" else 4 if budget_relevance == "context" else 0
    if row.get("source_grade") == "A":
        score += 8
    elif row.get("source_grade") == "B":
        score += 4
    score += 12 if age <= 14 else 8 if age <= 45 else 4 if age <= 90 else 1
    score += min(6, len(signals & LOCATION_SIGNAL_TYPES) * 2)

    decision_area = _decision_area(signals, budget_relevance)
    decision_role = _decision_role(row)
    location_reason = f"{SCOPE_LABELS[scope]}({matched_location})"
    reasons = [location_reason]
    if industry_matched:
        reasons.append(f"{industry_name} 연관({', '.join(matched_industry_terms)})")
    if budget_relevance == "direct":
        reasons.append(
            "비용·자금 계획 직접 연관"
            f"({', '.join(matched_budget_terms)}; "
            f"입력 {_budget_display(budget)}의 금액 적합성은 별도 확인)"
        )
    elif budget_relevance == "context":
        reasons.append(f"{_budget_display(budget)}의 비용·일정 간접 연관")

    decision_use = _source_bounded_decision_use(row, decision_area)
    decision_summary = (
        f"{SCOPE_LABELS[scope]} 자료로서 {' · '.join(reasons[1:]) if len(reasons) > 1 else '선택 위치의 변화'}에 연결됩니다. "
        f"{DECISION_LABELS[decision_area]}에서 {decision_use}하는 근거로 사용합니다."
    )
    enriched: dict[str, Any] = {
        **row,
        "title": _clean_text(row.get("title")),
        "summary": _clean_text(row.get("summary"))[:320],
        "location_scope": scope,
        "location_scope_label": SCOPE_LABELS[scope],
        "matched_location": matched_location,
        "industry_match": industry_matched,
        "matched_industry_terms": matched_industry_terms,
        "budget_relevance": budget_relevance,
        "matched_budget_terms": matched_budget_terms,
        "condition_fit": " · ".join(reasons),
        "selection_reason": " / ".join(reasons),
        "decision_area": decision_area,
        "decision_area_label": DECISION_LABELS[decision_area],
        "decision_role": decision_role,
        "decision_use": decision_use,
        "decision_summary": decision_summary,
        "age_days": age,
        "relevance_score": score,
        "score_role": "context_only",
        "structured_score_impact": "none",
        "evidence_tier": DECISION_SUPPORT_TIER,
        "evidence_tier_label": "판단 근거",
        "eligible_for_decision": True,
        "source_assertion_status": "supported",
        "usage_limit": (
            "정형 점수·등급에는 반영하지 않으며 기사에 직접 명시된 범위에서만 확인 근거로 사용; "
            "입력 예산 금액의 적합성을 증명하지 않음"
            if budget_relevance == "direct"
            else "정형 점수·등급에는 반영하지 않으며 기사에 직접 명시된 범위에서만 확인 근거로 사용"
        ),
    }
    return score, enriched


def _monitoring_location_basis(
    row: dict[str, str],
    *,
    context: LocationContext,
    scope: str,
    matched_location: str,
    industry_match: bool,
    business_policy: bool,
    industry_specific: bool,
) -> str:
    if scope in {"exact_area", "nearby", "district"}:
        if _location_is_primary(
            row,
            scope=scope,
            matched_location=matched_location,
        ):
            return "title_location"
        if (
            scope == "district"
            and row.get("source_group") == "seoul_district_official"
            and context.district
            and _clean_text(row.get("provider")).startswith(context.district)
        ):
            return "official_jurisdiction"
        return ""

    title = _clean_text(row.get("title"))
    official = row.get("source_grade") == "A" and row.get("source_group") != "news_search"
    if scope == "seoul":
        if business_policy and (industry_match or not industry_specific) and (
            "서울" in title or matched_location in title
        ):
            return "broad_industry" if industry_match else "broad_business_policy"
    if scope == "national" and official and business_policy and industry_match:
        return "broad_official_policy"
    return ""


def _monitoring_reference_use(row: dict[str, str], signals: set[str]) -> str:
    searchable = f"{_clean_text(row.get('title'))} {_clean_text(row.get('summary'))}"
    if any(term in searchable for term in TRANSIENT_OR_SINGLE_BUSINESS_TERMS):
        return "일시 운영·단일 점포 이슈의 실제 지속 여부를 현장 확인 전에 참고"
    if signals & {"development", "transport"}:
        return "지역 개발·교통 이슈의 대상 구간과 진행 상태를 추가 확인"
    if "small_business_policy" in signals:
        return "정책의 실제 대상 업종·신청 조건을 원문에서 추가 확인"
    return "최근 지역 이슈의 대상 범위와 지속성을 추가 확인"


def _monitoring_score_row(
    row: dict[str, str],
    *,
    context: LocationContext,
    industry_name: str,
    budget: int | None,
    max_age_days: int,
) -> tuple[int, dict[str, Any] | None]:
    age = _age_days(row.get("published_date", ""))
    if age > max_age_days or age == 9999:
        return 0, None

    scope, matched_location = _location_match(row, context)
    if not scope:
        return 0, None
    signals = _signal_set(row)
    if not (signals - {"general"}):
        return 0, None

    title = _clean_text(row.get("title"))
    summary = _clean_text(row.get("summary"))
    searchable = f"{title} {summary}"
    target_stations = {term for term in context.exact_terms if term.endswith("역")}
    title_stations = set(re.findall(r"[0-9A-Za-z가-힣]{2,}역", title))
    if target_stations and title_stations and title_stations.isdisjoint(target_stations):
        return 0, None
    if any(term in searchable for term in LOW_VALUE_EVIDENCE_TERMS):
        return 0, None
    if _has_disqualifying_context(row, scope=scope):
        return 0, None
    if any(term in title for term in SENSITIVE_STORY_TERMS) and not any(
        term in title for term in CURRENT_OPERATION_TERMS
    ):
        return 0, None

    industry_matched, matched_industry_terms = _industry_match(row, industry_name)
    budget_relevance, matched_budget_terms = _budget_relevance(row, budget, signals)
    business_policy = any(term in searchable for term in BUSINESS_POLICY_TERMS)
    industry_specific = _row_has_industry_specificity(row)
    concrete_location_change = _has_concrete_location_change(
        row,
        signals,
        title_only=scope == "district",
    )
    locally_relevant = bool(
        industry_matched
        or (business_policy and not industry_specific)
        or concrete_location_change
    )
    if scope in {"exact_area", "nearby", "district"} and not locally_relevant:
        return 0, None
    basis = _monitoring_location_basis(
        row,
        context=context,
        scope=scope,
        matched_location=matched_location,
        industry_match=industry_matched,
        business_policy=business_policy,
        industry_specific=industry_specific,
    )
    if not basis:
        return 0, None

    basis_reason = {
        "title_location": f"제목에서 {matched_location} 위치가 확인됨",
        "official_jurisdiction": (
            f"{context.district} 관할 공식 자료이지만 선택 상권과의 직접 공간 연결은 확인되지 않음"
        ),
        "broad_industry": "서울 범위와 선택 업종이 함께 확인됨",
        "broad_business_policy": "서울 범위의 업종 비특정 창업·소상공인 정책이 확인됨",
        "broad_official_policy": "공식 정책 자료의 적용 가능성을 확인할 필요가 있음",
    }[basis]
    missing_parts: list[str] = []
    if not industry_matched:
        missing_parts.append("선택 업종 직접 일치")
    if budget and budget_relevance != "direct":
        missing_parts.append("입력 예산 직접 연관")
    if not _is_persistent_location_context(row, signals):
        missing_parts.append("지속적 입지 변화")
    applicability_limit = (
        f"미확인 항목: {'·'.join(missing_parts) or '판단 직접성'}. "
        "따라서 점수·등급·추천 판단에는 사용하지 않음"
    )
    score = {
        "exact_area": 35,
        "nearby": 30,
        "district": 22,
        "seoul": 12,
        "national": 8,
    }[scope]
    score += 8 if row.get("source_grade") == "A" else 4
    score += 8 if industry_matched else 0
    score += 6 if age <= 14 else 4 if age <= 45 else 2 if age <= 90 else 1
    score += min(4, len(signals & LOCATION_SIGNAL_TYPES))
    reference_use = _monitoring_reference_use(row, signals)
    enriched: dict[str, Any] = {
        **row,
        "title": title,
        "summary": summary[:320],
        "location_scope": scope,
        "location_scope_label": SCOPE_LABELS[scope],
        "matched_location": matched_location,
        "industry_match": industry_matched,
        "matched_industry_terms": matched_industry_terms,
        "budget_relevance": budget_relevance,
        "matched_budget_terms": matched_budget_terms,
        "selection_reason": basis_reason,
        "monitoring_location_basis": basis,
        "reference_use": reference_use,
        "applicability_limit": applicability_limit,
        "monitoring_summary": f"{basis_reason}. {applicability_limit}.",
        "decision_use": "",
        "decision_summary": "",
        "age_days": age,
        "relevance_score": score,
        "score_role": "reference_only",
        "structured_score_impact": "none",
        "evidence_tier": REFERENCE_MONITORING_TIER,
        "evidence_tier_label": "참고·모니터링",
        "eligible_for_decision": False,
        "source_assertion_status": "not_enough_info",
        "usage_limit": applicability_limit,
    }
    return score, enriched


def retrieve_news_evidence(
    payload: dict[str, Any],
    *,
    limit: int = 3,
    max_age_days: int = 180,
    rows: Iterable[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Return strict condition-matched context evidence without touching structured scores."""

    area_name = str(payload.get("area_name") or "")
    industry_name = str(payload.get("industry_name") or "")
    context = _location_context(str(payload.get("area_code") or ""), area_name)
    budget = _budget_value(payload)

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows if rows is not None else _rows():
        score, enriched = _score_row(
            row,
            context=context,
            industry_name=industry_name,
            budget=budget,
            max_age_days=max_age_days,
        )
        if enriched and score > 0:
            scored.append((score, enriched))

    selected: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    broad_count = 0
    for score, row in sorted(
        scored,
        key=lambda item: (item[0], item[1].get("published_date", ""), item[1].get("evidence_id", "")),
        reverse=True,
    ):
        provider = row.get("provider") or row.get("source_id") or "unknown"
        if provider_counts.get(str(provider), 0) >= 1:
            continue
        broad = row.get("location_scope") in {"seoul", "national"}
        if broad and broad_count >= 1:
            continue
        decision_area = str(row.get("decision_area") or "")
        if decision_counts.get(decision_area, 0) >= 1 and len(selected) >= 2:
            continue
        row["signal_labels"] = " · ".join(
            SIGNAL_LABELS.get(item, item)
            for item in re.split(r"[;|]", str(row.get("signal_types") or ""))
            if item
        )
        selected.append(row)
        provider_counts[str(provider)] = provider_counts.get(str(provider), 0) + 1
        decision_counts[decision_area] = decision_counts.get(decision_area, 0) + 1
        broad_count += int(broad)
        if len(selected) >= max(1, min(limit, 4)):
            break

    for index, item in enumerate(selected, start=1):
        item["citation_index"] = index
        item["citation_marker"] = f"[NEWS:{index}]"
    return selected


def retrieve_news_evidence_tiers(
    payload: dict[str, Any],
    *,
    decision_limit: int = 3,
    monitoring_limit: int = 3,
    max_age_days: int = 180,
    rows: Iterable[dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return direct decision support and explicitly non-decision monitoring references."""

    source_rows = tuple(rows if rows is not None else _rows())
    decision_support = retrieve_news_evidence(
        payload,
        limit=decision_limit,
        max_age_days=max_age_days,
        rows=source_rows,
    )
    selected_identities = {_row_identity(item) for item in decision_support}
    context = _location_context(
        str(payload.get("area_code") or ""),
        str(payload.get("area_name") or ""),
    )
    industry_name = str(payload.get("industry_name") or "")
    budget = _budget_value(payload)

    scored_monitoring: list[tuple[int, dict[str, Any]]] = []
    for row in source_rows:
        if _row_identity(row) in selected_identities:
            continue
        score, enriched = _monitoring_score_row(
            row,
            context=context,
            industry_name=industry_name,
            budget=budget,
            max_age_days=max_age_days,
        )
        if enriched and score > 0:
            scored_monitoring.append((score, enriched))

    monitoring: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}
    broad_count = 0
    for score, row in sorted(
        scored_monitoring,
        key=lambda item: (
            item[0],
            item[1].get("published_date", ""),
            item[1].get("evidence_id", ""),
        ),
        reverse=True,
    ):
        provider = str(row.get("provider") or row.get("source_id") or "unknown")
        if provider_counts.get(provider, 0) >= 1:
            continue
        broad = row.get("location_scope") in {"seoul", "national"}
        if broad and broad_count >= 1:
            continue
        row["signal_labels"] = " · ".join(
            SIGNAL_LABELS.get(item, item)
            for item in re.split(r"[;|]", str(row.get("signal_types") or ""))
            if item
        )
        monitoring.append(row)
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        broad_count += int(broad)
        if len(monitoring) >= max(0, min(monitoring_limit, 4)):
            break

    for index, item in enumerate(monitoring, start=1):
        item["tier_rank"] = index
    return {
        DECISION_SUPPORT_TIER: decision_support,
        REFERENCE_MONITORING_TIER: monitoring,
    }


def news_evidence_version(items: list[dict[str, Any]]) -> str:
    if not items:
        return f"{RETRIEVAL_VERSION}:no-news"
    identity = RETRIEVAL_VERSION + "|" + "|".join(
        f"{item.get('evidence_tier', '')}:{item.get('evidence_id', '')}:{item.get('content_sha256', '')}"
        for item in items
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def news_evidence_for_prompt(items: list[dict[str, Any]]) -> str:
    decision_items = [
        item
        for item in items
        if item.get("evidence_tier", DECISION_SUPPORT_TIER) == DECISION_SUPPORT_TIER
    ]
    if not decision_items:
        return "사용자 조건을 모두 통과한 최근 외부 근거 없음"

    lines: list[str] = []
    for idx, item in enumerate(decision_items[:3], start=1):
        title = _clean_text(item.get("title", ""))[:180]
        summary = _clean_text(item.get("summary", ""))[:220]
        lines.append(
            f"[NEWS:{idx}] 발행 {item.get('published_date') or '날짜 미상'} | "
            f"{item.get('provider') or '출처 미상'} | 조건 적합: {item.get('condition_fit')}\n"
            f"판단 영역: {item.get('decision_area_label')} | 확인: {item.get('decision_use')}\n"
            f"제목: {title}\n요약: {summary}"
        )
    return "\n\n".join(lines)
