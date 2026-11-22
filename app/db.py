"""SQLite 持久层（仅标准库 sqlite3）。

表结构
------
layer          层位
evidence       依据：datum=测年候选区间；superposition=严格早于
registration   登记：拟采用的额外闭区间约束（可更换/撤回）
submission     提交流水：每次提交按最新入库状态核对后的留痕（含被驳回的提交）

所有写操作经 services 层在单个事务内完成；任一校验失败整批回滚，
原编年表（最近一次通过核对的状态）照常可查。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS layer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('datum', 'superposition')),
    layer_id    INTEGER REFERENCES layer(id),
    layer2_id   INTEGER REFERENCES layer(id),
    lower_year  INTEGER,
    upper_year  INTEGER,
    note        TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    withdrawn_at TEXT
);

CREATE TABLE IF NOT EXISTS registration (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    layer_id    INTEGER NOT NULL REFERENCES layer(id),
    lower_year  INTEGER NOT NULL,
    upper_year  INTEGER NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'withdrawn', 'replaced')),
    replaced_by INTEGER REFERENCES registration(id),
    created_at  TEXT NOT NULL,
    withdrawn_at TEXT
);

CREATE TABLE IF NOT EXISTS submission (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accepted    INTEGER NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    # check_same_thread=False：配合调用方的互斥锁在多线程 HTTP 服务中复用单连接
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def get_layer_by_code(conn: sqlite3.Connection, code: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM layer WHERE code = ?", (code,)).fetchone()


def get_layer(conn: sqlite3.Connection, layer_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM layer WHERE id = ?", (layer_id,)).fetchone()


def list_layers(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM layer ORDER BY id").fetchall()


def list_active_dating(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evidence WHERE kind='datum' AND active=1 ORDER BY id"
    ).fetchall()


def list_active_superpositions(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evidence WHERE kind='superposition' AND active=1 ORDER BY id"
    ).fetchall()


def get_evidence(conn: sqlite3.Connection, evidence_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()


def list_active_registrations(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM registration WHERE status='active' ORDER BY id"
    ).fetchall()


def get_registration(conn: sqlite3.Connection, reg_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM registration WHERE id = ?", (reg_id,)).fetchone()


def list_submissions(conn: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM submission ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


# --------------------------------------------------------------------------- #
# 写入（均假定已在事务中）
# --------------------------------------------------------------------------- #
def insert_layer(conn: sqlite3.Connection, code: str, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO layer(code, name, created_at) VALUES (?, ?, ?)",
        (code, name, now_iso()),
    )
    return int(cur.lastrowid)


def insert_dating(
    conn: sqlite3.Connection, layer_id: int, lower: int, upper: int, note: str
) -> int:
    cur = conn.execute(
        "INSERT INTO evidence(kind, layer_id, lower_year, upper_year, note, "
        "active, created_at) VALUES ('datum', ?, ?, ?, ?, 1, ?)",
        (layer_id, lower, upper, note, now_iso()),
    )
    return int(cur.lastrowid)


def insert_superposition(
    conn: sqlite3.Connection, earlier_id: int, later_id: int, note: str
) -> int:
    cur = conn.execute(
        "INSERT INTO evidence(kind, layer_id, layer2_id, note, active, created_at) "
        "VALUES ('superposition', ?, ?, ?, 1, ?)",
        (earlier_id, later_id, note, now_iso()),
    )
    return int(cur.lastrowid)


def deactivate_evidence(conn: sqlite3.Connection, evidence_id: int) -> None:
    conn.execute(
        "UPDATE evidence SET active=0, withdrawn_at=? WHERE id=?",
        (now_iso(), evidence_id),
    )


def insert_registration(
    conn: sqlite3.Connection, layer_id: int, lower: int, upper: int, note: str
) -> int:
    cur = conn.execute(
        "INSERT INTO registration(layer_id, lower_year, upper_year, note, status, "
        "created_at) VALUES (?, ?, ?, ?, 'active', ?)",
        (layer_id, lower, upper, note, now_iso()),
    )
    return int(cur.lastrowid)


def mark_registration(conn: sqlite3.Connection, reg_id: int, status: str,
                      replaced_by: Optional[int] = None) -> None:
    conn.execute(
        "UPDATE registration SET status=?, replaced_by=COALESCE(?, replaced_by), "
        "withdrawn_at=? WHERE id=?",
        (status, replaced_by, now_iso(), reg_id),
    )


def insert_submission(conn: sqlite3.Connection, accepted: bool, reason: str,
                      detail: Dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO submission(accepted, reason, detail, created_at) "
        "VALUES (?, ?, ?, ?)",
        (1 if accepted else 0, reason, json.dumps(detail, ensure_ascii=False), now_iso()),
    )
    return int(cur.lastrowid)
