from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from app.services.report_charts import render_report_charts
from app.services.report_publisher import _pdf_bytes_from_markdown


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-render one reviewed delivery PDF from a stored report JSON without DB or model access."
    )
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--reviewed-markdown", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pdf-name", default="report.pdf")
    args = parser.parse_args()

    source_bytes = args.source_json.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    report = source.get("report") or source
    if not isinstance(report, dict) or not report.get("indicator_pack"):
        raise ValueError("stored report JSON does not contain report.indicator_pack")

    markdown = args.reviewed_markdown.read_text(encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    chart_dir = args.output_dir / "charts"
    chart_paths = render_report_charts(report, chart_dir)
    missing = [chart_id for chart_id in ("C1", "C2", "C3", "C4", "C5") if chart_id not in chart_paths]
    if missing:
        raise RuntimeError(f"reviewed delivery is missing charts: {', '.join(missing)}")

    markdown_path = args.output_dir / "report.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    pdf_bytes = _pdf_bytes_from_markdown(markdown, args.output_dir)
    pdf_path = args.output_dir / args.pdf_name
    pdf_path.write_bytes(pdf_bytes)

    reader = PdfReader(pdf_path)
    page_count = len(reader.pages)
    page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
    annotation_count = sum(len(page.get("/Annots") or []) for page in reader.pages)
    combined_text = "\n".join(page_texts)
    normalized_text = re.sub(r"\s+", " ", combined_text)
    banned_patterns = {
        "internal_chart_code": r"(?<![A-Za-z0-9])C[1-5](?![A-Za-z0-9])",
        "numbered_evidence_badge": r"(?:근거\s*[12]|\[NEWS:\d+\])",
        "experience_placeholder": r"(?:experience_level|경험\s*미입력)",
        "internal_quarter_code": r"(?<!\d)20\d{2}[1-4](?!\d)",
        "known_particle_error": r"교대역\(법원\.검찰청\)는",
        "rejected_news_row": r"(?:책방오늘|mt\.co\.kr|nc\.press)",
        "withheld_vacancy_promoted": r"13\.8%",
    }
    banned_hits = {
        name: sorted(set(re.findall(pattern, combined_text)))
        for name, pattern in banned_patterns.items()
        if re.search(pattern, combined_text)
    }
    required_phrases = [
        "상권 내 치킨전문점 업종 합산 추정매출",
        "개별 점포의 매출이나 수익성을 뜻하지 않습니다",
        "직접 경쟁이 낮다고 단정할 수 없습니다",
        "세부 원천 지표는 현재 표시되지 않음",
    ]
    missing_required_phrases = [phrase for phrase in required_phrases if phrase not in normalized_text]
    blank_pages = [index for index, text in enumerate(page_texts, 1) if len(text) < 100]
    if banned_hits or missing_required_phrases or blank_pages or annotation_count:
        raise RuntimeError(
            "offline delivery PDF failed quality gate: "
            + json.dumps(
                {
                    "banned_hits": banned_hits,
                    "missing_required_phrases": missing_required_phrases,
                    "blank_pages": blank_pages,
                    "annotation_count": annotation_count,
                },
                ensure_ascii=False,
            )
        )
    manifest = {
        "delivery_version": "yangjae-chicken-offline-audited.v1.20260722",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offline_only": True,
        "db_access": False,
        "model_access": False,
        "source_json": str(args.source_json.resolve()),
        "source_json_sha256": _sha256_bytes(source_bytes),
        "reviewed_markdown": str(args.reviewed_markdown.resolve()),
        "reviewed_markdown_sha256": _sha256_bytes(markdown.encode("utf-8")),
        "pdf_path": str(pdf_path.resolve()),
        "pdf_sha256": _sha256_bytes(pdf_bytes),
        "pdf_pages": page_count,
        "pdf_annotation_count": annotation_count,
        "pdf_page_text_chars": [len(text) for text in page_texts],
        "quality_checks": {
            "banned_pattern_hits": banned_hits,
            "missing_required_phrases": missing_required_phrases,
            "blank_pages": blank_pages,
            "passed": True,
        },
        "chart_paths": chart_paths,
    }
    (args.output_dir / "delivery_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
