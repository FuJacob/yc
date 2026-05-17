import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id),
    phone TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('parent', 'kid')),
    onboarding_state TEXT NOT NULL DEFAULT 'pending_verification',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_family ON users(family_id);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_user_by_phone(phone: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE phone = ?", (phone,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def get_kid_for_parent(parent_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.* FROM users u
            JOIN users p ON p.family_id = u.family_id
            WHERE p.id = ? AND u.role = 'kid'
            LIMIT 1
            """,
            (parent_id,),
        ).fetchone()
        return dict(row) if row else None


def get_parent_for_kid(kid_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.* FROM users u
            JOIN users k ON k.family_id = u.family_id
            WHERE k.id = ? AND u.role = 'parent'
            LIMIT 1
            """,
            (kid_id,),
        ).fetchone()
        return dict(row) if row else None


def create_family_with_users(
    *,
    parent_name: str,
    parent_phone: str,
    kid_name: str,
    kid_phone: str,
) -> tuple[int, int, int]:
    """Create family + parent (auto-verified) + kid (pending). Returns ids."""
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO families (created_at) VALUES (?)", (ts,)
        )
        family_id = cur.lastrowid

        cur = conn.execute(
            """
            INSERT INTO users (family_id, phone, name, role, onboarding_state, created_at)
            VALUES (?, ?, ?, 'parent', 'verified', ?)
            """,
            (family_id, parent_phone, parent_name, ts),
        )
        parent_id = cur.lastrowid

        cur = conn.execute(
            """
            INSERT INTO users (family_id, phone, name, role, onboarding_state, created_at)
            VALUES (?, ?, ?, 'kid', 'pending_verification', ?)
            """,
            (family_id, kid_phone, kid_name, ts),
        )
        kid_id = cur.lastrowid

        return family_id, parent_id, kid_id


def set_onboarding_state(user_id: int, state: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET onboarding_state = ? WHERE id = ?",
            (state, user_id),
        )


def delete_family(family_id: int) -> int:
    """Delete a family and all its users. Returns rows deleted (users + 1 family)."""
    with connect() as conn:
        c1 = conn.execute("DELETE FROM users WHERE family_id = ?", (family_id,))
        c2 = conn.execute("DELETE FROM families WHERE id = ?", (family_id,))
        return c1.rowcount + c2.rowcount
