from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any

from app.core.settings import KNOWLEDGE_ROOT


KB_ROOT = KNOWLEDGE_ROOT

THEME_KEYWORDS: dict[str, list[str]] = {
    "mcda_wlc": ["mcda", "wlc", "weighted", "criteria", "가중", "정규화", "다기준", "입지"],
    "access_flow": ["huff", "distance", "attractiveness", "2sfca", "access", "접근", "거리", "유입", "보행"],
    "data_quality": ["dqv", "quality", "provenance", "coverage", "timeliness", "completeness", "품질", "최신", "커버리지", "결측", "계보"],
    "cost_proxy": ["rtms", "r-one", "rent", "lease", "임대", "월세", "권리금", "부동산", "프록시", "실거래"],
    "source_data": ["서울", "상권", "생활인구", "생활이동", "매출", "점포", "공공데이터", "원천", "gold", "silver"],
    "claim_guard": ["성공", "보장", "후보", "검증", "금지", "공식", "promotion", "candidate"],
}

THEME_LABELS = {
    "mcda_wlc": "다기준 점수화 근거",
    "access_flow": "접근성과 유입 해석 근거",
    "data_quality": "데이터 품질 판단 근거",
    "cost_proxy": "비용 프록시 해석 근거",
    "source_data": "원천데이터 연결 근거",
    "claim_guard": "표현 범위 관리 근거",
}


@dataclass(frozen=True)
class EvidenceChunk:
    source_path: str
    title: str
    theme: str
    excerpt: str
    score: int


def _clean_text(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError:
            data = json.loads(path.read_text(encoding="cp949", errors="ignore"))
        return json.dumps(data, ensure_ascii=False, indent=2)[:20000]

    if path.suffix.lower() == ".csv":
        rows: list[str] = []
        for encoding in ("utf-8-sig", "utf-8", "cp949"):
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    reader = csv.reader(handle)
                    for idx, row in enumerate(reader):
                        rows.append(" | ".join(str(cell) for cell in row[:12]))
                        if idx >= 80:
                            break
                return "\n".join(rows)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="ignore")

    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _chunk_text(text: str, max_chars: int = 850) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?。다])\s+|\n\s*\n", cleaned)
    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if current and len(current) + len(part) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return chunks[:24]


def _theme_for_text(text: str) -> str:
    lowered = text.lower()
    best_theme = "source_data"
    best_count = -1
    for theme, keywords in THEME_KEYWORDS.items():
        count = sum(1 for keyword in keywords if keyword.lower() in lowered)
        if count > best_count:
            best_theme = theme
            best_count = count
    return best_theme


# 각주(해석 프레임)로 인용할 수 있는 것은 외부 문헌뿐이다. 내부 감사/설계 문서(docs/)와
# 내부 데이터 산출물(datacorpus/)을 각주로 달면 자기 인용이 되므로 인덱스에서 제외한다.
EXTERNAL_TITLES = {
    "arcgis_huff_model_documentation": "ArcGIS Huff Model Documentation (확률적 상권 모형)",
    "arcgis_weighted_overlay_documentation": "ArcGIS Weighted Overlay Documentation (가중 중첩 분석)",
    "data_go_kr_kab_small_shop_rent": "한국부동산원 소규모 상가 임대 통계 (공공데이터포털)",
    "data_go_kr_molit_commercial_real_estate_trade_api": "국토교통부 상업용 부동산 실거래 API (공공데이터포털)",
    "dublin_core_dcmi_terms": "Dublin Core DCMI Metadata Terms",
    "uk_government_data_quality_framework": "UK Government Data Quality Framework",
    "uk_mcda_intro_guide_2024": "UK MCDA Introductory Guide 2024 (다기준 의사결정)",
    "w3c_data_quality_vocabulary": "W3C Data Quality Vocabulary (DQV)",
    "w3c_prov_o": "W3C PROV-O (데이터 계보 표준)",
}


@lru_cache(maxsize=1)
def _indexed_chunks() -> tuple[tuple[str, str, str, str], ...]:
    research_root = KB_ROOT / "research"
    if not research_root.exists():
        return tuple()

    records: list[tuple[str, str, str, str]] = []
    for path in sorted(research_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".html", ".txt"}:
            continue
        if path.stem not in EXTERNAL_TITLES:
            continue
        rel = str(path.relative_to(KB_ROOT)).replace("\\", "/")
        title = EXTERNAL_TITLES[path.stem]
        for chunk in _chunk_text(_read_text(path)):
            theme = _theme_for_text(f"{rel} {title} {chunk}")
            records.append((rel, title, theme, chunk))
    return tuple(records)


def _payload_terms(payload: dict[str, Any]) -> list[str]:
    terms = [
        str(payload.get("area_name") or ""),
        str(payload.get("industry_name") or ""),
        "MCDA",
        "WLC",
        "Huff",
        "DQV",
        "PROV",
        "데이터 품질",
        "비용 프록시",
        "상권",
        "점수화",
        "근거",
    ]
    axes = payload.get("axes") or {}
    if axes.get("axis_accessibility") is not None:
        terms.extend(["접근", "유입", "거리"])
    if (payload.get("extra_signals") or {}).get("cost_risk_score") is not None:
        terms.extend(["임대", "실거래", "R-ONE", "RTMS"])
    return [term.lower() for term in terms if term]


def retrieve_evidence_pack(payload: dict[str, Any], limit: int = 9) -> list[dict[str, str]]:
    """Return a small grounded pack from copied final_proj knowledge sources."""

    terms = _payload_terms(payload)
    scored: list[EvidenceChunk] = []
    for rel, title, theme, chunk in _indexed_chunks():
        haystack = f"{rel} {title} {chunk}".lower()
        lexical = sum(3 for term in terms if term and term in haystack)
        theme_bonus = 3 if theme in {"mcda_wlc", "data_quality", "cost_proxy", "access_flow"} else 1
        score = lexical + theme_bonus
        if score <= 0:
            continue
        scored.append(
            EvidenceChunk(
                source_path=rel,
                title=title,
                theme=theme,
                excerpt=chunk[:520].strip(),
                score=score,
            )
        )

    selected: list[EvidenceChunk] = []
    theme_counts: dict[str, int] = {}
    source_seen: set[str] = set()
    for item in sorted(scored, key=lambda x: x.score, reverse=True):
        # 같은 파일은 한 번만 각주로 인용한다 (테마가 달라도 중복 인용 금지).
        source_key = item.source_path
        if source_key in source_seen:
            continue
        if theme_counts.get(item.theme, 0) >= 2:
            continue
        selected.append(item)
        source_seen.add(source_key)
        theme_counts[item.theme] = theme_counts.get(item.theme, 0) + 1
        if len(selected) >= limit:
            break

    return [
        {
            "source_path": item.source_path,
            "title": item.title,
            "theme": item.theme,
            "theme_label": THEME_LABELS.get(item.theme, item.theme),
            "excerpt": item.excerpt,
        }
        for item in selected
    ]


def evidence_pack_for_prompt(evidence: list[dict[str, str]]) -> str:
    if not evidence:
        return "No copied knowledge-base evidence was retrieved."
    lines = []
    for idx, item in enumerate(evidence, start=1):
        lines.append(
            f"[{idx}] {item.get('title')} | {item.get('source_path')} | {item.get('theme_label')}\n"
            f"{item.get('excerpt')}"
        )
    return "\n\n".join(lines)
