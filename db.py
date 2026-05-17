import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import DB_PATH, PAYMENT_REQUEST_TTL_MINUTES

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
    payout_destination TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_family ON users(family_id);

CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id),
    kid_user_id INTEGER NOT NULL REFERENCES users(id),
    parent_user_id INTEGER REFERENCES users(id),
    request_code TEXT NOT NULL,
    service_name TEXT NOT NULL,
    description TEXT,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL CHECK (
        status IN (
            'pending_parent', 'approved', 'executing',
            'paid', 'failed', 'declined', 'expired'
        )
    ),
    sponge_reference TEXT,
    failure_reason TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payreq_family_status
    ON payment_requests(family_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_payreq_active_code
    ON payment_requests(family_id, request_code)
    WHERE status IN ('pending_parent','approved','executing');

CREATE TABLE IF NOT EXISTS payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_request_id INTEGER NOT NULL REFERENCES payment_requests(id),
    actor_user_id INTEGER REFERENCES users(id),
    event_type TEXT NOT NULL,
    message TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payevt_req ON payment_events(payment_request_id);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Migrate older users table if needed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "payout_destination" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN payout_destination TEXT")


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


def set_payout_destination(user_id: int, destination: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET payout_destination = ? WHERE id = ?",
            (destination, user_id),
        )


# ---------------------------------------------------------------------------
# Payment requests
# ---------------------------------------------------------------------------


def _generate_request_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def create_payment_request(
    *,
    family_id: int,
    kid_user_id: int,
    service_name: str,
    description: str,
    amount_cents: int,
    currency: str = "USD",
) -> dict:
    ts = now_iso()
    expires = (
        datetime.now(timezone.utc)
        + timedelta(minutes=PAYMENT_REQUEST_TTL_MINUTES)
    ).isoformat()

    with connect() as conn:
        for _ in range(10):
            code = _generate_request_code()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO payment_requests (
                        family_id, kid_user_id, request_code,
                        service_name, description, amount_cents, currency,
                        status, expires_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_parent', ?, ?, ?)
                    """,
                    (
                        family_id, kid_user_id, code,
                        service_name, description, amount_cents, currency,
                        expires, ts, ts,
                    ),
                )
                req_id = cur.lastrowid
                _append_event(
                    conn,
                    payment_request_id=req_id,
                    actor_user_id=kid_user_id,
                    event_type="created",
                    message=f"Kid requested {currency} {amount_cents/100:.2f} for {service_name}",
                )
                row = conn.execute(
                    "SELECT * FROM payment_requests WHERE id = ?", (req_id,)
                ).fetchone()
                return dict(row)
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Could not generate unique request code")


def get_payment_request_by_code(family_id: int, request_code: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM payment_requests WHERE family_id = ? AND request_code = ? "
            "ORDER BY id DESC LIMIT 1",
            (family_id, request_code),
        ).fetchone()
        return dict(row) if row else None


def get_payment_request_by_id(req_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM payment_requests WHERE id = ?", (req_id,)
        ).fetchone()
        return dict(row) if row else None


def list_pending_requests_for_family(family_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_requests WHERE family_id = ? AND status = 'pending_parent' "
            "ORDER BY id DESC",
            (family_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_payment_requests_for_family(family_id: int) -> list[dict]:
    """Pending or in-flight requests, for system-prompt context."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_requests WHERE family_id = ? "
            "AND status IN ('pending_parent','approved','executing') "
            "ORDER BY created_at ASC",
            (family_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def transition_status(
    req_id: int,
    *,
    expected: str,
    new: str,
    actor_user_id: Optional[int] = None,
    event_message: str = "",
    metadata: Optional[dict] = None,
    parent_user_id: Optional[int] = None,
    sponge_reference: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> bool:
    """Atomic status transition. Returns True iff row was in `expected` and is now `new`."""
    ts = now_iso()
    with connect() as conn:
        sets = ["status = ?", "updated_at = ?"]
        params: list = [new, ts]
        if parent_user_id is not None:
            sets.append("parent_user_id = ?")
            params.append(parent_user_id)
        if sponge_reference is not None:
            sets.append("sponge_reference = ?")
            params.append(sponge_reference)
        if failure_reason is not None:
            sets.append("failure_reason = ?")
            params.append(failure_reason)
        params.extend([req_id, expected])

        cur = conn.execute(
            f"UPDATE payment_requests SET {', '.join(sets)} "
            f"WHERE id = ? AND status = ?",
            params,
        )
        if cur.rowcount == 0:
            return False

        _append_event(
            conn,
            payment_request_id=req_id,
            actor_user_id=actor_user_id,
            event_type=new,
            message=event_message or f"transition {expected} -> {new}",
            metadata=metadata,
        )
        return True


def _append_event(
    conn,
    *,
    payment_request_id: int,
    actor_user_id: Optional[int],
    event_type: str,
    message: str,
    metadata: Optional[dict] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO payment_events
            (payment_request_id, actor_user_id, event_type, message, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payment_request_id, actor_user_id, event_type, message,
            json.dumps(metadata) if metadata else None,
            now_iso(),
        ),
    )


def append_event(
    *,
    payment_request_id: int,
    actor_user_id: Optional[int],
    event_type: str,
    message: str,
    metadata: Optional[dict] = None,
) -> None:
    with connect() as conn:
        _append_event(
            conn,
            payment_request_id=payment_request_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            message=message,
            metadata=metadata,
        )


def is_expired(req: dict) -> bool:
    try:
        exp = datetime.fromisoformat(req["expires_at"])
    except (KeyError, ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) > exp
