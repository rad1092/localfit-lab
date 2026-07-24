from __future__ import annotations

import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.settings import REPORTS_ROOT
from app.services.report_chart_catalog import CHART_TITLES
from app.services.report_charts import render_report_charts


REPORTS_OUT = REPORTS_ROOT
PUBLIC_PRESENTATION_VERSION = "public-copy-units-pagination.v16.20260723-preserve-score-grade-scope"
PUBLIC_PRESENTATION_MARKER = ".public-presentation-version"
PUBLIC_QUARTER_CODE_PATTERN = re.compile(r"(?<!\d)(20\d{2})([1-4])(?!\d)")
PUBLIC_URL_PATTERN = re.compile(r"https?://\S+")
DISPLAY_GRADE_PATTERN = re.compile(r"^([A-E])\s*(\+)?(?:\s*등급)?$")
DISPLAY_GRADE_SEARCH = re.compile(r"(?<![A-Z0-9])([A-E])\s*(\+)?(?:\s*등급)?(?![A-Z0-9])")
NUMERIC_SCORE_PATTERN = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d+)?\s*점(?!포)")
LABELED_NUMERIC_SCORE_PATTERN = re.compile(
    r"((?:[가-힣A-Za-z·]+\s*){0,4}(?:점수|등급))\s*(?:는|은|:|=)?\s*"
    r"\d{1,3}(?:\.\d+)?(?:\s*/\s*100)?(?:점)?(?:으로|이다|입니다)?"
)
HEADER_NUMERIC_SCORE_PATTERN = re.compile(
    r"((?:현재\s*)?(?:입지|종합|입지\s*종합|현재\s*위치)\s*(?:점수|등급))"
    r"\s*(?:는|은|:|=)?\s*\d{1,3}(?:\.\d+)?(?:\s*/\s*100)?(?:점)?(?:으로|이다|입니다)?"
)
PUBLIC_DISCLAIMER_PATTERNS = (
    re.compile(r"개별.{0,80}(?:생존|사업\s*성공|성공).{0,40}(?:확률|가능성).{0,40}(?:아니|불가|금지|해석|사용하지)"),
    re.compile(r"365일.{0,40}생존.{0,40}(?:확률|예측|해석|불가|사용하지)"),
    re.compile(r"입지\s*(?:점수|등급).{0,80}(?:생존|사업\s*성공|성공).{0,40}(?:아니|불가|금지|사용하지)"),
)
PUBLIC_PREDICTION_TERM = re.compile(r"(?:성공|생존|폐업)\s*(?:확률|가능성)")
PUBLIC_TEXT_REPLACEMENTS = {
    "창업 성공확률": "입지 등급",
    "창업 성공 확률": "입지 등급",
    "사업 성공확률": "입지 등급",
    "사업 성공 확률": "입지 등급",
    "성공확률": "입지 등급",
    "성공 확률": "입지 등급",
    "입지 점수": "입지 등급",
    "종합 점수": "종합 등급",
    "최근 분기 매출액": "최근 분기 상권×업종 합산 추정매출",
}
def _safe_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", value).strip()[:120] or "ai_report"


def _display_grade(*values: Any, fallback: str = "등급 보류") -> str:
    for value in values:
        match = DISPLAY_GRADE_PATTERN.fullmatch(str(value or "").strip().upper())
        if match:
            return f"{match.group(1)}{match.group(2) or ''}"
    return fallback


def _grade_in_text(value: str) -> str | None:
    match = DISPLAY_GRADE_SEARCH.search(str(value or "").upper())
    return f"{match.group(1)}{match.group(2) or ''}" if match else None


def _is_public_disclaimer(value: str) -> bool:
    compact = " ".join(str(value or "").split())
    return any(pattern.search(compact) for pattern in PUBLIC_DISCLAIMER_PATTERNS)


def _grade_label_text(label: str, grade: str) -> str:
    normalized = str(label).replace("점수", "등급").strip()
    if grade == "등급 보류":
        stem = normalized.removesuffix("등급").strip()
        return f"{stem} 등급 보류".strip()
    return f"{normalized} {grade}".strip()


def _replace_public_quarter_codes(value: str) -> str:
    """Format internal quarter codes without changing URL path/query values."""
    chunks: list[str] = []
    cursor = 0
    for url_match in PUBLIC_URL_PATTERN.finditer(value):
        chunks.append(
            PUBLIC_QUARTER_CODE_PATTERN.sub(
                lambda match: f"{match.group(1)}년 {match.group(2)}분기",
                value[cursor:url_match.start()],
            )
        )
        chunks.append(url_match.group(0))
        cursor = url_match.end()
    chunks.append(
        PUBLIC_QUARTER_CODE_PATTERN.sub(
            lambda match: f"{match.group(1)}년 {match.group(2)}분기",
            value[cursor:],
        )
    )
    return "".join(chunks)


