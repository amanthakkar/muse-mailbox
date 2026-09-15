"""SQLite store that models Durable Object storage + R2 blobs.

One connection, WAL, serialized by the relay lock (DO input gate).
Ciphertext lives in the `blobs` table (R2 stand-in); sequencing/dedupe/presence
live in `mailboxes` / `messages` / `idempotency` (DO stand-in).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS mailboxes (
  mailbox_id TEXT PRIMARY KEY,
  highest_seq INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  mailbox_id TEXT NOT NULL,
  participant TEXT NOT NULL,
  direction TEXT NOT NULL,
  FOREIGN KEY (mailbox_id) REFERENCES mailboxes(mailbox_id)
);

CREATE TABLE IF NOT EXISTS messages (
  mailbox_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  payload_hash BLOB NOT NULL,
  sent_at TEXT NOT NULL,
  stored_at TEXT NOT NULL,
  eph TEXT NOT NULL,
  nonce TEXT NOT NULL,
  PRIMARY KEY (mailbox_id, seq),
  UNIQUE (mailbox_id, message_id),
  FOREIGN KEY (mailbox_id) REFERENCES mailboxes(mailbox_id)
);

CREATE TABLE IF NOT EXISTS blobs (
  mailbox_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ct TEXT NOT NULL,
  PRIMARY KEY (mailbox_id, seq),
  FOREIGN KEY (mailbox_id, seq) REFERENCES messages(mailbox_id, seq)
);

CREATE TABLE IF NOT EXISTS idempotency (
  mailbox_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  payload_hash BLOB NOT NULL,
  seq INTEGER NOT NULL,
  PRIMARY KEY (mailbox_id, message_id)
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def create_mailbox(self, mailbox_id: str) -> None:
        self.conn.execute(
            "INSERT INTO mailboxes(mailbox_id, highest_seq, created_at) VALUES (?,?,?)",
            (mailbox_id, 0, rfc3339(utcnow())),
        )

    def add_token(
        self, token_hash: str, mailbox_id: str, participant: str, direction: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO tokens(token_hash, mailbox_id, participant, direction) VALUES (?,?,?,?)",
            (token_hash, mailbox_id, participant, direction),
        )

    def lookup_token(self, token_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()

    def mailbox_exists(self, mailbox_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM mailboxes WHERE mailbox_id = ?", (mailbox_id,)
        ).fetchone()
        return row is not None

    def highest_seq(self, mailbox_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT highest_seq FROM mailboxes WHERE mailbox_id = ?", (mailbox_id,)
        ).fetchone()
        if row is None:
            return None
        return int(row["highest_seq"])

    def idempotency_lookup(
        self, mailbox_id: str, message_id: str
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT payload_hash, seq FROM idempotency WHERE mailbox_id = ? AND message_id = ?",
            (mailbox_id, message_id),
        ).fetchone()

    def insert_message(self, row: dict[str, Any]) -> int:
        """Assign next seq and store metadata + blob in one transaction."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                "SELECT highest_seq FROM mailboxes WHERE mailbox_id = ?",
                (row["mailbox_id"],),
            ).fetchone()
            if cur is None:
                raise KeyError("mailbox_not_found")
            seq = int(cur["highest_seq"]) + 1
            stored_at = rfc3339(utcnow())
            self.conn.execute(
                """INSERT INTO messages(
                    mailbox_id, seq, from_id, to_id, message_id, payload_hash,
                    sent_at, stored_at, eph, nonce
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["mailbox_id"],
                    seq,
                    row["from_id"],
                    row["to_id"],
                    row["message_id"],
                    row["payload_hash"],
                    row["sent_at"],
                    stored_at,
                    row["eph"],
                    row["nonce"],
                ),
            )
            self.conn.execute(
                "INSERT INTO blobs(mailbox_id, seq, ct) VALUES (?,?,?)",
                (row["mailbox_id"], seq, row["ct"]),
            )
            self.conn.execute(
                "INSERT INTO idempotency(mailbox_id, message_id, payload_hash, seq) VALUES (?,?,?,?)",
                (row["mailbox_id"], row["message_id"], row["payload_hash"], seq),
            )
            self.conn.execute(
                "UPDATE mailboxes SET highest_seq = ? WHERE mailbox_id = ?",
                (seq, row["mailbox_id"]),
            )
            self.conn.execute("COMMIT")
            return seq
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def fetch_to(
        self, mailbox_id: str, participant: str, since: int, limit: int
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT m.seq, m.from_id, m.to_id, m.message_id, m.sent_at, m.stored_at,
                   m.eph, m.nonce, b.ct
            FROM messages m
            JOIN blobs b ON b.mailbox_id = m.mailbox_id AND b.seq = m.seq
            WHERE m.mailbox_id = ? AND m.to_id = ? AND m.seq > ?
            ORDER BY m.seq ASC
            LIMIT ?
            """,
            (mailbox_id, participant, since, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "seq": int(r["seq"]),
                    "v": 2,
                    "from": r["from_id"],
                    "to": r["to_id"],
                    "message_id": r["message_id"],
                    "sent_at": r["sent_at"],
                    "stored_at": r["stored_at"],
                    "eph": r["eph"],
                    "nonce": r["nonce"],
                    "ct": r["ct"],
                }
            )
        return out

    def prune(
        self,
        mailbox_id: str,
        *,
        ttl_days: int = 30,
        max_messages: int = 100_000,
        now: datetime | None = None,
    ) -> int:
        """Delete oldest/expired rows. Never rewinds or reuses seq."""
        now = now or utcnow()
        cutoff = rfc3339(now - timedelta(days=ttl_days))
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """DELETE FROM blobs WHERE mailbox_id = ? AND seq IN (
                     SELECT seq FROM messages WHERE mailbox_id = ? AND stored_at < ?
                   )""",
                (mailbox_id, mailbox_id, cutoff),
            )
            self.conn.execute(
                "DELETE FROM messages WHERE mailbox_id = ? AND stored_at < ?",
                (mailbox_id, cutoff),
            )
            count = self.conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE mailbox_id = ?",
                (mailbox_id,),
            ).fetchone()["n"]
            extra = int(count) - max_messages
            if extra > 0:
                old = self.conn.execute(
                    """SELECT seq FROM messages WHERE mailbox_id = ?
                       ORDER BY seq ASC LIMIT ?""",
                    (mailbox_id, extra),
                ).fetchall()
                seqs = [int(r["seq"]) for r in old]
                if seqs:
                    q = ",".join("?" * len(seqs))
                    self.conn.execute(
                        f"DELETE FROM blobs WHERE mailbox_id = ? AND seq IN ({q})",
                        [mailbox_id, *seqs],
                    )
                    self.conn.execute(
                        f"DELETE FROM messages WHERE mailbox_id = ? AND seq IN ({q})",
                        [mailbox_id, *seqs],
                    )
            self.conn.execute("COMMIT")
            return extra if extra > 0 else 0
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
