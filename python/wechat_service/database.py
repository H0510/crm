"""SQLite 持久化。

四张表：CRM 联系人、微信候选、好友申请任务、任务流转审计。
每次操作独立连接，避免多线程共享同一个 sqlite 连接。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "wechat_service.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crm_contact_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    wechat_id TEXT NOT NULL DEFAULT '',
    wechat_nickname TEXT NOT NULL DEFAULT '',
    wechat_remark TEXT NOT NULL DEFAULT '',
    match_status TEXT NOT NULL DEFAULT 'unmatched',
    friend_status TEXT NOT NULL DEFAULT 'none',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wechat_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hwnd INTEGER NOT NULL DEFAULT 0,
    window_title TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    nickname TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    wechat_id TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'low',
    x INTEGER NOT NULL DEFAULT 0,
    y INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS friend_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crm_contact_id TEXT NOT NULL,
    status TEXT NOT NULL,
    search_key TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '{}',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_friend_tasks_contact ON friend_tasks (crm_contact_id);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events (task_id);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class WeChatDatabase:
    """微信模块的本地数据访问层。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._migrate_candidates(connection)

    @staticmethod
    def _migrate_candidates(connection: sqlite3.Connection) -> None:
        """给旧库补采集字段，只加列，不改已有列。"""
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(wechat_candidates)")
        }
        additions = {
            "remark": "TEXT NOT NULL DEFAULT ''",
            "region": "TEXT NOT NULL DEFAULT ''",
            "email": "TEXT NOT NULL DEFAULT ''",
            "source": "TEXT NOT NULL DEFAULT ''",
            "fields_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE wechat_candidates ADD COLUMN {column} {definition}"
                )

    # ------------------------------------------------------------ 联系人

    def upsert_contact(self, contact: dict) -> dict:
        now = _now()
        crm_contact_id = (contact.get("crm_contact_id") or "").strip()
        if not crm_contact_id:
            raise ValueError("crm_contact_id 不能为空")

        values = {
            "crm_contact_id": crm_contact_id,
            "name": contact.get("name") or "",
            "phone": contact.get("phone") or "",
            "wechat_id": contact.get("wechat_id") or "",
            "wechat_nickname": contact.get("wechat_nickname") or "",
            "wechat_remark": contact.get("wechat_remark") or "",
            "now": now,
        }

        with self.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM crm_contacts WHERE crm_contact_id = ?",
                (crm_contact_id,),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE crm_contacts
                       SET name = ?, phone = ?, wechat_id = ?, wechat_nickname = ?,
                           wechat_remark = ?, updated_at = ?
                     WHERE crm_contact_id = ?
                    """,
                    (
                        values["name"],
                        values["phone"],
                        values["wechat_id"],
                        values["wechat_nickname"],
                        values["wechat_remark"],
                        values["now"],
                        crm_contact_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO crm_contacts
                        (crm_contact_id, name, phone, wechat_id, wechat_nickname,
                         wechat_remark, match_status, friend_status, last_error,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'unmatched', 'none', '', ?, ?)
                    """,
                    (
                        values["crm_contact_id"],
                        values["name"],
                        values["phone"],
                        values["wechat_id"],
                        values["wechat_nickname"],
                        values["wechat_remark"],
                        values["now"],
                        values["now"],
                    ),
                )
            row = connection.execute(
                "SELECT * FROM crm_contacts WHERE crm_contact_id = ?",
                (crm_contact_id,),
            ).fetchone()
        return dict(row)

    def get_contact(self, crm_contact_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM crm_contacts WHERE crm_contact_id = ?",
                (crm_contact_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_contacts(self, match_status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM crm_contacts"
        params: tuple = ()
        if match_status:
            sql += " WHERE match_status = ?"
            params = (match_status,)
        sql += " ORDER BY updated_at DESC"
        with self.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def update_match_status(
        self, crm_contact_id: str, match_status: str, last_error: str = ""
    ) -> dict:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE crm_contacts
                   SET match_status = ?, last_error = ?, updated_at = ?
                 WHERE crm_contact_id = ?
                """,
                (match_status, last_error, _now(), crm_contact_id),
            )
            row = connection.execute(
                "SELECT * FROM crm_contacts WHERE crm_contact_id = ?",
                (crm_contact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"联系人不存在: {crm_contact_id}")
        return dict(row)

    def update_friend_status(self, crm_contact_id: str, friend_status: str) -> dict:
        with self.connection() as connection:
            connection.execute(
                "UPDATE crm_contacts SET friend_status = ?, updated_at = ? WHERE crm_contact_id = ?",
                (friend_status, _now(), crm_contact_id),
            )
            row = connection.execute(
                "SELECT * FROM crm_contacts WHERE crm_contact_id = ?",
                (crm_contact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"联系人不存在: {crm_contact_id}")
        return dict(row)

    # ------------------------------------------------------------ 候选

    def save_candidates(self, candidates: list[dict], replace: bool = True) -> int:
        with self.connection() as connection:
            if replace:
                connection.execute("DELETE FROM wechat_candidates")
            for item in candidates:
                connection.execute(
                    """
                    INSERT INTO wechat_candidates
                        (hwnd, window_title, raw_text, nickname, phone, wechat_id,
                         confidence, x, y, width, height, scanned_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(item.get("hwnd") or 0),
                        item.get("window_title") or "",
                        item.get("raw_text") or "",
                        item.get("nickname") or "",
                        item.get("phone") or "",
                        item.get("wechat_id") or "",
                        item.get("confidence") or "low",
                        int(item.get("x") or 0),
                        int(item.get("y") or 0),
                        int(item.get("width") or 0),
                        int(item.get("height") or 0),
                        item.get("scanned_at") or _now(),
                    ),
                )
        return len(candidates)

    def list_candidates(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM wechat_candidates ORDER BY confidence DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_profile_candidate(self, item: dict) -> dict:
        """保存通讯录采集结果：有微信号按微信号去重，否则按昵称加电话。"""
        now = item.get("scanned_at") or _now()
        wechat_id = (item.get("wechat_id") or "").strip()
        nickname = (item.get("nickname") or "").strip()
        phone = (item.get("phone") or "").strip()
        values = (
            int(item.get("hwnd") or 0),
            item.get("window_title") or "",
            item.get("raw_text") or "",
            nickname,
            phone,
            wechat_id,
            item.get("confidence") or "low",
            int(item.get("x") or 0),
            int(item.get("y") or 0),
            int(item.get("width") or 0),
            int(item.get("height") or 0),
            now,
            item.get("remark") or "",
            item.get("region") or "",
            item.get("email") or "",
            item.get("source") or "address_book",
            item.get("fields_json") or "{}",
        )

        with self.connection() as connection:
            row = None
            if wechat_id:
                row = connection.execute(
                    """
                    SELECT id, confidence FROM wechat_candidates
                     WHERE wechat_id <> '' AND lower(wechat_id) = lower(?)
                     ORDER BY id DESC LIMIT 1
                    """,
                    (wechat_id,),
                ).fetchone()
            if row is None and (nickname or phone):
                row = connection.execute(
                    """
                    SELECT id, confidence FROM wechat_candidates
                     WHERE lower(nickname) = lower(?) AND phone = ?
                     ORDER BY id DESC LIMIT 1
                    """,
                    (nickname, phone),
                ).fetchone()

            if row:
                connection.execute(
                    """
                    UPDATE wechat_candidates
                       SET hwnd = ?, window_title = ?, raw_text = ?, nickname = ?,
                           phone = ?, wechat_id = ?, confidence = ?, x = ?, y = ?,
                           width = ?, height = ?, scanned_at = ?, remark = ?,
                           region = ?, email = ?, source = ?, fields_json = ?
                     WHERE id = ?
                    """,
                    values + (row["id"],),
                )
                return {"id": row["id"], "action": "updated"}

            cursor = connection.execute(
                """
                INSERT INTO wechat_candidates
                    (hwnd, window_title, raw_text, nickname, phone, wechat_id,
                     confidence, x, y, width, height, scanned_at, remark,
                     region, email, source, fields_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return {"id": int(cursor.lastrowid), "action": "created"}

    # ------------------------------------------------------------ 任务

    def create_task(
        self,
        crm_contact_id: str,
        status: str,
        search_key: str = "",
        evidence: dict | None = None,
    ) -> dict:
        now = _now()
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO friend_tasks
                    (crm_contact_id, status, search_key, evidence, last_error,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, '', ?, ?)
                """,
                (
                    crm_contact_id,
                    status,
                    search_key,
                    json.dumps(evidence or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM friend_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._task_row_to_dict(row)

    def get_task(self, task_id: int) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT t.*, c.name AS contact_name, c.phone AS contact_phone,
                       c.wechat_id AS contact_wechat_id, c.friend_status AS contact_friend_status
                  FROM friend_tasks t
                  LEFT JOIN crm_contacts c ON t.crm_contact_id = c.crm_contact_id
                 WHERE t.id = ?
                """,
                (task_id,),
            ).fetchone()
        return self._task_row_to_dict(row) if row else None

    def list_tasks(self, status: str | None = None) -> list[dict]:
        sql = """
            SELECT t.*, c.name AS contact_name, c.phone AS contact_phone,
                   c.wechat_id AS contact_wechat_id, c.friend_status AS contact_friend_status
              FROM friend_tasks t
              LEFT JOIN crm_contacts c ON t.crm_contact_id = c.crm_contact_id
        """
        params: tuple = ()
        if status:
            sql += " WHERE t.status = ?"
            params = (status,)
        sql += " ORDER BY t.updated_at DESC"
        with self.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._task_row_to_dict(row) for row in rows]

    def update_task_evidence(self, task_id: int, evidence: dict) -> dict:
        with self.connection() as connection:
            connection.execute(
                "UPDATE friend_tasks SET evidence = ?, updated_at = ? WHERE id = ?",
                (json.dumps(evidence or {}, ensure_ascii=False), _now(), task_id),
            )
            row = connection.execute(
                "SELECT * FROM friend_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"任务不存在: {task_id}")
        return self._task_row_to_dict(row)

    def update_task_status(self, task_id: int, status: str, last_error: str = "") -> dict:
        with self.connection() as connection:
            connection.execute(
                "UPDATE friend_tasks SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (status, last_error, _now(), task_id),
            )
            row = connection.execute(
                "SELECT * FROM friend_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"任务不存在: {task_id}")
        return self._task_row_to_dict(row)

    def add_task_event(
        self,
        task_id: int,
        from_status: str,
        to_status: str,
        actor: str,
        note: str = "",
    ) -> dict:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_events
                    (task_id, from_status, to_status, actor, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, from_status, to_status, actor, note, _now()),
            )
            row = connection.execute(
                "SELECT * FROM task_events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def list_task_events(self, task_id: int) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _task_row_to_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["evidence"] = json.loads(data.get("evidence") or "{}")
        except json.JSONDecodeError:
            data["evidence"] = {}
        data.setdefault("contact_name", "")
        data.setdefault("contact_phone", "")
        data.setdefault("contact_wechat_id", "")
        data.setdefault("contact_friend_status", "none")
        return data
