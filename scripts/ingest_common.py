from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATACORPUS = ROOT / "datacorpus"
RAW_ROOT = DATACORPUS / "_raw_ingest"

_key_file_value = os.getenv("LOCALFIT_KEY_FILE", "docs/90_private/key.md").strip()
_key_file_path = Path(_key_file_value).expanduser()
KEY_FILE = (_key_file_path if _key_file_path.is_absolute() else ROOT / _key_file_path).resolve()

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) final-map-project-data-ingest/20260703"
DATE_DIRECTORY_PATTERN = re.compile(r"^\d{8}$")

MANIFEST_FIELDS = [
    "run_id",
    "source_id",
    "snapshot_date",
    "provider",
    "dataset_name",
    "raw_path",
    "bytes",
    "sha256",
    "collection_status",
    "request_url_redacted",
    "request_params_json",
    "http_status",
    "provider_result_code",
    "provider_result_message",
    "spatial_unit",
    "time_unit",
    "source_period",
    "boundary_version",
    "area_code_type",
    "quality_notes_ko",
    "data_period_start",
    "data_period_end",
    "content_fingerprint",
    "change_status",
    "full_collection_status",
    "full_collection_completed_at",
    "collected_at",
]

FAILED_FIELDS = [
    "run_id",
    "source_id",
    "provider",
    "dataset_name",
    "attempted_at",
    "failure_type",
    "failure_reason_ko",
    "next_action_ko",
    "request_url_redacted",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_date() -> str:
    """Return the collection date, allowing deterministic admin/test overrides."""
    value = os.getenv("LOCALFIT_RUN_DATE", "").strip() or datetime.now().strftime("%Y%m%d")
    if not DATE_DIRECTORY_PATTERN.fullmatch(value):
        raise ValueError("LOCALFIT_RUN_DATE must use YYYYMMDD format.")
    return value


def latest_raw_path(*relative_parts: str, required_glob: str | None = None) -> Path:
    """Resolve the newest dated raw folder that contains the requested source.

    LOCALFIT_RAW_RUN_DATE can pin preprocessing to a specific collection date.
    LOCALFIT_RUN_DATE is also honored so one admin job can collect and preprocess
    a consistent snapshot without hard-coded calendar paths.
    """
    configured = (
        os.getenv("LOCALFIT_RAW_RUN_DATE", "").strip()
        or os.getenv("LOCALFIT_RUN_DATE", "").strip()
    )
    if configured:
        if not DATE_DIRECTORY_PATTERN.fullmatch(configured):
            raise ValueError("LOCALFIT_RAW_RUN_DATE must use YYYYMMDD format.")
        # A refresh may skip unchanged sources. Prefer this run date, then fall
        # back to the newest prior snapshot for each individual source.
        run_directories = sorted(
            (
                path
                for path in RAW_ROOT.iterdir()
                if path.is_dir()
                and DATE_DIRECTORY_PATTERN.fullmatch(path.name)
                and path.name <= configured
            ),
            key=lambda path: path.name,
            reverse=True,
        )
    else:
        run_directories = sorted(
            (
                path
                for path in RAW_ROOT.iterdir()
                if path.is_dir() and DATE_DIRECTORY_PATTERN.fullmatch(path.name)
            ),
            key=lambda path: path.name,
            reverse=True,
        )

    if len(relative_parts) >= 3 and relative_parts[:2] == ("seoul_open_data", "full"):
        complete_directory = latest_complete_service_directory(
            relative_parts[2],
            maximum_run_date=configured or None,
        )
        if complete_directory and (
            not required_glob or any(complete_directory.glob(required_glob))
        ):
            return complete_directory

    checked: list[str] = []
    for run_directory in run_directories:
        candidate = run_directory.joinpath(*relative_parts)
        checked.append(str(candidate))
        if not candidate.exists():
            continue
        if required_glob and not any(candidate.glob(required_glob)):
            continue
        return candidate

    suffix = f" containing {required_glob}" if required_glob else ""
    raise FileNotFoundError(
        "No collected raw source was found for "
        f"{'/'.join(relative_parts)}{suffix}. Checked: {checked or [str(RAW_ROOT)]}"
    )


def raw_run_date(path: Path) -> str:
    """Return the YYYYMMDD collection folder selected for a raw source path."""
    try:
        relative = path.resolve().relative_to(RAW_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Raw path is outside {RAW_ROOT}: {path}") from exc
    if not relative.parts or not DATE_DIRECTORY_PATTERN.fullmatch(relative.parts[0]):
        raise ValueError(f"Raw path has no YYYYMMDD collection folder: {path}")
    return relative.parts[0]


def raw_snapshot_date(path: Path) -> str:
    value = raw_run_date(path)
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def run_id(prefix: str = "run") -> str:
    return datetime.now().strftime(f"%Y%m%d_%H%M%S_{prefix}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _replace_file(temporary_name: str, path: Path, *, timeout_seconds: float = 5.0) -> None:
    """Replace an artifact, tolerating brief Windows reader locks from the dashboard."""
    deadline = time.monotonic() + timeout_seconds
    delay = 0.05
    while True:
        try:
            os.replace(temporary_name, path)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(0.5, delay * 2)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace a canonical artifact only after its complete payload is on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, value: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, value.encode(encoding))


def ensure_csv(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            existing_fields = list(reader.fieldnames or [])
            if existing_fields == fields:
                return
            rows = list(reader)
        # Manifest schemas evolve additively. Rewrite once so future appended rows
        # cannot become wider than the existing header.
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in fields})
                handle.flush()
                os.fsync(handle.fileno())
            _replace_file(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()


def append_csv(path: Path, row: dict[str, Any], fields: list[str]) -> None:
    ensure_csv(path, fields)
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writerow({field: row.get(field, "") for field in fields})


def _atomic_write_csv_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _manifest_service(row: dict[str, Any]) -> str:
    try:
        params = json.loads(str(row.get("request_params_json") or "{}"))
    except ValueError:
        return ""
    return str(params.get("service") or "") if isinstance(params, dict) else ""


def latest_complete_service_directory(
    service_name: str,
    *,
    maximum_run_date: str | None = None,
) -> Path | None:
    """Resolve only a manifest-certified complete Seoul full snapshot."""
    path = RAW_ROOT / "ingest_manifest.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
        candidates = sorted(
            (
                row
                for row in rows
                if row.get("full_collection_status") == "complete"
                and row.get("full_collection_completed_at")
                and _manifest_service(row) == service_name
            ),
            key=lambda row: str(row.get("full_collection_completed_at")),
            reverse=True,
        )
    for row in candidates:
        raw_path_value = str(row.get("raw_path") or "").strip()
        if not raw_path_value:
            continue
        raw_path = Path(raw_path_value)
        raw_path = raw_path if raw_path.is_absolute() else ROOT / raw_path
        directory = raw_path.resolve().parent
        try:
            run_date_value = directory.relative_to(RAW_ROOT.resolve()).parts[0]
        except (ValueError, IndexError):
            continue
        if maximum_run_date and run_date_value > maximum_run_date:
            continue
        completed_at = str(row.get("full_collection_completed_at") or "")
        invalidated_by_partial = False
        for other in rows:
            if (
                other.get("full_collection_status") == "complete"
                or _manifest_service(other) != service_name
                or str(other.get("collected_at") or "") <= completed_at
            ):
                continue
            other_raw_value = str(other.get("raw_path") or "").strip()
            if not other_raw_value:
                continue
            other_raw = Path(other_raw_value)
            other_raw = other_raw if other_raw.is_absolute() else ROOT / other_raw
            if other_raw.resolve().parent == directory:
                invalidated_by_partial = True
                break
        if invalidated_by_partial:
            continue
        if directory.exists():
            return directory
    if candidates:
        raise RuntimeError(
            f"No trusted complete snapshot remains for {service_name}; "
            "a later incomplete run modified every completed directory."
        )
    return None


def mark_manifest_run_complete(
    *,
    run_id_value: str,
    source_id: str,
    service_name: str,
    completed_at: str | None = None,
) -> str:
    """Mark every page only after a complete source run has succeeded."""
    path = RAW_ROOT / "ingest_manifest.csv"
    ensure_csv(path, MANIFEST_FIELDS)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    completed = completed_at or now_utc()
    matched = 0
    for row in rows:
        if (
            row.get("run_id") == run_id_value
            and row.get("source_id") == source_id
            and _manifest_service(row) == service_name
        ):
            row["full_collection_status"] = "complete"
            row["full_collection_completed_at"] = completed
            matched += 1
    if not matched:
        raise RuntimeError(
            f"완전 수집 완료로 표시할 매니페스트 행이 없습니다: {run_id_value}/{source_id}/{service_name}"
        )
    _atomic_write_csv_rows(path, rows, MANIFEST_FIELDS)
    return completed


def latest_complete_full_collection(source_id: str, service_name: str) -> dict[str, Any] | None:
    path = RAW_ROOT / "ingest_manifest.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        candidates = [
            dict(row)
            for row in rows
            if row.get("source_id") == source_id
            and row.get("full_collection_status") == "complete"
            and row.get("full_collection_completed_at")
            and _manifest_service(row) == service_name
        ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda row: str(row.get("full_collection_completed_at")))
    raw_path_value = str(latest.get("raw_path") or "").strip()
    if not raw_path_value:
        return None
    raw_path = Path(raw_path_value)
    raw_path = raw_path if raw_path.is_absolute() else ROOT / raw_path
    raw_directory = raw_path.resolve().parent
    if not raw_directory.exists():
        return None
    return {
        "run_id": latest.get("run_id"),
        "completed_at": latest.get("full_collection_completed_at"),
        "status": latest.get("full_collection_status"),
        "snapshot_date": latest.get("snapshot_date"),
        "raw_directory": str(raw_directory),
    }


def latest_failed_collection_at(source_id: str) -> str | None:
    path = RAW_ROOT / "failed_downloads.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        attempted = [
            str(row.get("attempted_at") or "")
            for row in csv.DictReader(handle)
            if row.get("source_id") == source_id and row.get("attempted_at")
        ]
    return max(attempted) if attempted else None


