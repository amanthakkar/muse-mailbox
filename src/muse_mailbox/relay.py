"""Blind relay: HTTP API matching PROTOCOL.md §2.

Scratch stand-in for Worker + Durable Object + R2. One lock per mailbox
serializes POSTs the way a Durable Object input gate does.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from typing import Any

from .canon import payload_hash_bytes
from .crypto import b64decode
from .store import Store, parse_rfc3339, rfc3339, utcnow
from .tokens import extract_bearer, token_hash

MAX_BODY = 256 * 1024
MAX_CT = 180 * 1024
SEND_RATE = 60
FETCH_RATE = 300
STALE_SECONDS = 300


class RelayError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail

    def body(self) -> dict:
        return {"error": self.code, "detail": self.detail}


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60.0:
                q.popleft()
            if len(q) >= limit:
                raise RelayError(429, "rate_limited", "slow down")
            q.append(now)


class Relay:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.limiter = RateLimiter()

    def mailbox_lock(self, mailbox_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(mailbox_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[mailbox_id] = lock
            return lock

    def handle(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        body: bytes,
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        try:
            status, payload = self._dispatch(method, path, headers, body, query or {})
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            return status, {"Content-Type": "application/json"}, raw
        except RelayError as exc:
            extra = {}
            if exc.status == 429:
                extra["Retry-After"] = "1"
            raw = json.dumps(exc.body(), separators=(",", ":")).encode("utf-8")
            return exc.status, {"Content-Type": "application/json", **extra}, raw

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        parts = [p for p in path.split("/") if p]
        if len(parts) != 4 or parts[0] != "v1" or parts[1] != "mailboxes":
            raise RelayError(404, "mailbox_not_found", "no such route")
        mailbox_id, action = parts[2], parts[3]
        if action == "stats" and method == "GET":
            return 200, self.stats(mailbox_id)
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        token = extract_bearer(auth)
        if action == "messages" and method == "POST":
            return 200, self.submit(mailbox_id, token, body)
        if action == "messages" and method == "GET":
            since = int(query.get("since", "0") or "0")
            limit = int(query.get("limit", "50") or "50")
            return 200, self.fetch(mailbox_id, token, since, limit)
        raise RelayError(404, "mailbox_not_found", "no such route")

    def stats(self, mailbox_id: str) -> dict[str, int]:
        seq = self.store.highest_seq(mailbox_id)
        # Unknown IDs look like idle mailboxes so the presence oracle is not an
        # existence oracle. Contract left this unspecified; see CONTRACT_REVIEW.md.
        return {"highest_seq": int(seq or 0)}

    def _auth(
        self, mailbox_id: str, token: str | None, direction: str
    ) -> dict[str, str]:
        if not token:
            raise RelayError(401, "invalid_token", "missing bearer")
        row = self.store.lookup_token(token_hash(token))
        if row is None:
            raise RelayError(401, "invalid_token", "unknown token")
        if row["mailbox_id"] != mailbox_id:
            raise RelayError(404, "mailbox_not_found", "mismatch")
        if row["direction"] != direction:
            raise RelayError(403, "forbidden", "wrong direction")
        return {"participant": row["participant"], "direction": row["direction"]}

    def submit(self, mailbox_id: str, token: str | None, body: bytes) -> dict:
        if len(body) > MAX_BODY:
            raise RelayError(413, "payload_too_large", "body exceeds 256 KiB")
        binding = self._auth(mailbox_id, token, "send")
        self.limiter.check(f"send:{token}", SEND_RATE)
        try:
            env = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RelayError(400, "invalid_request", "malformed json") from exc
        if not isinstance(env, dict):
            raise RelayError(400, "invalid_request", "object required")
        required = ("v", "from", "to", "message_id", "sent_at", "eph", "nonce", "ct")
        if any(k not in env for k in required):
            raise RelayError(400, "invalid_request", "missing field")
        if env.get("v") != 2:
            raise RelayError(400, "invalid_request", "v must be 2")
        from_id, to_id = env["from"], env["to"]
        if from_id != binding["participant"]:
            raise RelayError(403, "forbidden", "from does not match token")
        peer = "gauri" if from_id == "aman" else "aman"
        if to_id != peer:
            raise RelayError(403, "forbidden", "to does not match counterparty")
        try:
            eph = b64decode(env["eph"])
            nonce = b64decode(env["nonce"])
            ct = b64decode(env["ct"])
        except Exception as exc:
            raise RelayError(400, "invalid_request", "invalid base64") from exc
        if len(eph) != 32 or len(nonce) != 12:
            raise RelayError(400, "invalid_request", "eph/nonce length")
        if len(ct) > MAX_CT:
            raise RelayError(413, "payload_too_large", "ct exceeds 180 KiB")
        message_id = env["message_id"]
        p_hash = payload_hash_bytes(
            v=2,
            from_id=from_id,
            to_id=to_id,
            message_id=message_id,
            eph=env["eph"],
            nonce=env["nonce"],
            ct=env["ct"],
        )
        with self.mailbox_lock(mailbox_id):
            existing = self.store.idempotency_lookup(mailbox_id, message_id)
            if existing is not None:
                if existing["payload_hash"] == p_hash:
                    return {
                        "seq": int(existing["seq"]),
                        "message_id": message_id,
                        "duplicate": True,
                        "accepted_at": rfc3339(utcnow()),
                    }
                raise RelayError(
                    409, "idempotency_conflict", "message_id reused with different payload"
                )
            try:
                sent_at = parse_rfc3339(env["sent_at"])
            except Exception as exc:
                raise RelayError(400, "invalid_request", "bad sent_at") from exc
            skew = abs((utcnow() - sent_at).total_seconds())
            if skew > STALE_SECONDS:
                raise RelayError(422, "stale_timestamp", "sent_at skew > 300s")
            if not self.store.mailbox_exists(mailbox_id):
                raise RelayError(404, "mailbox_not_found", "mismatch")
            seq = self.store.insert_message(
                {
                    "mailbox_id": mailbox_id,
                    "from_id": from_id,
                    "to_id": to_id,
                    "message_id": message_id,
                    "payload_hash": p_hash,
                    "sent_at": env["sent_at"],
                    "eph": env["eph"],
                    "nonce": env["nonce"],
                    "ct": env["ct"],
                }
            )
            return {
                "seq": seq,
                "message_id": message_id,
                "duplicate": False,
                "accepted_at": rfc3339(utcnow()),
            }

    def fetch(
        self, mailbox_id: str, token: str | None, since: int, limit: int
    ) -> dict:
        binding = self._auth(mailbox_id, token, "fetch")
        self.limiter.check(f"fetch:{token}", FETCH_RATE)
        if limit < 1:
            raise RelayError(400, "invalid_request", "limit")
        if limit > 200:
            limit = 200
        if since < 0:
            raise RelayError(400, "invalid_request", "since")
        if not self.store.mailbox_exists(mailbox_id):
            raise RelayError(404, "mailbox_not_found", "mismatch")
        rows = self.store.fetch_to(mailbox_id, binding["participant"], since, limit)
        # 1 MiB page cap
        kept = []
        size = 0
        for row in rows:
            piece = len(row["ct"].encode("ascii"))
            if kept and size + piece > 1024 * 1024:
                break
            kept.append(row)
            size += piece
        highest = int(self.store.highest_seq(mailbox_id) or 0)
        next_since = kept[-1]["seq"] if kept else since
        return {
            "mailbox_id": mailbox_id,
            "highest_seq": highest,
            "messages": kept,
            "next_since": next_since,
        }
