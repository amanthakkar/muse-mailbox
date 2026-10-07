#!/usr/bin/env python3
"""Shared reply-routing for the muse-mailbox/2 Aman-side client.

Every path that decides WHICH CHAT an inbound Iko message belongs to must
use `resolve_chat()` here — the hook wake worker, the surface safety net,
and the daemon's ack routing. Previously each path reimplemented the
check order from prose instructions, and they drifted (2026-10-06: the
daemon's `route_origin_chat()` read a key `origin_chat_id` that the live
`route.json` never contained — it holds `chat_id` — so ack return_to
silently routed nowhere).

Check order (first match wins):
  1. `to_channel` on the inbound message: {"agent": "anton",
     "channel": <chat_id>} -> that chat. This is the explicit per-message
     destination, copied from the answered message's return_to (v2.1).
  2. `in_reply_to`: look up the outbound message we sent with that
     message_id in sent.json; route to the chat_id recorded for it.
  3. `route.json` sticky origin: the chat_id of the most recent outbound
     send. Accepts both the canonical `origin_chat_id` key and the legacy
     `chat_id` key (written by hand before this module existed).
  4. Fallback: (None, "fallback") — the caller decides (the runtime hook
     falls back to the originating side chat).

Conventions (enforced by the helpers here):
  - After EVERY outbound send, call `record_route(seq, chat_id, note)`.
  - After an inbound message is actually relayed to the user in chat, call
    `append_surfaced(seq)`. Never mark surfaced without a real relay.
  - Acks (type:ack) are protocol signals: never surface them.

All state files live in ~/.muse-mailbox (0600). Every loader tolerates a
missing file, invalid JSON, and mixed int/dict entries — a chat agent once
appended a dict to surfaced.json and broke set construction downstream;
that class of corruption must never take down a path again.
"""
import json
import os
import time

HOME = os.path.expanduser("~/.muse-mailbox")
SENT_PATH = os.path.join(HOME, "sent.json")
ROUTED_PATH = os.path.join(HOME, "routed.json")
SURFACED_PATH = os.path.join(HOME, "surfaced.json")
ROUTE_PATH = os.path.join(HOME, "route.json")


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_list(path):
    """A JSON list, or [] — never raises."""
    d = _read_json(path, [])
    return d if isinstance(d, list) else []


def coerce_seq_set(raw):
    """surfaced.json/acked.json are lists of ints, but agents have appended
    richer dict entries {"seq": N, ...}. Coerce those instead of blowing up:
    a failed set() here would silently treat everything as unsurfaced (or
    unacked) and spam the user with ancient messages. (2026-10-01: that
    exact corruption caused a 78-message false alarm in the safety net.)"""
    out = set()
    for x in raw if isinstance(raw, list) else []:
        if isinstance(x, int):
            out.add(x)
        elif isinstance(x, dict) and isinstance(x.get("seq"), int):
            out.add(x["seq"])
        elif isinstance(x, str) and x.isdigit():
            out.add(int(x))
    return out


def sent_lookup():
    """message_id -> sent entry (has chat_id, seq, note)."""
    idx = {}
    for s in load_list(SENT_PATH):
        if isinstance(s, dict) and s.get("message_id"):
            idx[s["message_id"]] = s
    return idx


def sticky_origin_chat():
    """Most recent outbound send's chat. Tolerates both key shapes."""
    d = _read_json(ROUTE_PATH, {})
    if not isinstance(d, dict):
        return None
    return d.get("origin_chat_id") or d.get("chat_id")


def resolve_chat(payload):
    """Return (chat_id_or_None, reason) for an inbound message payload.

    payload is the spool/delivered dict: keys relay_seq, message_id,
    in_reply_to, return_to, to_channel, type, ...
    """
    to_ch = payload.get("to_channel") or {}
    if isinstance(to_ch, dict) and to_ch.get("agent") == "anton" \
            and to_ch.get("channel"):
        return to_ch["channel"], "to_channel"
    in_reply_to = payload.get("in_reply_to")
    if in_reply_to:
        orig = sent_lookup().get(in_reply_to)
        if orig and orig.get("chat_id"):
            return orig["chat_id"], "in_reply_to"
    sticky = sticky_origin_chat()
    if sticky:
        return sticky, "route_sticky"
    return None, "fallback"


def record_route(chat_id, note="", seq=None):
    """Refresh the sticky origin chat after an outbound send."""
    entry = {"origin_chat_id": chat_id, "chat_id": chat_id,
             "ts": int(time.time()), "note": note}
    if seq is not None:
        entry["seq"] = seq
    _write_json_atomic(ROUTE_PATH, entry)
    if seq is not None:
        routed = load_list(ROUTED_PATH)
        routed.append({"seq": seq, "to": chat_id,
                       "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        _write_json_atomic(ROUTED_PATH, routed[-500:])


def append_surfaced(seq):
    """Record that relay seq was really relayed to the user in chat."""
    surfaced = load_list(SURFACED_PATH)
    seqs = coerce_seq_set(surfaced)
    if seq not in seqs:
        surfaced.append(seq)
        _write_json_atomic(SURFACED_PATH, surfaced)
