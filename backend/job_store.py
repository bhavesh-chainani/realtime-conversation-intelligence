"""Async inference job ledger — SQLite or DynamoDB (same table as sessions, pk JOB#uuid)."""

from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from .config import (
    AWS_REGION,
    DYNAMODB_CONVERSATIONS_TABLE,
    JOB_STORE_BACKEND,
    SQLITE_DB_PATH,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobLedger(ABC):
    @abstractmethod
    def create_pending(
        self,
        *,
        job_id: str,
        user_key: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        pass

    @abstractmethod
    def get_owned(self, job_id: str, user_key: str) -> dict[str, Any] | None:
        pass

    @abstractmethod
    def fetch_for_worker(self, job_id: str) -> dict[str, Any] | None:
        """Worker-facing load (no user check)."""

    @abstractmethod
    def mark_processing(self, job_id: str) -> bool:
        pass

    @abstractmethod
    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        pass

    @abstractmethod
    def mark_failed(self, job_id: str, error: str) -> None:
        pass

    @abstractmethod
    def claim_next_sqlite_poll(self) -> dict[str, Any] | None:
        """Poll mode only: atomically grab one pending row."""


class NullJobLedger(JobLedger):
    def create_pending(self, **kwargs: Any) -> None:
        return None

    def get_owned(self, job_id: str, user_key: str) -> dict[str, Any] | None:
        return None

    def fetch_for_worker(self, job_id: str) -> dict[str, Any] | None:
        return None

    def mark_processing(self, job_id: str) -> bool:
        return False

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        return None

    def mark_failed(self, job_id: str, error: str) -> None:
        return None

    def claim_next_sqlite_poll(self) -> dict[str, Any] | None:
        return None


class SqliteJobLedger(JobLedger):
    def __init__(self, db_path: pathlib.Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30, check_same_thread=False)

    def _init(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS inference_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

    def create_pending(
        self,
        *,
        job_id: str,
        user_key: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO inference_jobs
                (job_id, user_key, job_type, payload_json, status, result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    user_key,
                    job_type,
                    json.dumps(payload, default=str),
                    now,
                    now,
                ),
            )

    def get_owned(self, job_id: str, user_key: str) -> dict[str, Any] | None:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                """
                SELECT job_id, job_type, status, result_json, error, created_at, updated_at
                FROM inference_jobs WHERE job_id = ? AND user_key = ?
                """,
                (job_id, user_key),
            ).fetchone()
            if not row:
                return None
            out = dict(row)
            if out.get("result_json"):
                try:
                    out["result"] = json.loads(out.pop("result_json"))
                except json.JSONDecodeError:
                    out["result"] = None
                    out.pop("result_json", None)
            else:
                out.pop("result_json", None)
            return out

    def fetch_for_worker(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT * FROM inference_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["payload"] = json.loads(d.pop("payload_json"))
            except json.JSONDecodeError:
                d["payload"] = {}
            return d

    def mark_processing(self, job_id: str) -> bool:
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                """
                UPDATE inference_jobs SET status = 'processing', updated_at = ?
                WHERE job_id = ? AND status = 'pending'
                """,
                (now, job_id),
            )
            return cur.rowcount > 0

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                """
                UPDATE inference_jobs SET status = 'completed', result_json = ?, error = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(result, default=str), now, job_id),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                """
                UPDATE inference_jobs SET status = 'failed', error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (error[:16000], now, job_id),
            )

    def claim_next_sqlite_poll(self) -> dict[str, Any] | None:
        """Single-writer optimistic claim for local poll workers."""
        now = _now()
        job_id = None
        try:
            with self._conn() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("""
                    SELECT job_id FROM inference_jobs
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """).fetchone()
                if not row:
                    c.execute("COMMIT")
                    return None
                job_id = row[0]
                c.execute(
                    """
                    UPDATE inference_jobs SET status = 'processing', updated_at = ?
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (now, job_id),
                )
                c.execute("COMMIT")
        except Exception as exc:
            logger.warning("[jobs] sqlite claim failed: %s", exc)
            return None
        return self.fetch_for_worker(job_id) if job_id else None


class DynamoJobLedger(JobLedger):
    META = "META"

    def __init__(self, table_name: str, region: str):
        import boto3

        self._table_name = table_name
        self._ddb = boto3.resource("dynamodb", region_name=region)

    @property
    def _tbl(self):  # type: ignore
        return self._ddb.Table(self._table_name)

    def create_pending(
        self,
        *,
        job_id: str,
        user_key: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> None:
        now = _now()
        pk = f"JOB#{job_id}"
        self._tbl.put_item(
            Item={
                "pk": pk,
                "sk": self.META,
                "job_id": job_id,
                "user_key": user_key,
                "job_type": job_type,
                "payload_json": json.dumps(payload, default=str)[:350000],
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
        )

    def _row_meta(self, item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item:
            return None
        return item

    def get_owned(self, job_id: str, user_key: str) -> dict[str, Any] | None:
        r = self._tbl.get_item(Key={"pk": f"JOB#{job_id}", "sk": self.META})
        item = self._row_meta(r.get("Item"))
        if not item or item.get("user_key") != user_key:
            return None
        return self._normalize_public(item)

    def fetch_for_worker(self, job_id: str) -> dict[str, Any] | None:
        r = self._tbl.get_item(Key={"pk": f"JOB#{job_id}", "sk": self.META})
        item = r.get("Item")
        if not item:
            return None
        try:
            payload = json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        item["payload"] = payload
        item.pop("payload_json", None)
        return item

    def _normalize_public(self, item: dict[str, Any]) -> dict[str, Any]:
        out = {
            "job_id": item.get("job_id"),
            "job_type": item.get("job_type"),
            "status": item.get("status"),
            "error": item.get("error"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
        rj = item.get("result_json")
        if rj:
            try:
                out["result"] = json.loads(rj)
            except json.JSONDecodeError:
                out["result"] = None
        return out

    def mark_processing(self, job_id: str) -> bool:
        from botocore.exceptions import ClientError

        now = _now()
        try:
            self._tbl.update_item(
                Key={"pk": f"JOB#{job_id}", "sk": self.META},
                UpdateExpression="SET #st = :p, updated_at = :u",
                ConditionExpression="#st = :pend",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":p": "processing",
                    ":u": now,
                    ":pend": "pending",
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        now = _now()
        self._tbl.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": self.META},
            UpdateExpression="SET #st = :c, result_json = :r, error = :empty, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":c": "completed",
                ":r": json.dumps(result, default=str)[:350000],
                ":empty": None,
                ":u": now,
            },
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        now = _now()
        self._tbl.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": self.META},
            UpdateExpression="SET #st = :f, error = :e, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":f": "failed",
                ":e": error[:350000],
                ":u": now,
            },
        )

    def claim_next_sqlite_poll(self) -> dict[str, Any] | None:
        return None


def build_job_ledger() -> JobLedger:
    jb = JOB_STORE_BACKEND
    if jb == "none":
        return NullJobLedger()
    if jb == "sqlite":
        return SqliteJobLedger(SQLITE_DB_PATH)
    if jb == "dynamodb":
        if not DYNAMODB_CONVERSATIONS_TABLE:
            logger.warning("JOB_STORE_BACKEND=dynamodb but no Dynamo table configured")
            return NullJobLedger()
        return DynamoJobLedger(
            DYNAMODB_CONVERSATIONS_TABLE,
            AWS_REGION or "us-east-1",
        )
    logger.warning("Unknown JOB_STORE_BACKEND=%s; jobs disabled", jb)
    return NullJobLedger()


JOB_LEDGER = build_job_ledger()
