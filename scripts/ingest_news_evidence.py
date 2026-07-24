from __future__ import annotations

import argparse
import csv
import hashlib
import http.cookiejar
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Iterable

from ingest_common import (
    DATACORPUS,
    DEFAULT_USER_AGENT,
    KEY_FILE,
    MANIFEST_FIELDS,
    RAW_ROOT,
    ROOT,
    append_csv,
    log_failure,
    now_utc,
    parse_key_file,
    redact_url,
    run_id,
    sha256_bytes,
    write_raw,
)


SILVER_PATH = DATACORPUS / "_silver" / "silver_news_evidence.csv"
RUN_LOG_ROOT = RAW_ROOT / "run_logs"
SEOUL_CITY_RSS = "https://seoulboard.seoul.go.kr/rss/RSSGenerator?bbsNo=158"
SEOUL_DISTRICT_DIRECTORY = "https://www.seoul.go.kr/news/rssboard/siteList.do"
POLICY_BRIEFING_URL = "https://www.korea.kr/briefing/pressReleaseList.do"
SEMAS_PRESS_URL = "https://www.semas.or.kr/web/board/webBoardList.kmdc?bCd=241&pNm=BOA010501"

DISTRICT_NAMES = [
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
]

DISTRICT_REQUEST_NAMES = {name: name.removesuffix("구") for name in DISTRICT_NAMES}
DISTRICT_REQUEST_NAMES["중구"] = "중구"

DEFAULT_NAVER_QUERIES = [
    "서울 상권 개발 교통",
    "서울 소상공인 정책",
    "서울 재개발 정비사업 상권",
    *[f"{district} 상권 개발 교통 임대" for district in DISTRICT_NAMES],
    "서울 카페 음료 상권 창업 임대",
    "서울 외식 음식점 상권 창업 임대",
    "서울 제과 베이커리 상권 창업 임대",
    "서울 소매 편의점 상권 창업 임대",
    "서울 숙박 관광 상권 창업 임대",
    "서울 패션 의류 상권 창업 임대",
]

NEWS_FIELDS = [
    "evidence_id",
    "source_id",
    "source_group",
    "source_grade",
    "provider",
    "dataset_name",
    "title",
    "summary",
    "original_url",
    "published_date",
    "collected_at",
    "region_hints",
    "industry_hints",
    "signal_types",
    "query_text",
    "content_sha256",
    "raw_path",
    "score_role",
    "usage_note",
]

SIGNAL_KEYWORDS = {
    "development": ["개발", "도시계획", "정비사업", "재개발", "재건축", "착공", "준공", "공급공고"],
    "transport": ["교통", "지하철", "GTX", "버스", "도로", "보행", "역세권", "환승"],
    "commercial": ["상권", "점포", "매출", "골목", "전통시장", "상가", "임대", "공실"],
    "small_business_policy": ["소상공인", "자영업", "창업", "중소벤처", "지원사업", "정책자금"],
    "tourism_event": ["관광", "축제", "행사", "문화", "공연", "박람회"],
    "risk": ["폐업", "침체", "통제", "화재", "재난", "공사", "위험", "규제", "폭염", "침수"],
}

INDUSTRY_KEYWORDS = {
    "카페·음료": ["카페", "커피", "음료"],
    "외식": ["음식점", "식당", "외식", "한식", "중식", "일식", "양식"],
    "제과·제빵": ["제과", "제빵", "베이커리", "빵집"],
    "소매": ["소매", "편의점", "유통", "마트"],
    "숙박·관광": ["숙박", "호텔", "관광"],
    "패션": ["의류", "패션"],
}

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "tr_code",
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
LOW_VALUE_TITLE_PATTERN = re.compile(
    r"^\s*(?:\[(?:광고|기고|칼럼|포토|인사|부고|오늘의\s*운세)\]|(?:광고|부고)\s*[:：])",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class FeedSpec:
    source_id: str
    source_group: str
    source_grade: str
    provider: str
    dataset_name: str
    url: str
    region_hint: str = ""


PUBLIC_FEEDS = [
    FeedSpec(
        source_id="seoul_city_press_rss",
        source_group="seoul_official",
        source_grade="A",
        provider="서울특별시",
        dataset_name="서울시 보도자료 RSS",
        url=SEOUL_CITY_RSS,
        region_hint="서울특별시",
    ),
    FeedSpec(
        source_id="molit_press_rss",
        source_group="government_official",
        source_grade="A",
        provider="국토교통부",
        dataset_name="국토교통부 보도자료 RSS",
        url="https://www.molit.go.kr/dev/board/board_rss.jsp?rss_id=NEWS",
    ),
    FeedSpec(
        source_id="mss_press_rss",
        source_group="government_official",
        source_grade="A",
        provider="중소벤처기업부",
        dataset_name="중소벤처기업부 보도자료 RSS",
        url="https://mss.go.kr/rss/smba/board/86.do",
    ),
]


def _clean_text(value: Any, *, limit: int = 700) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _normalize_url(value: str, *, base: str = "") -> str:
    raw = unescape(str(value or "").strip())
    if not raw:
        return ""
    absolute = urllib.parse.urljoin(base, raw)
    parts = urllib.parse.urlsplit(absolute)
    query = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urllib.parse.urlencode(query), "")
    )


