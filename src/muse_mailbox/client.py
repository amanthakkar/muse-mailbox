"""Two-cursor receive client + sign-then-encrypt send (PROTOCOL.md §3, §6)."""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from cryptography.exceptions import InvalidTag

from .crypto import (
    Identity,
    PeerPublic,
    encrypt_to,
    sign_inner,
    try_decrypt,
    unpad_signed,
    verify_inner,
)
from .store import parse_rfc3339, rfc3339, utcnow


class Quarantine(Exception):
    def __init__(self, reason: str, envelope: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.envelope = envelope


@dataclass
class ReceiveState:
    fetch_cursor: int = 0
    process_cursor: dict[str, int] = field(default_factory=dict)
    buffer: dict[str, dict[int, dict]] = field(default_factory=dict)
    seen_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    dispatched: list[dict] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    next_sender_seq: int = 1

    def note_seen(self, message_id: str) -> None:
        self.seen_ids[message_id] = None
        while len(self.seen_ids) > 10_000:
            self.seen_ids.popitem(last=False)


@dataclass
class MailboxClient:
    participant: str
    mailbox_id: str
    identity: Identity
    peer: PeerPublic
    send_token: str
    fetch_token: str
    transport: Callable[..., dict]
    state: ReceiveState = field(default_factory=ReceiveState)
    grace_enc_privs: list = field(default_factory=list)

    def _now(self) -> datetime:
        return utcnow()

    def _inner(self, *, to: str, body: str, sender_seq: int, msg_type: str, message_id: str) -> dict:
        sent_at = rfc3339(self._now())
        return {
            "body": body,
            "from": self.participant,
            "from_sign_kid": self.identity.sign_kid,
            "message_id": message_id,
            "sender_seq": sender_seq,
            "sent_at": sent_at,
            "to": to,
            "to_enc_kid": self.peer.enc_kid,
            "type": msg_type,
            "v": 2,
        }

    def compose(self, body: str, *, msg_type: str = "message", sender_seq: int | None = None) -> dict:
        message_id = str(uuid.uuid4())
        if sender_seq is None:
            sender_seq = self.state.next_sender_seq
            self.state.next_sender_seq += 1
        inner = self._inner(
            to=self.peer.participant,
            body=body,
            sender_seq=sender_seq,
            msg_type=msg_type,
            message_id=message_id,
        )
        padded = sign_inner(self.identity, inner)
        eph, nonce, ct = encrypt_to(
            mailbox_id=self.mailbox_id,
            message_id=message_id,
            from_id=self.participant,
            to_id=self.peer.participant,
            padded=padded,
            recipient_enc_pub=self.peer.enc_pub_raw,
        )
        return {
            "v": 2,
            "from": self.participant,
            "to": self.peer.participant,
            "message_id": message_id,
            "sent_at": inner["sent_at"],
            "eph": eph,
            "nonce": nonce,
            "ct": ct,
            "_inner": inner,
        }

    def send(self, body: str, **kwargs: Any) -> dict:
        envelope = self.compose(body, **kwargs)
        wire = {k: v for k, v in envelope.items() if not k.startswith("_")}
        return self.transport("POST", wire, token=self.send_token)

    def fetch_page(self, since: int, limit: int = 50) -> dict:
        return self.transport(
            "GET",
            None,
            token=self.fetch_token,
            since=since,
            limit=limit,
        )

    def drain(self, *, limit: int = 200) -> None:
        """Pull until caught up. Empty inbound pages advance fetch_cursor to highest_seq.

        That empty-page jump is the interpretation required for
        `fetch_cursor == highest_seq` under directional fetch (see CONTRACT_REVIEW.md).
        """
        while True:
            page = self.fetch_page(self.state.fetch_cursor, limit=limit)
            messages = page.get("messages") or []
            if not messages:
                self.state.fetch_cursor = int(page["highest_seq"])
                break
            for msg in messages:
                self.handle(msg)
            self.state.fetch_cursor = int(page["next_since"])
            if int(page["next_since"]) == int(page["highest_seq"]):
                break

    def handle(self, msg: dict, *, aad_overrides: dict | None = None) -> str:
        """Returns 'dispatched' | 'buffered' | 'quarantined' | 'duplicate' | 'replay'."""
        mid = msg["message_id"]
        if mid in self.state.seen_ids:
            return "duplicate"
        enc_privs = [self.identity.enc_priv, *self.grace_enc_privs]
        try:
            pt = try_decrypt(
                mailbox_id=self.mailbox_id,
                envelope=msg,
                enc_privs=enc_privs,
                aad_overrides=aad_overrides,
            )
        except (InvalidTag, ValueError):
            self._quarantine(msg, "undecryptable blob")
            self.state.note_seen(mid)
            return "quarantined"
        try:
            sig, inner_bytes = unpad_signed(pt)
            inner = verify_inner(self.peer, sig, inner_bytes)
        except Exception:
            self._quarantine(msg, "signature")
            self.state.note_seen(mid)
            return "quarantined"
        if (
            inner.get("from") != msg.get("from")
            or inner.get("to") != msg.get("to")
            or inner.get("message_id") != msg.get("message_id")
        ):
            self._quarantine(msg, "routing mismatch")
            self.state.note_seen(mid)
            return "quarantined"
        if inner.get("to") != self.participant:
            self._quarantine(msg, "not addressed to me")
            self.state.note_seen(mid)
            return "quarantined"
        try:
            sent = parse_rfc3339(inner["sent_at"])
        except Exception:
            self._quarantine(msg, "bad inner sent_at")
            self.state.note_seen(mid)
            return "quarantined"
        now = self._now()
        if sent - now > timedelta(hours=48):
            self._quarantine(msg, "inner sent_at too far future")
            self.state.note_seen(mid)
            return "quarantined"
        if now - sent > timedelta(days=30):
            self._quarantine(msg, "inner sent_at too old")
            self.state.note_seen(mid)
            return "quarantined"
        s = inner.get("sender_seq")
        if not isinstance(s, int) or isinstance(s, bool) or s < 1:
            self._quarantine(msg, "bad sender_seq")
            self.state.note_seen(mid)
            return "quarantined"
        self.state.note_seen(mid)
        peer = msg["from"]
        cursor = self.state.process_cursor.get(peer, 0)
        buf = self.state.buffer.setdefault(peer, {})
        if s == cursor + 1:
            self._dispatch(inner)
            self.state.process_cursor[peer] = s
            while self.state.process_cursor[peer] + 1 in buf:
                s2 = self.state.process_cursor[peer] + 1
                held = buf.pop(s2)
                self._dispatch(held["inner"])
                self.state.process_cursor[peer] = s2
            return "dispatched"
        if s > cursor + 1:
            buf[s] = {"msg": msg, "inner": inner}
            return "buffered"
        return "replay"

    def _dispatch(self, inner: dict) -> None:
        self.state.dispatched.append(inner)

    def _quarantine(self, msg: dict, reason: str) -> None:
        self.state.quarantined.append({"reason": reason, "message_id": msg.get("message_id")})
        self.state.alerts.append(reason)