def latest_incomplete_collection_at(source_id: str, service_name: str) -> str | None:
    path = RAW_ROOT / "ingest_manifest.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        collected = [
            str(row.get("collected_at") or "")
            for row in csv.DictReader(handle)
            if row.get("source_id") == source_id
            and _manifest_service(row) == service_name
            and row.get("full_collection_status") != "complete"
            and row.get("collected_at")
        ]
    return max(collected) if collected else None


PROGRESS_PREFIX = "LOCALFIT_PROGRESS "
SOURCE_STATE_PATH = RAW_ROOT / "source_state_catalog.json"


def emit_progress(
    *,
    label: str,
    current_units: int,
    total_units: int,
    unit: str = "건",
    eta_seconds: float | None = None,
    data_period_start: str | None = None,
    data_period_end: str | None = None,
    message: str | None = None,
) -> None:
    """Emit one machine-readable progress event while keeping stdout human-readable."""
    payload = {
        "label": label,
        "current_units": max(0, int(current_units)),
        "total_units": max(0, int(total_units)),
        "unit": unit,
        "eta_seconds": round(max(0.0, eta_seconds), 1) if eta_seconds is not None else None,
        "data_period_start": data_period_start,
        "data_period_end": data_period_end,
        "message": message,
        "emitted_at": now_utc(),
    }
    print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def data_period_bounds(body: bytes, service_name: str | None = None) -> tuple[str | None, str | None]:
    """Extract the actual provider data period independently from collection time."""
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return None, None
    root: Any = payload.get(service_name, {}) if service_name and isinstance(payload, dict) else payload
    if isinstance(root, dict) and isinstance(root.get("row"), list):
        rows = root["row"]
    else:
        rows = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                candidate = value.get("row")
                if isinstance(candidate, list):
                    rows.extend(item for item in candidate if isinstance(item, dict))
                else:
                    for nested in value.values():
                        visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(root)

    period_keys = (
        "STDR_YYQU_CD",
        "STDR_YM_CD",
        "STDR_YM",
        "STDR_DE",
        "STDR_YY_CD",
        "STDR_YY",
        "BASE_YM",
        "BASE_YEAR",
    )
    periods: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in period_keys:
            value = str(row.get(key, "")).strip()
            if value and value.lower() not in {"nan", "none"}:
                periods.append(value)
                break
    return (min(periods), max(periods)) if periods else (None, None)