def _sanitize_public_line(value: str, *, preferred_grade: str | None = None) -> str:
    line = str(value)
    line = re.sub(
        r"\s*(?:\[NEWS:\d+\]|\[근거\s*\d+\]|"
        r"(?<!\w)근거\s*\d+(?![\d,.]|\s*(?:천|만)?(?:억원|만원|개월|분기|원|억|개|명|건|분|년|월|일|%|㎡)))\s*",
        " ",
        line,
    )
    for chart_id, title in CHART_TITLES.items():
        line = re.sub(
            rf"(?<![A-Za-z0-9]){chart_id}(?=\s*(?:차트|시각화|그래프|에서|은|는|을|를))",
            title,
            line,
        )
    if _is_public_disclaimer(line) or PUBLIC_PREDICTION_TERM.search(line):
        return ""
    protected_scope_phrases = {
        "점수·등급·추천 판단": "__PUBLIC_SCORE_GRADE_RECOMMENDATION_SCOPE__",
        "점수·등급": "__PUBLIC_SCORE_GRADE_SCOPE__",
    }
    for phrase, placeholder in protected_scope_phrases.items():
        line = line.replace(phrase, placeholder)
    preferred = _display_grade(preferred_grade, fallback="")
    line = HEADER_NUMERIC_SCORE_PATTERN.sub(
        lambda match: _grade_label_text(match.group(1), preferred or "등급 보류"),
        line,
    )
    grade = _grade_in_text(line) or "등급 보류"
    for old, new in {
        "점수라는": "등급이라는",
        "점수가": "등급이",
        "점수는": "등급은",
        "점수를": "등급을",
        "점수와": "등급과",
        "점수로": "등급으로",
    }.items():
        line = line.replace(old, new)
    for old, new in PUBLIC_TEXT_REPLACEMENTS.items():
        line = line.replace(old, new)
    line = _replace_public_quarter_codes(line)
    line = LABELED_NUMERIC_SCORE_PATTERN.sub(
        lambda match: _grade_label_text(match.group(1), grade),
        line,
    )
    line = NUMERIC_SCORE_PATTERN.sub(grade, line)
    line = line.replace("점수", "등급")
    for phrase, placeholder in protected_scope_phrases.items():
        line = line.replace(placeholder, phrase)
    return re.sub(r"\s{2,}", " ", line).strip()


def _sanitize_public_text(value: str, *, preferred_grade: str | None = None) -> str:
    lines: list[str] = []
    for raw_line in str(value).splitlines() or [str(value)]:
        if not raw_line.strip():
            lines.append("")
            continue
        sentences = re.split(r"(?<=[.!?])\s+", raw_line)
        cleaned = [
            _sanitize_public_line(sentence, preferred_grade=preferred_grade).strip()
            for sentence in sentences
        ]
        lines.append(" ".join(sentence for sentence in cleaned if sentence))
    return "\n".join(lines).strip()


def _sanitize_public_markdown(value: str, *, preferred_grade: str | None = None) -> str:
    lines: list[str] = []
    for raw_line in str(value or "").splitlines():
        raw_line = re.sub(r"^(\s*)(\d+)\.\s+\d+[.)]\s+", r"\1\2. ", raw_line)
        line_grade = _grade_in_text(raw_line)
        cleaned = _sanitize_public_text(raw_line, preferred_grade=line_grade or preferred_grade)
        if cleaned or not raw_line.strip():
            lines.append(cleaned)
    return "\n".join(lines).strip() + "\n"


