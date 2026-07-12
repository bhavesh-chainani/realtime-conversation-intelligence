"""Inference worker loop (poll SQLite or consume SQS)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time

from .config import (
    AWS_REGION,
    AWS_SQS_INFERENCE_QUEUE_URL,
    INFERENCE_QUEUE_MODE,
    JOB_STORE_BACKEND,
)
from .job_store import JOB_LEDGER, NullJobLedger
from .worker_runtime import dispatch_inference_job

logger = logging.getLogger(__name__)


async def process_job_id(job_id: str) -> None:
    """Load job_id, gate pending→processing, run dispatcher, finalize."""
    if isinstance(JOB_LEDGER, NullJobLedger):
        logger.error("JOB_LEDGER is null — nothing to process")
        return

    row = JOB_LEDGER.fetch_for_worker(job_id)
    if not row:
        logger.warning("[worker] job not found job_id=%s", job_id)
        return

    status = row.get("status") or ""

    if status == "completed":
        return

    if status == "pending":
        if not JOB_LEDGER.mark_processing(job_id):
            logger.info("[worker] skip job_id=%s (already claimed)", job_id)
            return
        row = JOB_LEDGER.fetch_for_worker(job_id)
        if not row or row.get("status") != "processing":
            return

    elif status != "processing":
        logger.info("[worker] unexpected status=%s job_id=%s", status, job_id)
        return

    try:
        result = await dispatch_inference_job(row)
        JOB_LEDGER.mark_completed(job_id, result)
    except Exception as exc:
        logger.exception("[worker] job failed job_id=%s", job_id)
        JOB_LEDGER.mark_failed(job_id, str(exc))


async def process_poll_claim_row(row: dict) -> None:
    job_id = row["job_id"]
    try:
        row_full = JOB_LEDGER.fetch_for_worker(job_id)
        if not row_full or row_full.get("status") != "processing":
            return
        result = await dispatch_inference_job(row_full)
        JOB_LEDGER.mark_completed(job_id, result)
    except Exception as exc:
        logger.exception("[worker] job failed job_id=%s", job_id)
        JOB_LEDGER.mark_failed(job_id, str(exc))


def run_poll_loop_forever(interval_sec: float = 2.0) -> None:
    if isinstance(JOB_LEDGER, NullJobLedger):
        logger.error("Cannot poll jobs: NullJobLedger")
        sys.exit(1)
    if JOB_STORE_BACKEND == "dynamodb":
        logger.error(
            "DynamoDB job ledger does not support poll mode; use INFERENCE_QUEUE_MODE=sqs"
        )
        sys.exit(1)
    logger.info("Inference worker (poll mode) starting, interval=%ss", interval_sec)
    while True:
        row = JOB_LEDGER.claim_next_sqlite_poll()
        if row:
            asyncio.run(process_poll_claim_row(row))
        else:
            time.sleep(interval_sec)


def run_sqs_loop_forever() -> None:
    if not AWS_SQS_INFERENCE_QUEUE_URL:
        logger.error("AWS_SQS_INFERENCE_QUEUE_URL missing")
        sys.exit(1)
    import boto3

    sqs = boto3.client("sqs", region_name=AWS_REGION or "us-east-1")
    url = AWS_SQS_INFERENCE_QUEUE_URL
    logger.info("Inference worker (sqs mode) polling %s", url)
    while True:
        resp = sqs.receive_message(
            QueueUrl=url,
            WaitTimeSeconds=20,
            MaxNumberOfMessages=5,
            VisibilityTimeout=900,
        )
        for msg in resp.get("Messages", []):
            rcpt = msg.get("ReceiptHandle")
            try:
                body = json.loads(msg.get("Body") or "{}")
                job_id = str(body.get("job_id") or "")
                if not job_id:
                    logger.warning("[worker] SQS missing job_id")
                    continue
                asyncio.run(process_job_id(job_id))
            except json.JSONDecodeError:
                logger.warning("[worker] malformed JSON in SQS message")
            except Exception:
                logger.exception("[worker] SQS handler error")
            finally:
                if rcpt:
                    try:
                        sqs.delete_message(QueueUrl=url, ReceiptHandle=rcpt)
                    except Exception as exc:
                        logger.warning("delete_message failed: %s", exc)


def main() -> None:
    from .logging_config import setup_logging

    setup_logging()
    mode = INFERENCE_QUEUE_MODE
    if mode == "sqs":
        run_sqs_loop_forever()
    else:
        run_poll_loop_forever()


if __name__ == "__main__":
    main()