def source_state(service_name: str) -> dict[str, Any] | None:
    if not SOURCE_STATE_PATH.exists():
        return None
    try:
        payload = json.loads(SOURCE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = payload.get("services", {}).get(service_name) if isinstance(payload, dict) else None
    return dict(value) if isinstance(value, dict) else None


def update_source_state_catalog(entries: list[dict[str, Any]]) -> Path:
    payload: dict[str, Any] = {}
    if SOURCE_STATE_PATH.exists():
        try:
            payload = json.loads(SOURCE_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    for entry in entries:
        service_name = str(entry.get("service") or "").strip()
        if not service_name:
            continue
        existing_service = services.get(service_name)
        value = {
            **(dict(existing_service) if isinstance(existing_service, dict) else {}),
            **dict(entry),
        }
        value["updated_at"] = now_utc()
        services[service_name] = value
    output = {"updated_at": now_utc(), "services": services}
    atomic_write_text(SOURCE_STATE_PATH, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return SOURCE_STATE_PATH


def raw_history_paths(*relative_parts: str, required_glob: str | None = None) -> list[Path]:
    """Return every dated snapshot for one source, newest first."""
    configured = os.getenv("LOCALFIT_RAW_RUN_DATE", "").strip() or os.getenv("LOCALFIT_RUN_DATE", "").strip()
    maximum = configured if configured and DATE_DIRECTORY_PATTERN.fullmatch(configured) else None
    paths: list[Path] = []
    if not RAW_ROOT.exists():
        return paths
    for run_directory in sorted(RAW_ROOT.iterdir(), key=lambda path: path.name, reverse=True):
        if not run_directory.is_dir() or not DATE_DIRECTORY_PATTERN.fullmatch(run_directory.name):
            continue
        if maximum and run_directory.name > maximum:
            continue
        candidate = run_directory.joinpath(*relative_parts)
        if not candidate.exists():
            continue
        if required_glob and not any(candidate.glob(required_glob)):
            continue
        paths.append(candidate)
    return paths


def page_set_fingerprint(sample_bodies: dict[tuple[int, int], bytes]) -> str:
    return page_digest_set_fingerprint(
        {
            page_range: sha256_bytes(body)
            for page_range, body in sample_bodies.items()
        }
    )


def page_digest_set_fingerprint(page_digests: dict[tuple[int, int], str]) -> str:
    """Fingerprint ordered page digests without retaining the page bodies."""
    return hashlib.sha256(
        "\n".join(
            f"{start}:{end}:{digest}"
            for (start, end), digest in sorted(page_digests.items())
        ).encode("utf-8")
    ).hexdigest()


def validate_paged_collection_response(
    *,
    initial_total_count: int,
    page_total_count: int,
    start: int,
    end: int,
    row_count: int,
) -> None:
    """Fail the collection immediately if a provider snapshot moves mid-run."""
    if page_total_count != initial_total_count:
        raise RuntimeError(
            "list_total_count changed during collection: "
            f"initial={initial_total_count}, page={page_total_count}, range={start}-{end}"
        )
    if start < 1 or end < start:
        raise RuntimeError(f"invalid page range: {start}-{end}")
    expected_rows = max(0, min(end, initial_total_count) - start + 1)
    if row_count != expected_rows:
        raise RuntimeError(
            "page row count does not match its requested range: "
            f"range={start}-{end}, expected={expected_rows}, actual={row_count}"
        )


def raw_directory_full_fingerprint(raw_dir: Path, service_name: str) -> str:
    manifest_path = RAW_ROOT / "ingest_manifest.csv"
    manifest_hashes: dict[str, str] = {}
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                raw_path = str(row.get("raw_path") or "").replace("\\", "/")
                digest = str(row.get("sha256") or "")
                if raw_path and digest:
                    manifest_hashes[raw_path] = digest
    page_digests: dict[tuple[int, int], str] = {}
    pattern = re.compile(rf"^{re.escape(service_name)}_(\d+)_(\d+)\.json$")
    for path in raw_dir.glob(f"{service_name}_*.json"):
        match = pattern.match(path.name)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        try:
            relative = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
        except ValueError:
            relative = ""
        digest = manifest_hashes.get(relative)
        page_digests[(start, end)] = digest or sha256_bytes(path.read_bytes())
    # Keep the exact numeric page ordering used while collecting.  Sorting the
    # rendered "start:end:digest" strings lexicographically made page 10000
    # sort before page 2000 and falsely classified any 10+ page snapshot as
    # revised even when every page digest was unchanged.
    return page_digest_set_fingerprint(page_digests)


def sampled_skip_ttl_hours() -> float:
    value = os.getenv("LOCALFIT_SAMPLED_SKIP_TTL_HOURS", "24").strip()
    try:
        return max(0.0, float(value))
    except ValueError as exc:
        raise ValueError("LOCALFIT_SAMPLED_SKIP_TTL_HOURS must be a non-negative number.") from exc


def classify_seoul_probe(
    *,
    source_id: str,
    service_name: str,
    total_count: int,
    sample_bodies: dict[tuple[int, int], bytes],
    ttl_hours: float | None = None,
    include_full_fingerprint: bool = True,
) -> dict[str, Any]:
    """Compare a cheap first/middle/last-page probe with the newest local snapshot."""
    full_collection = latest_complete_full_collection(source_id, service_name)
    history = raw_history_paths(
        "seoul_open_data", "full", service_name, required_glob=f"{service_name}_*.json"
    )
    current_periods = [data_period_bounds(body, service_name) for body in sample_bodies.values()]
    current_start = min((item[0] for item in current_periods if item[0]), default=None)
    current_end = max((item[1] for item in current_periods if item[1]), default=None)
    current_fingerprint = page_set_fingerprint(sample_bodies)
    if not history:
        return {
            "status": "new_source",
            "previous_snapshot_date": None,
            "previous_total_count": None,
            "total_count": total_count,
            "data_period_start": current_start,
            "data_period_end": current_end,
            "content_fingerprint": current_fingerprint,
            "samples_match": False,
            "sampled_skip_allowed": False,
        }

    complete_directory = (
        Path(str(full_collection["raw_directory"]))
        if full_collection and full_collection.get("raw_directory")
        else None
    )
    previous = complete_directory if complete_directory and complete_directory.exists() else history[0]
    previous_is_complete = complete_directory is not None and previous.resolve() == complete_directory.resolve()
    previous_full_fingerprint = (
        raw_directory_full_fingerprint(previous, service_name)
        if include_full_fingerprint
        else None
    )
    previous_first = next(iter(sorted(previous.glob(f"{service_name}_1_*.json"))), None)
    previous_total: int | None = None
    previous_period_end: str | None = None
    if previous_first:
        try:
            previous_body = previous_first.read_bytes()
            decoded = json.loads(previous_body.decode("utf-8", errors="replace"))
            root = decoded.get(service_name, {}) if isinstance(decoded, dict) else {}
            previous_total = int(root.get("list_total_count", 0) or 0) if isinstance(root, dict) else None
            previous_period_end = data_period_bounds(previous_body, service_name)[1]
        except (OSError, TypeError, ValueError):
            previous_total = None

    samples_match = True
    compared_samples = 0
    for (start, end), body in sample_bodies.items():
        candidate = previous / f"{service_name}_{start}_{end}.json"
        if not candidate.exists():
            samples_match = False
            continue
        compared_samples += 1
        if sha256_bytes(body) != sha256_bytes(candidate.read_bytes()):
            samples_match = False

    effective_ttl_hours = sampled_skip_ttl_hours() if ttl_hours is None else max(0.0, ttl_hours)
    full_age_hours: float | None = None
    completed_at: datetime | None = None
    if previous_is_complete and full_collection and full_collection.get("completed_at"):
        try:
            completed_at = datetime.fromisoformat(str(full_collection["completed_at"]))
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            full_age_hours = max(0.0, (datetime.now(timezone.utc) - completed_at).total_seconds() / 3600)
        except ValueError:
            completed_at = None
            full_age_hours = None
    latest_failure_at = latest_failed_collection_at(source_id)
    latest_incomplete_at = latest_incomplete_collection_at(source_id, service_name)
    failure_after_complete = False
    incomplete_after_complete = False
    if completed_at is not None and latest_failure_at:
        try:
            failed_at = datetime.fromisoformat(latest_failure_at)
            if failed_at.tzinfo is None:
                failed_at = failed_at.replace(tzinfo=timezone.utc)
            failure_after_complete = failed_at > completed_at
        except ValueError:
            failure_after_complete = True
    if completed_at is not None and latest_incomplete_at:
        try:
            incomplete_at = datetime.fromisoformat(latest_incomplete_at)
            if incomplete_at.tzinfo is None:
                incomplete_at = incomplete_at.replace(tzinfo=timezone.utc)
            incomplete_after_complete = incomplete_at > completed_at
        except ValueError:
            incomplete_after_complete = True
    sampled_skip_allowed = (
        previous_is_complete
        and full_age_hours is not None
        and full_age_hours <= effective_ttl_hours
        and not failure_after_complete
        and not incomplete_after_complete
    )

    if previous_total == total_count and samples_match and compared_samples == len(sample_bodies):
        status = "unchanged_sampled" if sampled_skip_allowed else "sample_match_full_refresh_due"
    elif previous_total is not None and total_count < previous_total:
        status = "provider_window_shrink"
    elif previous_period_end and current_end and current_end > previous_period_end:
        status = "new_period"
    else:
        status = "revised"
    state = source_state(service_name)
    if status in {"unchanged_sampled", "sample_match_full_refresh_due"} and state and int(state.get("total_count") or 0) == total_count:
        current_start = (
            str(state.get("latest_window_period_start") or "")
            or str(state.get("data_period_start") or "")
            or current_start
        )
        current_end = (
            str(state.get("latest_window_period_end") or "")
            or str(state.get("data_period_end") or "")
            or current_end
        )
    return {
        "status": status,
        "previous_snapshot_date": raw_snapshot_date(previous),
        "previous_total_count": previous_total,
        "total_count": total_count,
        "data_period_start": current_start,
        "data_period_end": current_end,
        "content_fingerprint": current_fingerprint,
        "samples_match": samples_match,
        "sample_count": len(sample_bodies),
        "sampled_skip_allowed": sampled_skip_allowed,
        "sampled_skip_ttl_hours": effective_ttl_hours,
        "last_full_collection_at": full_collection.get("completed_at") if full_collection else None,
        "last_full_collection_age_hours": round(full_age_hours, 3) if full_age_hours is not None else None,
        "failure_after_full_collection": failure_after_complete,
        "incomplete_after_full_collection": incomplete_after_complete,
        "previous_full_fingerprint": previous_full_fingerprint,
    }


def update_collection_change_report(entries: list[dict[str, Any]]) -> Path:
    """Atomically merge per-source probe/collection decisions for one pipeline run."""
    date_value = run_date()
    path = RAW_ROOT / date_value / "collection_change_summary.json"
    pipeline_run_id = os.getenv("LOCALFIT_PIPELINE_RUN_ID", "").strip()
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    if existing.get("pipeline_run_id") != pipeline_run_id:
        existing = {}
    sources = {
        f"{item.get('source_id')}::{item.get('service', '')}": item
        for item in existing.get("sources", [])
        if item.get("source_id")
    }
    for entry in entries:
        value = dict(entry)
        value["pipeline_run_id"] = pipeline_run_id or None
        value["checked_at"] = now_utc()
        sources[f"{value['source_id']}::{value.get('service', '')}"] = value
    payload = {
        "pipeline_run_id": pipeline_run_id or None,
        "collection_date": f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:]}",
        "updated_at": now_utc(),
        "sources": sorted(sources.values(), key=lambda item: str(item.get("source_id"))),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def parse_key_file() -> dict[str, str]:
    text = KEY_FILE.read_text(encoding="utf-8")

    def match(pattern: str, name: str, required: bool = False) -> str:
        m = re.search(pattern, text, flags=re.S | re.I)
        if not m:
            if required:
                raise RuntimeError(f"key.md에서 {name} 항목을 찾지 못했습니다.")
            return ""
        return m.group(1).strip()

    return {
        "sbdc_endpoint": match(r"https://www\.data\.go\.kr/data/15012005/openapi\.do[\s\S]*?endpoint\s*:\s*(\S+)", "SBDC endpoint"),
        "sbdc_key": match(r"https://www\.data\.go\.kr/data/15012005/openapi\.do[\s\S]*?key\s*:\s*(\S+)", "SBDC key"),
        "rtms_endpoint": match(r"https://www\.data\.go\.kr/data/15126463/openapi\.do[\s\S]*?endpoint\s*:\s*(\S+)", "RTMS endpoint"),
        "rtms_key": match(r"https://www\.data\.go\.kr/data/15126463/openapi\.do[\s\S]*?key\s*:\s*(\S+)", "RTMS key"),
        "reb_key": match(r"https://www\.reb\.or\.kr/r-one/openapi/SttsApiTbl\.do[\s\S]*?key\s*:\s*(\S+)", "REB key"),
        "kosis_key": match(r"https://kosis\.kr/openapi/[\s\S]*?key\s*:\s*(\S+)", "KOSIS key"),
        "sgis_service_id": match(r"서비스\s*ID\s*:\s*(\S+)", "SGIS service ID"),
        "sgis_secret": match(r"https://sgis\.mods\.go\.kr[\s\S]*?\n\s*key\s*:\s*(\S+)", "SGIS secret"),
        "vworld_key": match(r"https://www\.vworld\.kr/dev/v4dv_geocoderguide2_s001\.do[\s\S]*?key\s*:\s*(\S+)", "VWorld key"),
        "juso_key": match(r"https://business\.juso\.go\.kr/jst/jstRoadNmAddrApiSearch[\s\S]*?key\s*:\s*(\S+)", "Juso key"),
        "seoul_key": match(r"https://data\.seoul\.go\.kr/together/guide/useGuide\.do[\s\S]*?key\s*:\s*(\S+)", "Seoul OpenAPI key"),
        "naver_api_hub_endpoint": match(
            r"(?m)^\s*naver_api_hub_endpoint\s*:\s*(\S+)\s*$",
            "NAVER API HUB endpoint",
        ),
        "naver_api_hub_api_key_id": match(
            r"(?m)^\s*naver_api_hub_api_key_id\s*:\s*(\S+)\s*$",
            "NAVER API HUB API Key ID",
        ),
        "naver_api_hub_api_key": match(
            r"(?m)^\s*naver_api_hub_api_key\s*:\s*(\S+)\s*$",
            "NAVER API HUB API Key",
        ),
        "naver_api_hub_client_id": match(
            r"(?m)^\s*naver_api_hub_client_id\s*:\s*(\S+)\s*$",
            "NAVER API HUB client ID",
        ),
        "naver_api_hub_client_secret": match(
            r"(?m)^\s*naver_api_hub_client_secret\s*:\s*(\S+)\s*$",
            "NAVER API HUB client secret",
        ),
    }


def redact_url(url: str, extra_values: list[str] | None = None) -> str:
    parsed = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_q = []
    secret_names = {"key", "apikey", "api_key", "servicekey", "consumer_key", "consumer_secret", "accesstoken", "accessToken", "confmkey"}
    for name, value in q:
        if name.lower() in {s.lower() for s in secret_names}:
            redacted_q.append((name, "<redacted>"))
        else:
            redacted_q.append((name, value))
    path = parsed.path
    if extra_values:
        for value in extra_values:
            if value:
                path = path.replace(value, "<redacted>")
    query = urllib.parse.urlencode(redacted_q, doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, bytes, dict[str, str]]:
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        return response.status, body, dict(response.headers.items())


def write_raw(
    *,
    run_id_value: str,
    source_id: str,
    provider: str,
    dataset_name: str,
    body: bytes,
    relative_path: str,
    request_url_redacted: str,
    request_params: dict[str, Any] | None = None,
    http_status: int | str = "",
    provider_result_code: str = "",
    provider_result_message: str = "",
    spatial_unit: str = "",
    time_unit: str = "",
    source_period: str = "",
    boundary_version: str = "",
    area_code_type: str = "",
    quality_notes_ko: str = "",
    data_period_start: str | None = None,
    data_period_end: str | None = None,
    content_fingerprint: str | None = None,
    change_status: str = "collected",
) -> Path:
    path = RAW_ROOT / relative_path
    atomic_write_bytes(path, body)
    digest = sha256_bytes(body)
    collection_date = run_date()
    append_csv(
        RAW_ROOT / "ingest_manifest.csv",
        {
            "run_id": run_id_value,
            "source_id": source_id,
            "snapshot_date": f"{collection_date[:4]}-{collection_date[4:6]}-{collection_date[6:]}",
            "provider": provider,
            "dataset_name": dataset_name,
            "raw_path": str(path.relative_to(ROOT)),
            "bytes": len(body),
            "sha256": digest,
            "collection_status": "success",
            "request_url_redacted": request_url_redacted,
            "request_params_json": json.dumps(request_params or {}, ensure_ascii=False, sort_keys=True),
            "http_status": http_status,
            "provider_result_code": provider_result_code,
            "provider_result_message": provider_result_message,
            "spatial_unit": spatial_unit,
            "time_unit": time_unit,
            "source_period": source_period,
            "boundary_version": boundary_version,
            "area_code_type": area_code_type,
            "quality_notes_ko": quality_notes_ko,
            "data_period_start": data_period_start or "",
            "data_period_end": data_period_end or "",
            "content_fingerprint": content_fingerprint or digest,
            "change_status": change_status,
            "collected_at": now_utc(),
        },
        MANIFEST_FIELDS,
    )
    return path


def log_failure(
    *,
    run_id_value: str,
    source_id: str,
    provider: str,
    dataset_name: str,
    failure_type: str,
    failure_reason_ko: str,
    next_action_ko: str,
    request_url_redacted: str,
) -> None:
    append_csv(
        RAW_ROOT / "failed_downloads.csv",
        {
            "run_id": run_id_value,
            "source_id": source_id,
            "provider": provider,
            "dataset_name": dataset_name,
            "attempted_at": now_utc(),
            "failure_type": failure_type,
            "failure_reason_ko": failure_reason_ko,
            "next_action_ko": next_action_ko,
            "request_url_redacted": request_url_redacted,
        },
        FAILED_FIELDS,
    )


def sanitize_sgis_auth_response(body: bytes) -> bytes:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return body
    result = data.get("result")
    if isinstance(result, dict) and "accessToken" in result:
        result["accessToken"] = "<redacted>"
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