def _sanitize_public_value(
    value: Any,
    *,
    key: str = "",
    preferred_grade: str | None = None,
) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for child_key, child_value in value.items():
            name = str(child_key)
            if name == "markdown_body" and isinstance(child_value, str):
                cleaned[child_key] = _sanitize_public_markdown(
                    child_value,
                    preferred_grade=preferred_grade,
                )
            else:
                cleaned[child_key] = _sanitize_public_value(
                    child_value,
                    key=name,
                    preferred_grade=preferred_grade,
                )
        return cleaned
    if isinstance(value, list):
        cleaned_items = [
            _sanitize_public_value(item, key=key, preferred_grade=preferred_grade)
            for item in value
        ]
        return [item for item in cleaned_items if item not in (None, "", [], {})]
    if isinstance(value, tuple):
        return tuple(
            _sanitize_public_value(item, key=key, preferred_grade=preferred_grade)
            for item in value
        )
    if not isinstance(value, str):
        return value
    cleaned = _sanitize_public_text(value, preferred_grade=preferred_grade)
    if (key.endswith("점수") or key.endswith("등급")) and cleaned:
        return _display_grade(cleaned) if re.fullmatch(r"\s*\d{1,3}(?:\.\d+)?(?:\s*/\s*100)?\s*", cleaned) else cleaned
    return cleaned


def _normalize_grade_metric(metric: dict[str, Any]) -> None:
    grade = _display_grade(
        metric.get("display_grade"),
        metric.get("grade"),
        metric.get("display"),
        metric.get("score_display"),
    )
    metric["display_grade"] = grade
    metric["grade"] = grade
    metric["display"] = grade
    if isinstance(metric.get("score_display"), str):
        metric["score_display"] = grade