def _repair_known_feed_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.netloc.lower().removeprefix("www.") == "sb.go.kr" and parts.path == "/RssXML3.do":
        return "https://www.sb.go.kr/rssBbsNtt.do?bbsNo=41&searchCtgry=&integrDeptCode="
    return url


def _parse_date(value: str) -> str:
    text = _clean_text(value, limit=80)
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = text.replace("년", "-").replace("월", "-").replace("일", "")
    normalized = normalized.replace(".", "-").replace("/", "-")
    match = re.search(r"(20\d{2})-?(\d{1,2})-?(\d{1,2})", normalized)
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


def _regions(text: str, forced: str = "") -> str:
    values = [district for district in DISTRICT_NAMES if district in text]
    if forced and forced not in values:
        values.insert(0, forced)
    if "서울" in text and "서울특별시" not in values:
        values.append("서울특별시")
    return ";".join(dict.fromkeys(values))


def _industries(text: str) -> str:
    matches = [label for label, keywords in INDUSTRY_KEYWORDS.items() if any(word in text for word in keywords)]
    return ";".join(matches)


def _signals(text: str) -> str:
    matches = [label for label, keywords in SIGNAL_KEYWORDS.items() if any(word in text for word in keywords)]
    return ";".join(matches or ["general"])


def _record(
    *,
    source_id: str,
    source_group: str,
    source_grade: str,
    provider: str,
    dataset_name: str,
    title: str,
    summary: str,
    original_url: str,
    published_date: str,
    collected_at: str,
    region_hint: str = "",
    query_text: str = "",
    raw_path: str = "",
) -> dict[str, str] | None:
    clean_title = _clean_text(title, limit=320)
    clean_summary = _clean_text(summary, limit=700)
    clean_url = _normalize_url(original_url)
    if not clean_title or not clean_url:
        return None
    searchable = f"{clean_title} {clean_summary} {provider} {dataset_name}"
    region_hints = _regions(searchable, region_hint)
    content_identity = f"{clean_url}|{clean_title}|{published_date}"
    evidence_id = hashlib.sha256(content_identity.encode("utf-8")).hexdigest()[:24]
    content_sha = hashlib.sha256(f"{clean_title}\n{clean_summary}".encode("utf-8")).hexdigest()
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "source_group": source_group,
        "source_grade": source_grade,
        "provider": provider,
        "dataset_name": dataset_name,
        "title": clean_title,
        "summary": clean_summary,
        "original_url": clean_url,
        "published_date": published_date,
        "collected_at": collected_at,
        "region_hints": region_hints,
        "industry_hints": _industries(searchable),
        "signal_types": _signals(searchable),
        "query_text": _clean_text(query_text, limit=160),
        "content_sha256": content_sha,
        "raw_path": raw_path,
        "score_role": "evidence_only",
        "usage_note": "정량 점수에는 반영하지 않고 최근 정책·지역 변화의 정성 근거로만 사용",
    }


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 25,
    attempts: int = 2,
) -> tuple[int, bytes, dict[str, str]]:
    request_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=request_headers, data=data)
            handlers: list[Any] = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
            if urllib.parse.urlsplit(url).hostname in {"semas.or.kr", "www.semas.or.kr"}:
                legacy_context = ssl.create_default_context()
                legacy_context.set_ciphers("DEFAULT:@SECLEVEL=1")
                handlers.append(urllib.request.HTTPSHandler(context=legacy_context))
            opener = urllib.request.build_opener(*handlers)
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.read(), dict(response.headers.items())
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_error or "request failed"))


