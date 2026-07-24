from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


os.environ["LOCALFIT_ADMIN_WORKER"] = "1"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.admin_pipeline import execute_stored_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one registered LocalFit admin pipeline job.")
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()
    execute_stored_job(args.job_id)


if __name__ == "__main__":
    main()
