"""CSV bulk import: upload validation (sync) + row processing (Celery)."""

import csv
import io
import logging

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .models import BulkImportJob
from .serializers import LinkSerializer

logger = logging.getLogger(__name__)

ALLOWED_COLUMNS = {"original_url", "slug", "title", "expires_at", "password", "is_active"}
ALIASES = {"url": "original_url", "destination": "original_url", "long_url": "original_url"}
MAX_STORED_ERRORS = 100


class BulkImportError(Exception):
    """Raised for problems with the uploaded file itself (not individual rows)."""


def iter_rows(text: str):
    """Yield (line_number, row_dict) with normalised headers; skips blank lines."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [ALIASES.get(h.strip().lower(), h.strip().lower()) for h in next(reader)]
    except StopIteration:
        raise BulkImportError("The CSV file is empty.") from None
    if "original_url" not in header:
        raise BulkImportError("CSV must contain an 'original_url' column (alias: url).")
    for line_no, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        yield line_no, dict(zip(header, values, strict=False))


def validate_upload(uploaded_file) -> tuple[str, int]:
    if uploaded_file.size > settings.BULK_IMPORT_MAX_BYTES:
        raise BulkImportError(f"File too large (max {settings.BULK_IMPORT_MAX_BYTES // 1024} KB).")
    try:
        text = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BulkImportError("File must be UTF-8 encoded CSV.") from None
    total = 0
    passwords: set[str] = set()
    try:
        for _line_no, row in iter_rows(text):
            total += 1
            if (row.get("password") or "").strip():
                passwords.add(row["password"].strip())
    except csv.Error as exc:
        raise BulkImportError(f"Invalid CSV: {exc}") from None
    if total == 0:
        raise BulkImportError("The CSV file contains no data rows.")
    if total > settings.BULK_IMPORT_MAX_ROWS:
        raise BulkImportError(f"Too many rows ({total}); the limit is {settings.BULK_IMPORT_MAX_ROWS}.")
    if len(passwords) > settings.BULK_IMPORT_MAX_DISTINCT_PASSWORDS:
        raise BulkImportError(
            f"Too many different passwords ({len(passwords)}); the limit is "
            f"{settings.BULK_IMPORT_MAX_DISTINCT_PASSWORDS} per import. Use fewer distinct passwords or split the file."
        )
    return text, total


def _plain(detail) -> dict:
    if isinstance(detail, dict):
        return {k: [str(e) for e in v] if isinstance(v, list | tuple) else str(v) for k, v in detail.items()}
    if isinstance(detail, list | tuple):
        return {"non_field_errors": [str(e) for e in detail]}
    return {"non_field_errors": [str(detail)]}


def _save_progress(job: BulkImportJob, success: int, failed: int, errors: list[dict]) -> None:
    """Persist progress without touching ``status``. Also moves ``updated_at`` so a live job is
    never mistaken for a stuck one by ``fail_stuck_import_jobs``."""
    BulkImportJob.objects.filter(pk=job.pk).update(
        success_count=success, failed_count=failed, errors=errors, updated_at=timezone.now()
    )


def run_import(job: BulkImportJob) -> BulkImportJob:
    job.status = BulkImportJob.Status.PROCESSING
    job.save(update_fields=["status", "updated_at"])
    success = failed = processed = 0
    errors: list[dict] = []
    password_hashes: dict[str, str] = {}  # PBKDF2 is slow: hash each distinct password once per job
    try:
        for line_no, row in iter_rows(job.payload):
            data = {k: v.strip() for k, v in row.items() if k in ALLOWED_COLUMNS and v and v.strip()}
            serializer = LinkSerializer(data=data, context={"password_hashes": password_hashes})
            try:
                if serializer.is_valid():
                    serializer.save(owner=job.owner)
                    success += 1
                    detail = None
                else:
                    detail = serializer.errors
            except serializers.ValidationError as exc:
                detail = exc.detail
            if detail is not None:
                failed += 1
                if len(errors) < MAX_STORED_ERRORS:
                    errors.append({"row": line_no, "errors": _plain(detail)})
            processed += 1
            if processed % settings.BULK_IMPORT_CHUNK == 0:
                _save_progress(job, success, failed, errors)
        job.status = BulkImportJob.Status.COMPLETED
    except SoftTimeLimitExceeded:
        logger.error("Bulk import %s hit the time limit after %s rows", job.pk, processed)
        job.status = BulkImportJob.Status.FAILED
        errors.append(
            {
                "row": 0,
                "errors": {
                    "non_field_errors": [
                        f"Time limit exceeded after {processed} rows; {success} links were created. "
                        "Upload the remaining rows as a new file."
                    ]
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk import %s failed", job.pk)
        job.status = BulkImportJob.Status.FAILED
        errors.append({"row": 0, "errors": {"non_field_errors": [str(exc)]}})
    job.success_count, job.failed_count, job.errors = success, failed, errors
    job.payload = ""  # the CSV may contain plaintext passwords - never keep it after processing
    job.finished_at = timezone.now()
    job.save()
    return job
