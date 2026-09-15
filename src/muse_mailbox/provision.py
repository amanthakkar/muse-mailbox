"""Scratch-only mailbox provisioning. Never used for production keys/tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .card import make_card
from .crypto import Identity
from .store import Store
from .tokens import generate_token, token_hash


def new_mailbox_id() -> str:
    return "mb_" + os.urandom(16).hex()


def provision_dev(
    store: Store, *, relay_url: str = "http://127.0.0.1:8787"
) -> dict:
    mailbox_id = new_mailbox_id()
    store.create_mailbox(mailbox_id)
    tokens = {}
    for participant in ("aman", "gauri"):
        for direction in ("send", "fetch"):
            tok = generate_token(participant, direction)
            tokens[f"{participant}_{direction}"] = tok
            store.add_token(token_hash(tok), mailbox_id, participant, direction)
    identities = {
        "aman": Identity.generate("aman"),
        "gauri": Identity.generate("gauri"),
    }
    cards = {
        "aman": make_card(
            owner="aman",
            peer="gauri",
            relay_url=relay_url,
            mailbox_id=mailbox_id,
            send_token=tokens["aman_send"],
            fetch_token=tokens["aman_fetch"],
            identities=identities,
        ),
        "gauri": make_card(
            owner="gauri",
            peer="aman",
            relay_url=relay_url,
            mailbox_id=mailbox_id,
            send_token=tokens["gauri_send"],
            fetch_token=tokens["gauri_fetch"],
            identities=identities,
        ),
    }
    return {
        "mailbox_id": mailbox_id,
        "tokens": tokens,
        "identities": identities,
        "cards": cards,
    }


def write_scratch_secrets(bundle: dict, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "README").write_text(
        "SCRATCH ONLY. Destroy after tests. Never production material.\n",
        encoding="utf-8",
    )
    (dest / "cards.json").write_text(
        json.dumps(bundle["cards"], indent=2), encoding="utf-8"
    )
    os.chmod(dest / "cards.json", 0o600)
