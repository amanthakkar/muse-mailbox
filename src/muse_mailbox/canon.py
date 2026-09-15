"""Canonical JSON and AAD helpers (PROTOCOL.md §3)."""

from __future__ import annotations

import json
from typing import Any, Mapping


def canonical_json_bytes(obj: Mapping[str, Any]) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def aad_bytes(
    *,
    v: int,
    mailbox_id: str,
    message_id: str,
    from_id: str,
    to_id: str,
    eph_b64: str,
) -> bytes:
    """AAD is canonical JSON of the six routing fields. Key order is sorted."""
    return canonical_json_bytes(
        {
            "eph": eph_b64,
            "from": from_id,
            "mailbox_id": mailbox_id,
            "message_id": message_id,
            "to": to_id,
            "v": v,
        }
    )


def payload_hash_bytes(
    *,
    v: int,
    from_id: str,
    to_id: str,
    message_id: str,
    eph: str,
    nonce: str,
    ct: str,
) -> bytes:
    """Idempotency hash. sent_at is excluded so a retry may refresh freshness."""
    import hashlib

    return hashlib.sha256(
        canonical_json_bytes(
            {
                "ct": ct,
                "eph": eph,
                "from": from_id,
                "message_id": message_id,
                "nonce": nonce,
                "to": to_id,
                "v": v,
            }
        )
    ).digest()
