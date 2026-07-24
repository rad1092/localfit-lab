from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pypdf import PdfReader


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_RUN_ROOT = (
    PROJECT_ROOT / "runtime" / "evaluations" / "two-tier-news-random15-20260723"
)
DEFAULT_REPORTS_ROOT = (
    PROJECT_ROOT / "runtime" / "reports" / "two-tier-news-random15-20260723"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tmp" / "pdfs" / "random15"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _numbered_page_paths(root: Path) -> list[Path]:
    return sorted(
        root.glob("page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )


def _contact_sheet(
    paths: list[Path],
    output: Path,
    *,
    columns: int,
    thumb_width: int,
) -> None:
    thumbs: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as source:
            image = source.convert("RGB")
            height = round(image.height * thumb_width / image.width)
            image = image.resize(
                (thumb_width, height),
                Image.Resampling.LANCZOS,
            )
        canvas = Image.new("RGB", (thumb_width, height + 34), "white")
        canvas.paste(image, (0, 34))
        ImageDraw.Draw(canvas).text((10, 9), path.stem, fill="black")
        thumbs.append(canvas)
    rows = (len(thumbs) + columns - 1) // columns
    cell_width = max(image.width for image in thumbs)
    cell_height = max(image.height for image in thumbs)
    contact = Image.new(
        "RGB",
        (columns * cell_width, rows * cell_height),
        (225, 225, 225),
    )
    for index, image in enumerate(thumbs):
        contact.paste(
            image,
            ((index % columns) * cell_width, (index // columns) * cell_height),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    contact.save(output, quality=92)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render every report PDF page and C3/C5 chart for independent review."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_RUN_ROOT / "cases.json",
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=DEFAULT_REPORTS_ROOT,
    )
    parser.add_argument("--label", default="generated")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dpi", type=int, default=110)
    parser.add_argument("--pdftoppm", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain a non-empty cases array")
    reports_root = args.reports_root.resolve()
    output_root = args.output_root.resolve()
    pdftoppm = args.pdftoppm.resolve()
    if not pdftoppm.exists():
        raise FileNotFoundError(pdftoppm)

    rows: list[dict[str, Any]] = []
    chart_contact_paths: list[Path] = []
    chart_group_paths: list[Path] = []
    for index, case in enumerate(cases, 1):
        case_id = str(case["id"])
        artifact_id = f"{case_id}_r1"
        artifact_dir = reports_root / args.label / artifact_id
        pdf_path = artifact_dir / "report.pdf"
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        case_root = output_root / case_id
        case_root.mkdir(parents=True, exist_ok=True)
        for stale in case_root.glob("page-*.png"):
            stale.unlink()
        prefix = case_root / "page"
        completed = subprocess.run(
            [
                str(pdftoppm),
                "-png",
                "-r",
                str(args.dpi),
                str(pdf_path),
                str(prefix),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"pdftoppm failed for {case_id}: {completed.stderr.strip()}"
            )
        pages = _numbered_page_paths(case_root)
        pdf_page_count = len(PdfReader(str(pdf_path)).pages)
        if len(pages) != pdf_page_count:
            raise RuntimeError(
                f"{case_id}: rendered {len(pages)} pages, expected {pdf_page_count}"
            )
        contact_path = case_root / "contact.png"
        _contact_sheet(
            pages,
            contact_path,
            columns=3,
            thumb_width=480,
        )
        chart_paths = [
            artifact_dir / "charts" / "C3.png",
            artifact_dir / "charts" / "C5.png",
        ]
        if not all(path.exists() for path in chart_paths):
            raise FileNotFoundError(f"{case_id}: missing C3 or C5")
        chart_contact_paths.extend(chart_paths)
        rows.append(
            {
                "case": case,
                "artifact_id": artifact_id,
                "report_pdf": str(pdf_path),
                "report_pdf_sha256": _sha256(pdf_path),
                "page_count": pdf_page_count,
                "rendered_pages": [
                    {
                        "path": str(path),
                        "sha256": _sha256(path),
                    }
                    for path in pages
                ],
                "contact_sheet": str(contact_path),
                "contact_sheet_sha256": _sha256(contact_path),
                "charts": {
                    path.stem: {
                        "path": str(path),
                        "sha256": _sha256(path),
                    }
                    for path in chart_paths
                },
            }
        )
        print(
            f"[{index}/{len(cases)}] rendered {case_id}: {pdf_page_count} pages",
            flush=True,
        )

    for start in range(0, len(chart_contact_paths), 10):
        group_index = start // 10 + 1
        group_path = output_root / "chart_contacts" / f"C3_C5_group_{group_index:02d}.png"
        _contact_sheet(
            chart_contact_paths[start : start + 10],
            group_path,
            columns=2,
            thumb_width=700,
        )
        chart_group_paths.append(group_path)

    result = {
        "protocol_version": "localfit.pdf-batch-render-review.v1.0",
        "manifest": str(args.manifest.resolve()),
        "reports_root": str(reports_root),
        "dpi": args.dpi,
        "case_count": len(rows),
        "total_page_count": sum(row["page_count"] for row in rows),
        "chart_contact_sheets": [
            {
                "path": str(path),
                "sha256": _sha256(path),
            }
            for path in chart_group_paths
        ],
        "rows": rows,
    }
    _write_json(output_root / "render_manifest.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