def _normalize_score_metrics(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _normalize_score_metrics(item)
        return
    if not isinstance(value, dict):
        return
    label = str(value.get("label") or "")
    is_score_metric = value.get("unit") == "score" or (
        ("등급" in label or "점수" in label)
        and any(key in value for key in ("display", "grade", "display_grade", "raw"))
    )
    if is_score_metric:
        value["label"] = label.replace("점수", "등급")
        _normalize_grade_metric(value)
    for item in value.values():
        _normalize_score_metrics(item)


def normalize_public_report_data(report_data: dict[str, Any]) -> dict[str, Any]:
    """Return a display-only copy while leaving stored/internal numeric scalars untouched."""
    raw_header = report_data.get("header_block") if isinstance(report_data, dict) else None
    preferred_grade = _display_grade(
        raw_header.get("display_grade") if isinstance(raw_header, dict) else None,
        raw_header.get("grade") if isinstance(raw_header, dict) else None,
        raw_header.get("score") if isinstance(raw_header, dict) and isinstance(raw_header.get("score"), str) else None,
        fallback="",
    )
    public = _sanitize_public_value(
        deepcopy(report_data),
        preferred_grade=preferred_grade,
    )
    if not isinstance(public, dict):
        return {}

    header = public.get("header_block")
    if isinstance(header, dict):
        header_grade = _display_grade(
            header.get("display_grade"),
            header.get("grade"),
            header.get("score") if isinstance(header.get("score"), str) else None,
        )
        header["display_grade"] = header_grade
        header["grade"] = header_grade
        if isinstance(header.get("score"), str):
            header["score"] = header_grade
        header["score_label"] = _sanitize_public_text(str(header.get("score_label") or "입지 등급"))
        for metric in header.get("key_metrics") or []:
            if isinstance(metric, dict) and (
                metric.get("unit") == "score" or "등급" in str(metric.get("label") or "")
            ):
                _normalize_grade_metric(metric)

    for axis in public.get("axis_interpretations") or []:
        if not isinstance(axis, dict):
            continue
        grade = _display_grade(
            axis.get("display_grade"), axis.get("grade"), axis.get("score_display"), axis.get("interpretation_level")
        )
        axis["display_grade"] = grade
        axis["grade"] = grade
        axis["score_display"] = grade
        axis["interpretation_level"] = f"{grade}등급" if grade != "등급 보류" else grade

    for alternative in public.get("alternatives") or []:
        if not isinstance(alternative, dict):
            continue
        grade = _display_grade(
            alternative.get("display_grade"),
            alternative.get("grade"),
            alternative.get("score") if isinstance(alternative.get("score"), str) else None,
        )
        alternative["display_grade"] = grade
        alternative["grade"] = grade
        if isinstance(alternative.get("score"), str):
            alternative["score"] = grade
        if isinstance(alternative.get("cost"), str) and NUMERIC_SCORE_PATTERN.search(alternative["cost"]):
            alternative["cost"] = _display_grade(alternative.get("cost_display_grade"))

    _normalize_score_metrics(public.get("indicator_pack") or {})
    markdown = public.get("markdown_body")
    if isinstance(markdown, str) and markdown.strip():
        public["markdown_body"] = _sanitize_public_markdown(
            markdown,
            preferred_grade=header.get("display_grade") if isinstance(header, dict) else preferred_grade,
        )
    return public


def report_artifacts_are_current(report_id: int | str) -> bool:
    report_dir = REPORTS_OUT / str(report_id)
    marker = report_dir / PUBLIC_PRESENTATION_MARKER
    markdown_path = report_dir / "report.md"
    pdf_path = report_dir / "report.pdf"
    try:
        if marker.read_text(encoding="utf-8").strip() != PUBLIC_PRESENTATION_VERSION:
            return False
        markdown = markdown_path.read_text(encoding="utf-8")
        referenced_chart_ids = set(re.findall(r"charts/(C[1-5])\.png", markdown))
        required = [markdown_path, pdf_path] + [
            report_dir / "charts" / f"{chart_id}.png" for chart_id in referenced_chart_ids
        ]
        return all(path.exists() for path in required)
    except OSError:
        return False


def _markdown_from_report(report_data: dict[str, Any]) -> str:
    public = normalize_public_report_data(report_data)
    markdown = public.get("markdown_body")
    if isinstance(markdown, str) and markdown.strip():
        return markdown.strip() + "\n"
    title = public.get("narrative_title") or "AI 상세 리포트"
    return f"# {title}\n\n{public.get('summary', '')}\n"


def _embed_chart_links(markdown: str, available_chart_ids: set[str] | None = None) -> str:
    text = markdown
    embedded: set[str] = set()
    available = set(CHART_TITLES) if available_chart_ids is None else set(available_chart_ids)
    for chart_id in ["C1", "C2", "C3", "C4", "C5"]:
        marker = f"[CHART:{chart_id}]"
        image = f"![{CHART_TITLES[chart_id]}](charts/{chart_id}.png)"
        if chart_id not in available:
            empty_note = f"{CHART_TITLES[chart_id]}: 표시 가능한 데이터 없음"
            text = text.replace(marker, empty_note)
            text = text.replace(image, empty_note)
            continue
        if marker in text and image not in text:
            text = text.replace(marker, image, 1)
            text = text.replace(marker, "")
            embedded.add(chart_id)
        elif f"charts/{chart_id}.png" in text:
            embedded.add(chart_id)
            text = text.replace(marker, "")
    missing = [
        chart_id
        for chart_id in ["C1", "C2", "C3", "C4", "C5"]
        if chart_id in available and chart_id not in embedded
    ]
    if missing:
        section = ["", "## 시각화 자료", ""]
        for chart_id in missing:
            section.extend([f"### {CHART_TITLES[chart_id]}", f"![{CHART_TITLES[chart_id]}](charts/{chart_id}.png)", ""])
        text = text.rstrip() + "\n" + "\n".join(section).rstrip() + "\n"
    return text


def _font_names() -> tuple[str, str]:
    body_path = next(
        (
            path
            for path in [
                Path("C:/Windows/Fonts/malgun.ttf"),
                Path("C:/Windows/Fonts/NanumGothic.ttf"),
                Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            ]
            if path.exists()
        ),
        None,
    )
    bold_path = next(
        (
            path
            for path in [
                Path("C:/Windows/Fonts/malgunbd.ttf"),
                Path("C:/Windows/Fonts/NanumGothicBold.ttf"),
                Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
            ]
            if path.exists()
        ),
        body_path,
    )
    if not body_path:
        return "Helvetica", "Helvetica-Bold"
    try:
        pdfmetrics.registerFont(TTFont("KoreanBody", str(body_path)))
    except Exception:
        pass
    try:
        pdfmetrics.registerFont(TTFont("KoreanBold", str(bold_path)))
    except Exception:
        pass
    return "KoreanBody", "KoreanBold"


def _pdf_bytes_from_markdown(markdown: str, base_dir: Path) -> bytes:
    font_name, bold_font_name = _font_names()
    styles = getSampleStyleSheet()
    ink = colors.HexColor("#0f172a")
    muted = colors.HexColor("#64748b")
    accent = colors.HexColor("#4338ca")
    body = ParagraphStyle(
        "KoreanBase",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.4,
        leading=14.2,
        textColor=ink,
        spaceAfter=5,
        wordWrap="CJK",
    )
    bullet = ParagraphStyle("KoreanBullet", parent=body, leftIndent=10, firstLineIndent=-7, bulletIndent=2, spaceAfter=4)
    methodology_bullet = ParagraphStyle(
        "KoreanMethodologyBullet",
        parent=bullet,
        fontSize=8.9,
        leading=12.4,
        spaceAfter=2.5,
    )
    checklist_chain = ParagraphStyle(
        "KoreanChecklistChain",
        parent=bullet,
        keepWithNext=True,
    )
    numbered = ParagraphStyle("KoreanNumbered", parent=body, leftIndent=14, firstLineIndent=-11, spaceAfter=5)
    metadata = ParagraphStyle("KoreanMeta", parent=body, fontSize=8.2, leading=12, textColor=muted, spaceAfter=2)
    table_body = ParagraphStyle("KoreanTable", parent=body, fontSize=7.8, leading=10.8, wordWrap="CJK")
    table_head = ParagraphStyle("KoreanTableHead", parent=table_body, fontName=bold_font_name, textColor=colors.white)
    heading = ParagraphStyle(
        "KoreanHeading",
        parent=body,
        fontName=bold_font_name,
        fontSize=15.5,
        leading=20,
        textColor=ink,
        spaceBefore=13,
        spaceAfter=8,
        keepWithNext=True,
    )
    subheading = ParagraphStyle(
        "KoreanSubheading",
        parent=body,
        fontName=bold_font_name,
        fontSize=11,
        leading=15,
        textColor=accent,
        spaceBefore=8,
        spaceAfter=5,
        keepWithNext=True,
    )
    section_lead = ParagraphStyle("KoreanSectionLead", parent=body, keepWithNext=True)
    title = ParagraphStyle(
        "KoreanTitle",
        parent=body,
        fontName=bold_font_name,
        fontSize=22,
        leading=29,
        textColor=ink,
        spaceAfter=10,
    )
    caption = ParagraphStyle("KoreanCaption", parent=body, fontSize=7.6, leading=10, textColor=muted, alignment=1, spaceAfter=6)

    buffer = BytesIO()
    document_title = next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), "입지봇 AI 상세 리포트")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=17 * mm,
        title=document_title,
        author="입지봇",
    )
    story = []
    table_rows: list[list[Paragraph]] = []
    compact_methodology = False

    def ptext(value: Any) -> str:
        return escape(str(value or "-").strip())

    def table_widths(rows: list[list[Paragraph]]) -> list[float] | None:
        if not rows:
            return None
        headers = [getattr(cell, "text", "") for cell in rows[0]]
        col_count = len(headers)
        if col_count == 4 and any("판단 영역" in header for header in headers):
            return [24 * mm, 18 * mm, 58 * mm, 78 * mm]
        if col_count == 4 and any("원천 기관" in header for header in headers):
            return [38 * mm, 50 * mm, 28 * mm, 62 * mm]
        if col_count == 4 and any("판단 제외 사유" in header for header in headers):
            return [46 * mm, 39 * mm, 42 * mm, 51 * mm]
        if col_count == 5 and any("한 줄 판단" in header for header in headers):
            return [30 * mm, 16 * mm, 22 * mm, 34 * mm, 76 * mm]
        if col_count == 5:
            return [32 * mm, 24 * mm, 24 * mm, 44 * mm, 54 * mm]
        if col_count == 4:
            return [44 * mm, 34 * mm, 26 * mm, 74 * mm]
        if col_count == 3:
            return [58 * mm, 48 * mm, 72 * mm]
        return None

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        table = Table(table_rows, colWidths=table_widths(table_rows), repeatRows=1 if len(table_rows) > 1 else 0)
        cell_padding = 3 if compact_methodology else 5
        commands = [
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), ink),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
            ("FONTNAME", (0, 1), (-1, -1), font_name),
            ("LEFTPADDING", (0, 0), (-1, -1), cell_padding),
            ("RIGHTPADDING", (0, 0), (-1, -1), cell_padding),
            ("TOPPADDING", (0, 0), (-1, -1), cell_padding),
            ("BOTTOMPADDING", (0, 0), (-1, -1), cell_padding),
        ]
        for row_index in range(2, len(table_rows), 2):
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#f8fafc")))
        table.setStyle(TableStyle(commands))
        table.hAlign = "LEFT"
        story.append(table)
        story.append(Spacer(1, 5))
        table_rows = []

    markdown_lines = markdown.splitlines()

    def next_nonblank_line(start_index: int) -> str:
        for candidate in markdown_lines[start_index + 1 :]:
            if candidate.strip():
                return candidate.strip()
        return ""

    last_was_heading = False
    for line_index, raw in enumerate(markdown_lines):
        line = raw.strip()
        if not line:
            flush_table()
            if not last_was_heading:
                story.append(Spacer(1, 4))
            continue
        image_match = re.fullmatch(r"!\[([^\]]*)\]\((.+?)\)", line)
        if image_match:
            flush_table()
            image_path = (base_dir / image_match.group(2)).resolve()
            if image_path.exists():
                chart = Image(str(image_path))
                max_chart_height = 80 * mm if image_path.stem == "C5" else 92 * mm
                scale = min(
                    (174 * mm) / chart.imageWidth,
                    max_chart_height / chart.imageHeight,
                )
                chart.drawWidth = chart.imageWidth * scale
                chart.drawHeight = chart.imageHeight * scale
                chart.hAlign = "CENTER"
                chart_block = [chart]
                if image_match.group(1):
                    chart_block.append(Paragraph(ptext(image_match.group(1)), caption))
                if last_was_heading and story and isinstance(story[-1], Paragraph):
                    chart_block.insert(0, story.pop())
                story.append(KeepTogether(chart_block))
            last_was_heading = False
            continue
        if re.fullmatch(r"\[CHART:C[1-5]\]", line):
            continue
        if line == "[PAGEBREAK]":
            flush_table()
            story.append(PageBreak())
            last_was_heading = False
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            style = table_head if not table_rows else table_body
            table_rows.append([Paragraph(ptext(cell), style) for cell in cells])
            last_was_heading = False
            continue
        flush_table()
        if line.startswith("# "):
            story.append(Paragraph(ptext(line[2:]), title))
            last_was_heading = False
        elif line.startswith("## "):
            compact_methodology = line[3:].strip() == "데이터 출처 및 산정 기준"
            story.append(Paragraph(ptext(line[3:]), heading))
            last_was_heading = True
        elif line.startswith("### "):
            story.append(Paragraph(ptext(line[4:]), subheading))
            last_was_heading = True
        elif line.startswith("- "):
            following_line = next_nonblank_line(line_index)
            if line.startswith("- [ ]") and following_line.startswith("- [ ]"):
                target_style = checklist_chain
            elif compact_methodology:
                target_style = methodology_bullet
            else:
                target_style = metadata if len(story) < 8 else bullet
            prefix = "" if target_style is metadata else "• "
            story.append(Paragraph(f"{prefix}{ptext(line[2:])}", target_style))
            last_was_heading = False
        elif re.match(r"^\d+\.\s+", line):
            story.append(Paragraph(ptext(line), numbered))
            last_was_heading = False
        else:
            story.append(Paragraph(ptext(line), section_lead if last_was_heading else body))
            last_was_heading = False

    flush_table()

    def draw_page(canvas, document) -> None:
        canvas.saveState()
        page_width, _ = A4
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.setLineWidth(0.4)
        canvas.line(document.leftMargin, 12 * mm, page_width - document.rightMargin, 12 * mm)
        canvas.setFillColor(muted)
        canvas.setFont(font_name, 7.5)
        canvas.drawString(document.leftMargin, 8 * mm, "입지봇 | 상권·업종 입지 리서치")
        canvas.drawRightString(page_width - document.rightMargin, 8 * mm, f"{canvas.getPageNumber()}")
        canvas.restoreState()

    class FooterCanvas(pdf_canvas.Canvas):
        def showPage(self) -> None:
            draw_page(self, doc)
            super().showPage()

    doc.build(story, canvasmaker=FooterCanvas)
    return buffer.getvalue()


def publish_report_artifacts(report_id: int | str, report_data: dict[str, Any]) -> dict[str, Any]:
    public = normalize_public_report_data(report_data)
    title = _safe_filename(str(public.get("narrative_title") or f"report_{report_id}"))
    report_dir = REPORTS_OUT / str(report_id)
    chart_dir = report_dir / "charts"
    report_dir.mkdir(parents=True, exist_ok=True)
    marker_path = report_dir / PUBLIC_PRESENTATION_MARKER
    marker_path.unlink(missing_ok=True)

    chart_paths = render_report_charts(public, chart_dir)
    markdown = _embed_chart_links(_markdown_from_report(public), set(chart_paths))
    md_path = report_dir / "report.md"
    pdf_path = report_dir / "report.pdf"
    md_path.write_text(markdown, encoding="utf-8")
    pdf_path.write_bytes(_pdf_bytes_from_markdown(markdown, report_dir))
    marker_path.write_text(PUBLIC_PRESENTATION_VERSION, encoding="utf-8")
    return {
        "title": title,
        "report_dir": str(report_dir),
        "markdown_path": str(md_path),
        "pdf_path": str(pdf_path),
        "chart_paths": chart_paths,
    }
