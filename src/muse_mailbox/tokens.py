"""Directional bearer tokens."""

from __future__ import annotations

import hashlib
import os

PREFIX = {
    ("aman", "send"): "mm1_as_",
    ("aman", "fetch"): "mm1_af_",
    ("gauri", "send"): "mm1_gs_",
    ("gauri", "fetch"): "mm1_gf_",
}


def generate_token(participant: str, direction: str) -> str:
    prefix = PREFIX[(participant, direction)]
    return prefix + os.urandom(32).hex()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def extract_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
