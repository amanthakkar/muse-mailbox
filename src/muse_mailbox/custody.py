"""File-based key custody (Hatch has no worker-readable vault KV).

Layout of $MUSE_MAILBOX_HOME (directory mode 0700):

  identity.json   0600   Ed25519 + X25519 seeds. Worker only.
  card.json       0600   This Muse's connection card (own tokens + both pubs).
  state.json      0600   fetch_cursor / process_cursor / seen_ids.
  last_seq        0644   presence counter. The ONLY file the secretless hook opens.

The hook script must never mention identity.json, card.json, or state.json —
not even to test they exist. Hook-script integrity is the security boundary.
Any worker in the runtime can read 0600 files on the VM disk; that caveat is accepted.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .card import verify_card_fingerprints
from .client import MailboxClient, ReceiveState
from .crypto import Identity, PeerPublic

DIR_MODE = 0o700
SECRET_MODE = 0o600
HOOK_MODE = 0o644
HOOK_FILENAME = "last_seq"
SECRET_FILENAMES = frozenset({"identity.json", "card.json", "state.json"})


class CustodyError(Exception):
    pass


@dataclass
class Home:
    path: Path
    identity: Identity
    card: dict
    state: ReceiveState

    @property
    def send_token(self) -> str:
        return self.card["credentials"]["send"]

    @property
    def fetch_token(self) -> str:
        return self.card["credentials"]["fetch"]

    @property
    def mailbox_id(self) -> str:
        return self.card["mailbox_id"]

    @property
    def peer(self) -> PeerPublic:
        peer_id = self.card["peer"]
        return PeerPublic.from_card_entry(peer_id, self.card["keys"][peer_id])


def _chmod(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def init_home(path: Path, identity: Identity, card: dict) -> Home:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _chmod(path, DIR_MODE)
    ident_path = path / "identity.json"
    card_path = path / "card.json"
    state_path = path / "state.json"
    last_seq = path / HOOK_FILENAME
    ident_path.write_text(
        json.dumps(identity.private_dict(), indent=2) + "\n", encoding="utf-8"
    )
    _chmod(ident_path, SECRET_MODE)
    card_path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    _chmod(card_path, SECRET_MODE)
    state_path.write_text("{}\n", encoding="utf-8")
    _chmod(state_path, SECRET_MODE)
    last_seq.write_text("0\n", encoding="utf-8")
    _chmod(last_seq, HOOK_MODE)
    return load_home(path)


def load_home(path: Path) -> Home:
    path = Path(path)
    ident = Identity.from_private_dict(
        json.loads((path / "identity.json").read_text(encoding="utf-8"))
    )
    card = json.loads((path / "card.json").read_text(encoding="utf-8"))
    if ident.participant != card["owner"]:
        raise CustodyError("identity participant does not match card owner")
    verify_card_fingerprints(card)
    raw_state = json.loads((path / "state.json").read_text(encoding="utf-8") or "{}")
    state = ReceiveState(
        fetch_cursor=int(raw_state.get("fetch_cursor") or 0),
        process_cursor={
            k: int(v) for k, v in (raw_state.get("process_cursor") or {}).items()
        },
        next_sender_seq=int(raw_state.get("next_sender_seq") or 1),
    )
    return Home(path=path, identity=ident, card=card, state=state)


def save_state(home: Home) -> None:
    payload = {
        "fetch_cursor": home.state.fetch_cursor,
        "process_cursor": home.state.process_cursor,
        "next_sender_seq": home.state.next_sender_seq,
    }
    dest = home.path / "state.json"
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _chmod(dest, SECRET_MODE)


def write_last_seq(home: Home, highest_seq: int) -> None:
    dest = home.path / HOOK_FILENAME
    dest.write_text(f"{int(highest_seq)}\n", encoding="utf-8")
    _chmod(dest, HOOK_MODE)


def read_last_seq(path: Path) -> int:
    """Hook-safe: only opens last_seq."""
    text = (Path(path) / HOOK_FILENAME).read_text(encoding="utf-8").strip() or "0"
    return int(text)


def hook_script_is_safe(script: str) -> bool:
    """True iff the hook source never names a secret file."""
    lowered = script.lower()
    return not any(name in lowered for name in SECRET_FILENAMES)


def client_from_home(home: Home, transport) -> MailboxClient:
    return MailboxClient(
        participant=home.identity.participant,
        mailbox_id=home.mailbox_id,
        identity=home.identity,
        peer=home.peer,
        send_token=home.send_token,
        fetch_token=home.fetch_token,
        transport=transport,
        state=home.state,
    )
