from __future__ import annotations

import csv
from pathlib import Path


MANIFEST = Path("datacorpus/_raw_ingest/ingest_manifest.csv")


def main() -> int:
    with MANIFEST.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames or []

    changed = 0
    for row in rows:
        run_id = row.get("run_id", "")
        if run_id == "20260703_110102_sgis_spatial_codes":
            row["collection_status"] = "superseded_failed_retry"
            row["quality_notes_ko"] = (
                "SGIS 공간코드 확장 수집 중 addr/stage 시도 목록 호출에서 cd=non 형식이 현재 신규 호스트와 맞지 않아 중단된 재시도 전 부분 산출물이다. "
                "후속 run 20260703_110138_sgis_spatial_codes에서 cd 생략 방식으로 재수집했으므로 최종 후보에서는 제외한다."
            )
            changed += 1
        elif run_id == "20260703_110332_juso_address_batch":
            row["collection_status"] = "superseded_low_quality_input"
            row["quality_notes_ko"] = (
                "서울 공공와이파이 위치정보의 첫 후보 주소를 단순 선택해 Juso 정규화 원응답을 저장한 배치다. "
                "일부 비서울 주소와 검색결과가 과도하게 넓은 주소가 섞여 품질 기준에 미달하므로, 검증형 run 20260703_110550_juso_validated_address로 대체한다."
            )
            changed += 1

    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print({"changed": changed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