def _decode(body: bytes, headers: dict[str, str] | None = None) -> str:
    content_type = (headers or {}).get("Content-Type", "")
    charset_match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "cp949", "euc-kr"])
    for encoding in dict.fromkeys(encodings):
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


def _tag_text(element: ET.Element, names: set[str]) -> str:
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in names:
            return "".join(child.itertext()).strip()
    return ""


def _parse_feed(body: bytes, spec: FeedSpec, *, collected_at: str, raw_path: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        text = body.decode("utf-8", errors="replace")
        text = re.sub(
            r"&(?!(?:amp|lt|gt|quot|apos);)([A-Za-z][A-Za-z0-9]+);",
            lambda match: unescape(match.group(0)),
            text,
        )
        root = ET.fromstring(text)

    items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    records: list[dict[str, str]] = []
    for item in items:
        title = _tag_text(item, {"title"})
        summary = _tag_text(item, {"description", "summary", "content", "encoded"})
        link = _tag_text(item, {"link", "guid", "id"})
        if not link:
            for child in list(item):
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        published = _tag_text(item, {"pubdate", "published", "updated", "date"})
        record = _record(
            source_id=spec.source_id,
            source_group=spec.source_group,
            source_grade=spec.source_grade,
            provider=spec.provider,
            dataset_name=spec.dataset_name,
            title=title,
            summary=summary,
            original_url=_normalize_url(link, base=spec.url),
            published_date=_parse_date(published),
            collected_at=collected_at,
            region_hint=spec.region_hint,
            raw_path=raw_path,
        )
        if record:
            records.append(record)
    return records


def _raw_relative_path(run_value: str, source_id: str, extension: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", source_id).strip("_")[:90]
    return f"{datetime.now():%Y%m%d}/news/{run_value}/{safe}.{extension}"


def _collect_feed(spec: FeedSpec, *, run_value: str, collected_at: str) -> tuple[list[dict[str, str]], str]:
    status, body, _ = _request(spec.url)
    raw_path = write_raw(
        run_id_value=run_value,
        source_id=spec.source_id,
        provider=spec.provider,
        dataset_name=spec.dataset_name,
        body=body,
        relative_path=_raw_relative_path(run_value, spec.source_id, "xml"),
        request_url_redacted=redact_url(spec.url),
        http_status=status,
        spatial_unit=spec.region_hint or "대한민국",
        time_unit="발행일",
        source_period="latest feed",
        area_code_type="자치구명/지역명 텍스트",
        quality_notes_ko="정량 점수 미반영 evidence-only. 기사·보도자료 전문 재배포 금지.",
    )
    relative = str(raw_path.relative_to(ROOT)).replace("\\", "/")
    return _parse_feed(body, spec, collected_at=collected_at, raw_path=relative), relative


def _discover_district_feeds(
    district: str,
    *,
    run_value: str,
    collected_at: str,
) -> list[FeedSpec]:
    request_name = DISTRICT_REQUEST_NAMES[district]
    payload = urllib.parse.urlencode({"schGu": request_name}).encode("utf-8")
    status, body, headers = _request(
        SEOUL_DISTRICT_DIRECTORY,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
    )
    source_id = f"seoul_district_rss_directory_{hashlib.sha1(district.encode('utf-8')).hexdigest()[:10]}"
    write_raw(
        run_id_value=run_value,
        source_id=source_id,
        provider="서울특별시",
        dataset_name=f"{district} RSS 주소 목록",
        body=body,
        relative_path=_raw_relative_path(run_value, source_id, "html"),
        request_url_redacted=SEOUL_DISTRICT_DIRECTORY,
        request_params={"schGu": request_name},
        http_status=status,
        spatial_unit=district,
        time_unit="수집시점",
        area_code_type="자치구명",
        quality_notes_ko="서울시 공식 자치구 RSS 디렉터리에서 발견한 피드만 사용.",
    )
    html = _decode(body, headers)
    matches = re.finditer(
        r"(?P<label>[^<>\r\n]{2,120})<br\s*/?>\s*(?P<url>https?://[^<\s]+)",
        html,
        flags=re.IGNORECASE,
    )
    feeds: list[FeedSpec] = []
    seen: set[str] = set()
    for match in matches:
        url = _repair_known_feed_url(_normalize_url(match.group("url")))
        if not url or url in seen:
            continue
        seen.add(url)
        label = _clean_text(match.group("label"), limit=120) or f"{district} 공식 RSS"
        if district == "성북구" and "rssBbsNtt.do" in url:
            label = "성북구청 새소식"
        suffix = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        feeds.append(
            FeedSpec(
                source_id=f"seoul_district_rss_{suffix}",
                source_group="seoul_district_official",
                source_grade="A",
                provider=f"{district}청",
                dataset_name=label,
                url=url,
                region_hint=district,
            )
        )
    return feeds


def _collect_districts(
    *,
    run_value: str,
    collected_at: str,
    district_limit: int | None,
    failures: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    records: list[dict[str, str]] = []
    discovered = 0
    fetched = 0
    districts = DISTRICT_NAMES[:district_limit] if district_limit else DISTRICT_NAMES
    for district in districts:
        try:
            feeds = _discover_district_feeds(district, run_value=run_value, collected_at=collected_at)
            discovered += len(feeds)
        except Exception as exc:
            _log_source_failure(
                run_value=run_value,
                source_id="seoul_district_rss_directory",
                provider="서울특별시",
                dataset_name=f"{district} RSS 주소 목록",
                url=SEOUL_DISTRICT_DIRECTORY,
                exc=exc,
                failures=failures,
            )
            continue
        for spec in feeds:
            try:
                feed_records, _ = _collect_feed(spec, run_value=run_value, collected_at=collected_at)
                records.extend(feed_records)
                fetched += 1
            except Exception as exc:
                _log_source_failure(
                    run_value=run_value,
                    source_id=spec.source_id,
                    provider=spec.provider,
                    dataset_name=spec.dataset_name,
                    url=spec.url,
                    exc=exc,
                    failures=failures,
                )
    return records, {"districts": len(districts), "feeds_discovered": discovered, "feeds_fetched": fetched}


def _parse_policy_briefing(
    html: str,
    *,
    collected_at: str,
    raw_path: str,
    source_id: str = "korea_policy_briefing",
    dataset_name: str = "대한민국 정책브리핑 보도자료",
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for anchor in re.finditer(
        r"<a[^>]+href=\"(?P<url>[^\"]*pressReleaseView\.do[^\"]*)\"[^>]*>(?P<body>[\s\S]*?)</a>",
        html,
        flags=re.IGNORECASE,
    ):
        body = anchor.group("body")
        title_match = re.search(r"<strong[^>]*>([\s\S]*?)</strong>", body, flags=re.IGNORECASE)
        lead_match = re.search(r"<span[^>]+class=\"lead\"[^>]*>([\s\S]*?)</span>", body, flags=re.IGNORECASE)
        source_match = re.search(
            r"<span[^>]+class=\"source\"[^>]*>\s*"
            r"<span[^>]*>(?P<date>[\s\S]*?)</span>\s*"
            r"<span[^>]*>(?P<provider>[\s\S]*?)</span>",
            body,
            flags=re.IGNORECASE,
        )
        provider = _clean_text(source_match.group("provider"), limit=100) if source_match else "대한민국 정책브리핑"
        published = _clean_text(source_match.group("date"), limit=40) if source_match else ""
        record = _record(
            source_id=source_id,
            source_group="government_official",
            source_grade="A",
            provider=provider,
            dataset_name=dataset_name,
            title=title_match.group(1) if title_match else "",
            summary=lead_match.group(1) if lead_match else "",
            original_url=_normalize_url(anchor.group("url"), base=POLICY_BRIEFING_URL),
            published_date=_parse_date(published),
            collected_at=collected_at,
            raw_path=raw_path,
        )
        if record:
            records.append(record)
    return records


def _collect_policy_briefing(
    *,
    run_value: str,
    collected_at: str,
    pages: int,
    failures: list[dict[str, str]],
    rep_code: str = "",
    source_suffix: str = "",
    dataset_name: str = "정부부처 보도자료 목록",
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for page in range(1, pages + 1):
        query = {"pageIndex": page}
        if rep_code:
            query["repCode"] = rep_code
        url = f"{POLICY_BRIEFING_URL}?{urllib.parse.urlencode(query)}"
        suffix = f"_{source_suffix}" if source_suffix else ""
        source_id = f"korea_policy_briefing{suffix}_page_{page}"
        try:
            status, body, headers = _request(url)
            raw_path = write_raw(
                run_id_value=run_value,
                source_id=source_id,
                provider="대한민국 정책브리핑",
                dataset_name=dataset_name,
                body=body,
                relative_path=_raw_relative_path(run_value, source_id, "html"),
                request_url_redacted=redact_url(url),
                request_params=query,
                http_status=status,
                spatial_unit="대한민국",
                time_unit="발행일",
                source_period="latest list pages",
                quality_notes_ko="정부부처 공식 보도자료 메타데이터. 정량 점수 미반영 evidence-only.",
            )
            relative = str(raw_path.relative_to(ROOT)).replace("\\", "/")
            records.extend(
                _parse_policy_briefing(
                    _decode(body, headers),
                    collected_at=collected_at,
                    raw_path=relative,
                    source_id=f"korea_policy_briefing{suffix}",
                    dataset_name=dataset_name,
                )
            )
        except Exception as exc:
            _log_source_failure(
                run_value=run_value,
                source_id=source_id,
                provider="대한민국 정책브리핑",
                dataset_name=dataset_name,
                url=url,
                exc=exc,
                failures=failures,
            )
    return records


def _parse_semas_press(html: str, *, collected_at: str, raw_path: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in re.finditer(r"<tr[^>]*>(?P<body>[\s\S]*?)</tr>", html, flags=re.IGNORECASE):
        body = row.group("body")
        anchor = re.search(
            r"<a[^>]+href=\"(?P<url>[^\"]*webBoardView\.kmdc[^\"]*)\"[^>]*>(?P<title>[\s\S]*?)</a>",
            body,
            flags=re.IGNORECASE,
        )
        date_match = re.search(r"<td[^>]*>\s*(?P<date>20\d{2}-\d{2}-\d{2})\s*</td>", body, flags=re.IGNORECASE)
        if not anchor or not date_match:
            continue
        href = re.sub(r";jsessionid=[^?]+", "", unescape(anchor.group("url")), flags=re.IGNORECASE)
        title = _clean_text(anchor.group("title"), limit=300)
        record = _record(
            source_id="semas_press_board",
            source_group="government_official",
            source_grade="A",
            provider="소상공인시장진흥공단",
            dataset_name="소상공인시장진흥공단 보도·설명",
            title=title,
            summary=title,
            original_url=_normalize_url(href, base=SEMAS_PRESS_URL),
            published_date=date_match.group("date"),
            collected_at=collected_at,
            raw_path=raw_path,
        )
        if record:
            records.append(record)
    return records


def _collect_semas(*, run_value: str, collected_at: str) -> list[dict[str, str]]:
    status, body, headers = _request(SEMAS_PRESS_URL)
    raw_path = write_raw(
        run_id_value=run_value,
        source_id="semas_press_board",
        provider="소상공인시장진흥공단",
        dataset_name="보도·설명 게시판",
        body=body,
        relative_path=_raw_relative_path(run_value, "semas_press_board", "html"),
        request_url_redacted=redact_url(SEMAS_PRESS_URL),
        request_params={"bCd": "241", "pNm": "BOA010501"},
        http_status=status,
        spatial_unit="대한민국",
        time_unit="등록일",
        source_period="latest list page",
        quality_notes_ko="공단 공식 보도·설명 메타데이터. 정량 점수 미반영 evidence-only.",
    )
    relative = str(raw_path.relative_to(ROOT)).replace("\\", "/")
    return _parse_semas_press(_decode(body, headers), collected_at=collected_at, raw_path=relative)


def _append_metadata_only_manifest(
    *,
    run_value: str,
    body: bytes,
    url: str,
    query: str,
    http_status: int,
) -> None:
    append_csv(
        RAW_ROOT / "ingest_manifest.csv",
        {
            "run_id": run_value,
            "source_id": "naver_api_hub_news",
            "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
            "provider": "NAVER API HUB",
            "dataset_name": "뉴스 검색 결과",
            "raw_path": "",
            "bytes": len(body),
            "sha256": sha256_bytes(body),
            "collection_status": "success_metadata_only",
            "request_url_redacted": redact_url(url),
            "request_params_json": json.dumps({"query": query}, ensure_ascii=False),
            "http_status": http_status,
            "provider_result_code": "",
            "provider_result_message": "",
            "spatial_unit": "검색어 기반 지역명",
            "time_unit": "기사 발행시각",
            "source_period": "latest search results",
            "boundary_version": "",
            "area_code_type": "지역명 텍스트",
            "quality_notes_ko": "공급자 이용조건을 고려해 원응답은 영구 저장하지 않고 해시와 정규화 메타데이터만 보존.",
            "collected_at": now_utc(),
        },
        MANIFEST_FIELDS,
    )


def _naver_queries(custom: list[str], areas: list[str], industry: str) -> list[str]:
    queries = list(custom or DEFAULT_NAVER_QUERIES)
    for area in areas:
        queries.extend([f"{area} 상권", f"{area} 개발 교통"])
        if industry:
            queries.append(f"{area} {industry}")
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


def _valid_secret(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and not text.startswith("<") and "입력" not in text and "발급" not in text)


def _collect_naver(
    *,
    run_value: str,
    collected_at: str,
    queries: list[str],
    display: int,
    failures: list[dict[str, str]],
) -> tuple[list[dict[str, str]], str]:
    keys = parse_key_file()
    endpoint = keys.get("naver_api_hub_endpoint") or "https://naverapihub.apigw.ntruss.com/search/v1/news"
    api_key_id = keys.get("naver_api_hub_client_id") or keys.get("naver_api_hub_api_key_id") or ""
    api_key = keys.get("naver_api_hub_client_secret") or keys.get("naver_api_hub_api_key") or ""
    if not (_valid_secret(api_key_id) and _valid_secret(api_key)):
        return [], "skipped_missing_key"

    records: list[dict[str, str]] = []
    headers = {
        "X-NCP-APIGW-API-KEY-ID": api_key_id,
        "X-NCP-APIGW-API-KEY": api_key,
    }
    for query in queries:
        url = f"{endpoint}?{urllib.parse.urlencode({'query': query, 'display': display, 'start': 1, 'sort': 'date', 'format': 'json'})}"
        try:
            status, body, _ = _request(url, headers=headers)
            _append_metadata_only_manifest(
                run_value=run_value,
                body=body,
                url=url,
                query=query,
                http_status=status,
            )
            payload = json.loads(body.decode("utf-8"))
            for item in payload.get("items") or []:
                original_url = item.get("originallink") or item.get("link") or ""
                domain = urllib.parse.urlsplit(original_url).netloc.lower().removeprefix("www.")
                record = _record(
                    source_id="naver_api_hub_news",
                    source_group="news_search",
                    source_grade="B",
                    provider=domain or "NAVER 뉴스 검색",
                    dataset_name="NAVER API HUB 뉴스 검색",
                    title=item.get("title") or "",
                    summary=item.get("description") or "",
                    original_url=original_url,
                    published_date=_parse_date(item.get("pubDate") or ""),
                    collected_at=collected_at,
                    query_text=query,
                )
                if record:
                    records.append(record)
        except Exception as exc:
            _log_source_failure(
                run_value=run_value,
                source_id="naver_api_hub_news",
                provider="NAVER API HUB",
                dataset_name="뉴스 검색 결과",
                url=url,
                exc=exc,
                failures=failures,
            )
    return records, "collected" if records else "collected_empty"


def _log_source_failure(
    *,
    run_value: str,
    source_id: str,
    provider: str,
    dataset_name: str,
    url: str,
    exc: Exception,
    failures: list[dict[str, str]],
) -> None:
    reason = _clean_text(str(exc), limit=300)
    failures.append({"source_id": source_id, "provider": provider, "reason": reason})
    log_failure(
        run_id_value=run_value,
        source_id=source_id,
        provider=provider,
        dataset_name=dataset_name,
        failure_type=type(exc).__name__,
        failure_reason_ko=reason,
        next_action_ko="다음 수집 주기에 재시도하고 공급자 URL 또는 응답 형식 변경 여부를 확인한다.",
        request_url_redacted=redact_url(url),
    )


def _published_within(record: dict[str, str], days: int) -> bool:
    value = record.get("published_date") or ""
    if not value:
        return True
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() >= date.today() - timedelta(days=days)
    except ValueError:
        return True


def _quality_filter(records: Iterable[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, int]]:
    accepted: list[dict[str, str]] = []
    rejected: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for record in records:
        if record.get("source_group") != "news_search":
            accepted.append(record)
            continue

        title = _clean_text(record.get("title"), limit=320)
        summary = _clean_text(record.get("summary"), limit=700)
        parsed_url = urllib.parse.urlsplit(record.get("original_url") or "")
        domain = parsed_url.netloc.lower().removeprefix("www.")
        if len(title) < 8:
            reject("short_title")
            continue
        if len(summary) < 20:
            reject("short_summary")
            continue
        if not record.get("published_date"):
            reject("missing_date")
            continue
        if parsed_url.scheme not in {"http", "https"} or not domain:
            reject("invalid_url")
            continue
        if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in BLOCKED_NEWS_DOMAINS):
            reject("non_news_platform")
            continue
        if LOW_VALUE_TITLE_PATTERN.search(title):
            reject("low_value_editorial")
            continue
        signals = {item for item in (record.get("signal_types") or "").split(";") if item and item != "general"}
        if not (record.get("region_hints") or record.get("industry_hints") or signals):
            reject("insufficient_relevance")
            continue
        accepted.append(record)
    return accepted, rejected


def _story_key(record: dict[str, str]) -> str:
    title = re.sub(r"^\s*\[[^\]]{1,30}\]\s*", "", record.get("title") or "")
    title = re.sub(r"[^0-9a-zA-Z가-힣]+", "", title).lower()
    if len(title) < 8:
        return record.get("evidence_id") or ""
    return f"{record.get('published_date', '')}|{title}"


def _prefer_record(current: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
    grade_rank = {"A": 3, "B": 2, "C": 1}
    current_key = (
        grade_rank.get(current.get("source_grade", ""), 0),
        len(current.get("summary") or ""),
        current.get("collected_at", ""),
    )
    candidate_key = (
        grade_rank.get(candidate.get("source_grade", ""), 0),
        len(candidate.get("summary") or ""),
        candidate.get("collected_at", ""),
    )
    return candidate if candidate_key >= current_key else current


def _read_existing() -> list[dict[str, str]]:
    if not SILVER_PATH.exists():
        return []
    with SILVER_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _merge_records(new_records: Iterable[dict[str, str]], *, retention_days: int) -> tuple[int, int]:
    existing = _read_existing()
    by_id: dict[str, dict[str, str]] = {}
    for record in [*existing, *new_records]:
        if not record.get("evidence_id") or not _published_within(record, retention_days):
            continue
        current = by_id.get(record["evidence_id"])
        if not current:
            by_id[record["evidence_id"]] = record
            continue
        by_id[record["evidence_id"]] = _prefer_record(current, record)

    by_story: dict[str, dict[str, str]] = {}
    for record in by_id.values():
        key = _story_key(record)
        current = by_story.get(key)
        by_story[key] = _prefer_record(current, record) if current else record

    rows = sorted(
        by_story.values(),
        key=lambda row: (row.get("published_date", ""), row.get("collected_at", ""), row.get("evidence_id", "")),
        reverse=True,
    )
    SILVER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = SILVER_PATH.with_suffix(".csv.tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=NEWS_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in NEWS_FIELDS})
    temp_path.replace(SILVER_PATH)
    return len(existing), len(rows)


def _source_enabled(selected: set[str], name: str) -> bool:
    return "all" in selected or name in selected


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    run_value = run_id("news")
    collected_at = now_utc()
    failures: list[dict[str, str]] = []
    records: list[dict[str, str]] = []
    status: dict[str, Any] = {}
    selected = set(args.source)

    if _source_enabled(selected, "seoul"):
        city_spec = PUBLIC_FEEDS[0]
        try:
            city_records, _ = _collect_feed(city_spec, run_value=run_value, collected_at=collected_at)
            records.extend(city_records)
            status["seoul_city"] = {"status": "collected", "records": len(city_records)}
        except Exception as exc:
            _log_source_failure(
                run_value=run_value,
                source_id=city_spec.source_id,
                provider=city_spec.provider,
                dataset_name=city_spec.dataset_name,
                url=city_spec.url,
                exc=exc,
                failures=failures,
            )
            status["seoul_city"] = {"status": "failed", "records": 0}
        district_records, district_status = _collect_districts(
            run_value=run_value,
            collected_at=collected_at,
            district_limit=args.district_limit,
            failures=failures,
        )
        records.extend(district_records)
        status["seoul_districts"] = {
            "status": "collected",
            "records": len(district_records),
            **district_status,
        }

    if _source_enabled(selected, "government"):
        government_records: list[dict[str, str]] = []
        government_status: dict[str, Any] = {}
        for spec in PUBLIC_FEEDS[1:]:
            try:
                feed_records, _ = _collect_feed(spec, run_value=run_value, collected_at=collected_at)
                government_records.extend(feed_records)
                government_status[spec.source_id] = {"status": "collected", "records": len(feed_records)}
            except Exception as exc:
                _log_source_failure(
                    run_value=run_value,
                    source_id=spec.source_id,
                    provider=spec.provider,
                    dataset_name=spec.dataset_name,
                    url=spec.url,
                    exc=exc,
                    failures=failures,
                )
                government_status[spec.source_id] = {"status": "failed", "records": 0}
        try:
            semas_records = _collect_semas(run_value=run_value, collected_at=collected_at)
            government_records.extend(semas_records)
            government_status["semas_press_board"] = {"status": "collected", "records": len(semas_records)}
        except Exception as exc:
            _log_source_failure(
                run_value=run_value,
                source_id="semas_press_board",
                provider="소상공인시장진흥공단",
                dataset_name="보도·설명 게시판",
                url=SEMAS_PRESS_URL,
                exc=exc,
                failures=failures,
            )
            government_status["semas_press_board"] = {"status": "failed", "records": 0}

        if government_status.get("molit_press_rss", {}).get("status") != "collected":
            molit_records = _collect_policy_briefing(
                run_value=run_value,
                collected_at=collected_at,
                pages=1,
                failures=failures,
                rep_code="A00006",
                source_suffix="molit",
                dataset_name="국토교통부 보도자료 (정책브리핑 공식 연계)",
            )
            government_records.extend(molit_records)
            government_status["molit_policy_briefing"] = {
                "status": "fallback_collected",
                "records": len(molit_records),
            }

        policy_records = _collect_policy_briefing(
                run_value=run_value,
                collected_at=collected_at,
                pages=args.government_pages,
                failures=failures,
            )
        government_records.extend(policy_records)
        government_status["korea_policy_briefing"] = {"status": "collected", "records": len(policy_records)}
        records.extend(government_records)
        status["government"] = {
            "status": "collected" if government_records else "collected_empty",
            "records": len(government_records),
            "sources": government_status,
        }

    if _source_enabled(selected, "naver"):
        queries = _naver_queries(args.naver_query, args.area, args.industry)
        naver_records, naver_status = _collect_naver(
            run_value=run_value,
            collected_at=collected_at,
            queries=queries,
            display=args.naver_display,
            failures=failures,
        )
        records.extend(naver_records)
        status["naver"] = {
            "status": naver_status,
            "records": len(naver_records),
            "query_count": len(queries),
            "credential_file": str(KEY_FILE),
        }

    quality_records, rejection_counts = _quality_filter(records)
    filtered = [record for record in quality_records if _published_within(record, args.lookback_days)]
    before_count, after_count = _merge_records(filtered, retention_days=args.retention_days)
    source_counts: dict[str, int] = {}
    for record in filtered:
        key = record.get("source_group") or "unknown"
        source_counts[key] = source_counts.get(key, 0) + 1

    summary = {
        "run_id": run_value,
        "started_at": collected_at,
        "finished_at": now_utc(),
        "selected_sources": sorted(selected),
        "status": status,
        "records_collected": len(records),
        "records_after_quality_filter": len(quality_records),
        "records_within_lookback": len(filtered),
        "quality_filter_rejections": rejection_counts,
        "source_group_counts": source_counts,
        "silver_rows_before": before_count,
        "silver_rows_after": after_count,
        "silver_path": str(SILVER_PATH.relative_to(ROOT)).replace("\\", "/"),
        "failure_count": len(failures),
        "failures": failures,
        "score_role": "evidence_only",
    }
    RUN_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    summary_path = RUN_LOG_ROOT / f"{run_value}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect source-linked news evidence for LocalFit reports.")
    parser.add_argument(
        "--source",
        action="append",
        choices=["all", "seoul", "government", "naver"],
        default=[],
        help="Source group to collect. Repeatable. Defaults to all.",
    )
    parser.add_argument("--area", action="append", default=[], help="Area name used to add NAVER queries.")
    parser.add_argument("--industry", default="", help="Industry name used with --area for NAVER queries.")
    parser.add_argument("--naver-query", action="append", default=[], help="Explicit NAVER news query. Repeatable.")
    parser.add_argument("--naver-display", type=int, default=50, choices=range(1, 101))
    parser.add_argument("--government-pages", type=int, default=2)
    parser.add_argument("--district-limit", type=int, default=None, help="Test-only limit for Seoul districts.")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--retention-days", type=int, default=730)
    args = parser.parse_args()
    if not args.source:
        args.source = ["all"]
    return args


def main() -> None:
    args = parse_args()
    try:
        run_once(args)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "message": _clean_text(exc)},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
