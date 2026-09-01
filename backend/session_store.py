"""Persist call sessions + events — DynamoDB (AWS) or SQLite (local)."""

from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from .config import (
    AWS_REGION,
    DYNAMODB_CONVERSATIONS_TABLE,
    SQLITE_DB_PATH,
    STORAGE_BACKEND,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore(ABC):
    @abstractmethod
    def create_session(self, user_key: str) -> str:
        pass

    @abstractmethod
    def ensure_session_owned(self, session_id: str, user_key: str) -> bool:
        pass

    @abstractmethod
    def update_transcript_snapshot(
        self, session_id: str, user_key: str, transcript: str
    ) -> None:
        pass

    @abstractmethod
    def append_event(
        self, session_id: str, user_key: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        pass

    @abstractmethod
    def get_session(self, session_id: str, user_key: str) -> dict[str, Any] | None:
        pass

    @abstractmethod
    def list_recent_events(
        self, session_id: str, user_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        pass


class NullSessionStore(SessionStore):
    """No persistence when STORAGE_BACKEND explicitly disabled."""

    def create_session(self, user_key: str) -> str:
        return str(uuid.uuid4())

    def ensure_session_owned(self, session_id: str, user_key: str) -> bool:
        return False

    def update_transcript_snapshot(
        self, session_id: str, user_key: str, transcript: str
    ) -> None:
        return None

    def append_event(
        self, session_id: str, user_key: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        return None

    def get_session(self, session_id: str, user_key: str) -> dict[str, Any] | None:
        return None

    def list_recent_events(
        self, session_id: str, user_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        return []


class SqliteSessionStore(SessionStore):
    def __init__(self, db_path: pathlib.Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_transcript TEXT
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
                """
            )

    def create_session(self, user_key: str) -> str:
        sid = str(uuid.uuid4())
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                """INSERT INTO sessions (session_id, user_key, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (sid, user_key, now, now),
            )
        return sid

    def ensure_session_owned(self, session_id: str, user_key: str) -> bool:
        row = self._fetch_meta(session_id)
        return bool(row and row["user_key"] == user_key)

    def _fetch_meta(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            cur = c.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            r = cur.fetchone()
            return dict(r) if r else None

    def update_transcript_snapshot(
        self, session_id: str, user_key: str, transcript: str
    ) -> None:
        if not self.ensure_session_owned(session_id, user_key):
            return
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                "UPDATE sessions SET last_transcript = ?, updated_at = ? WHERE session_id = ?",
                (transcript[:500000], now, session_id),
            )

    def append_event(
        self, session_id: str, user_key: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        if not self.ensure_session_owned(session_id, user_key):
            return
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                """INSERT INTO session_events (session_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, event_type, json.dumps(payload, default=str), now),
            )
            c.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )

    def get_session(self, session_id: str, user_key: str) -> dict[str, Any] | None:
        row = self._fetch_meta(session_id)
        if not row or row["user_key"] != user_key:
            return None
        return {
            "session_id": session_id,
            "user_key": row["user_key"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_transcript_preview": (
                row["last_transcript"][:500] + "..."
                if row.get("last_transcript") and len(row["last_transcript"]) > 500
                else row.get("last_transcript")
            ),
        }

    def list_recent_events(
        self, session_id: str, user_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not self.ensure_session_owned(session_id, user_key):
            return []
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            cur = c.execute(
                """SELECT event_type, payload_json, created_at FROM session_events
                   WHERE session_id = ? ORDER BY id DESC LIMIT ?""",
                (session_id, limit),
            )
            out = []
            for r in cur.fetchall():
                try:
                    payload = json.loads(r["payload_json"])
                except json.JSONDecodeError:
                    payload = {}
                out.append(
                    {
                        "event_type": r["event_type"],
                        "created_at": r["created_at"],
                        "payload": payload,
                    }
                )
            out.reverse()
            return out


class DynamoSessionStore(SessionStore):
    META_SK = "META"

    def __init__(self, table_name: str, region_name: str):
        import boto3
        from boto3.dynamodb.conditions import Key

        self._Key = Key
        self._table = boto3.resource("dynamodb", region_name=region_name).Table(
            table_name
        )

    def create_session(self, user_key: str) -> str:
        sid = str(uuid.uuid4())
        now = _utc_now_iso()
        pk = f"SESSION#{sid}"
        self._table.put_item(
            Item={
                "pk": pk,
                "sk": self.META_SK,
                "session_id": sid,
                "user_key": user_key,
                "created_at": now,
                "updated_at": now,
            }
        )
        return sid

    def ensure_session_owned(self, session_id: str, user_key: str) -> bool:
        meta = self._get_meta(session_id)
        return bool(meta and meta.get("user_key") == user_key)

    def _get_meta(self, session_id: str) -> dict[str, Any] | None:
        pk = f"SESSION#{session_id}"
        r = self._table.get_item(Key={"pk": pk, "sk": self.META_SK})
        return r.get("Item")

    def update_transcript_snapshot(
        self, session_id: str, user_key: str, transcript: str
    ) -> None:
        if not self.ensure_session_owned(session_id, user_key):
            return
        now = _utc_now_iso()
        pk = f"SESSION#{session_id}"
        truncated = transcript[:380000]
        self._table.update_item(
            Key={"pk": pk, "sk": self.META_SK},
            UpdateExpression="SET updated_at = :u, last_transcript = :t",
            ExpressionAttributeValues={":u": now, ":t": truncated},
        )

    def append_event(
        self, session_id: str, user_key: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        if not self.ensure_session_owned(session_id, user_key):
            return
        now = _utc_now_iso()
        sk = f"EVT#{now}#{uuid.uuid4().hex[:12]}"
        pk = f"SESSION#{session_id}"
        payload_str = json.dumps(payload, default=str)
        self._table.put_item(
            Item={
                "pk": pk,
                "sk": sk,
                "session_id": session_id,
                "event_type": event_type,
                "payload_json": payload_str[:350000],
                "created_at": now,
            }
        )
        self._table.update_item(
            Key={"pk": pk, "sk": self.META_SK},
            UpdateExpression="SET updated_at = :u",
            ExpressionAttributeValues={":u": now},
        )

    def get_session(self, session_id: str, user_key: str) -> dict[str, Any] | None:
        meta = self._get_meta(session_id)
        if not meta or meta.get("user_key") != user_key:
            return None
        lt = meta.get("last_transcript") or ""
        preview = lt[:500] + "..." if len(lt) > 500 else lt
        return {
            "session_id": meta.get("session_id"),
            "user_key": meta["user_key"],
            "created_at": meta["created_at"],
            "updated_at": meta["updated_at"],
            "last_transcript_preview": preview or None,
        }

    def list_recent_events(
        self, session_id: str, user_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not self.ensure_session_owned(session_id, user_key):
            return []
        pk = f"SESSION#{session_id}"
        expr = self._Key("pk").eq(pk) & self._Key("sk").begins_with("EVT#")
        r = self._table.query(
            KeyConditionExpression=expr,
            ScanIndexForward=False,
            Limit=limit,
        )
        items = list(reversed(r.get("Items", [])))
        out = []
        for i in items:
            try:
                payload = json.loads(i.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            out.append(
                {
                    "event_type": i.get("event_type", ""),
                    "created_at": i.get("created_at", ""),
                    "payload": payload,
                }
            )
        return out


def get_session_store() -> SessionStore:
    backend = STORAGE_BACKEND
    if backend == "none":
        return NullSessionStore()
    if backend == "sqlite":
        return SqliteSessionStore(SQLITE_DB_PATH)
    if backend == "dynamodb":
        if not DYNAMODB_CONVERSATIONS_TABLE:
            logger.warning(
                "STORAGE_BACKEND=dynamodb but DYNAMODB_CONVERSATIONS_TABLE unset; disabling persistence"
            )
            return NullSessionStore()
        region = AWS_REGION or "us-east-1"
        return DynamoSessionStore(DYNAMODB_CONVERSATIONS_TABLE, region)
    logger.warning("Unknown STORAGE_BACKEND=%s; disabling persistence", backend)
    return NullSessionStore()


SESSION_STORE = get_session_store()
