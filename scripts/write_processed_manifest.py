from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "datacorpus" / "_processed"


def count_rows(path: Path) -> int:
    # 결과 CSV의 헤더를 제외한 행 수만 세어 manifest에 기록한다.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)


def main() -> None:
    outputs = []
    for path in sorted(OUT_DIR.glob("*.csv"), key=lambda p: p.name):
        outputs.append(
            {
                "path": str(path.relative_to(ROOT)),
                "rows": count_rows(path),
                "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
            }
        )
    manifest = {
        "created_for": "서울 상권 상세리포트 데이터 코퍼스 요약",
        "source_dir": str(ROOT / "datacorpus"),
        "outputs": outputs,
    }
    (OUT_DIR / "요약_생성결과.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
