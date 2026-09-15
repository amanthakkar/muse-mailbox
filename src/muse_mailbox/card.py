"""Connection-card helpers (PROTOCOL.md §7). Never embed private keys."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .crypto import Identity, PeerPublic, fingerprint, fingerprint_prefix


def utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_card(
    *,
    owner: str,
    peer: str,
    relay_url: str,
    mailbox_id: str,
    send_token: str,
    fetch_token: str,
    identities: dict[str, Identity],
) -> dict[str, Any]:
    keys = {name: ident.public_dict(mailbox_id) for name, ident in identities.items()}
    return {
        "protocol": "muse-mailbox/2",
        "card_id": str(uuid.uuid4()),
        "issued_at": utc_now_z(),
        "relay_url": relay_url,
        "mailbox_id": mailbox_id,
        "owner": owner,
        "peer": peer,
        "credentials": {"send": send_token, "fetch": fetch_token},
        "keys": keys,
    }


def verify_card_fingerprints(card: dict[str, Any]) -> dict[str, str]:
    """Recompute fingerprints from key bytes. Raises on mismatch."""
    mailbox_id = card["mailbox_id"]
    out = {}
    for participant, entry in card["keys"].items():
        peer = PeerPublic.from_card_entry(participant, entry)
        expected = fingerprint(
            mailbox_id, participant, peer.enc_pub_raw, peer.sign_pub_raw
        )
        if expected != entry["fingerprint"]:
            raise ValueError(f"fingerprint mismatch for {participant}")
        out[participant] = expected
    return out


def spoken_prefix(card: dict[str, Any], participant: str) -> str:
    fps = verify_card_fingerprints(card)
    return fingerprint_prefix(fps[participant], 8)
