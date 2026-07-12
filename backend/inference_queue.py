"""Notify async workers (SQS)."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def publish_inference_job(job_id: str) -> None:
    from .config import (
        AWS_REGION,
        AWS_SQS_INFERENCE_QUEUE_URL,
        INFERENCE_QUEUE_MODE,
    )

    if INFERENCE_QUEUE_MODE != "sqs":
        return
    if not AWS_SQS_INFERENCE_QUEUE_URL:
        logger.warning(
            "[queue] INFERENCE_QUEUE_MODE=sqs but AWS_SQS_INFERENCE_QUEUE_URL empty"
        )
        return
    import boto3

    client = boto3.client("sqs", region_name=AWS_REGION or "us-east-1")
    client.send_message(
        QueueUrl=AWS_SQS_INFERENCE_QUEUE_URL,
        MessageBody=json.dumps({"job_id": job_id}),
    )
