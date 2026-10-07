#!/usr/bin/env python3
"""Safety net for the Iko<->Aman mailbox delivery path.

The muse-mailbox-watch hook worker is the fast path (seconds), but its
report handoff has silently failed at least twice
(2026-09-19: relay seq 50, 51, 53 all reached spool/delivered/ without ever
surfacing in chat). This script is run on a 5-minute cron: it lists
delivered messages no chat agent has yet acknowledged as surfaced,
printing their full bodies so the cron handoff carries them.

Ack convention: whenever an Iko message is relayed to the user in chat
(via either path), the relaying agent appends the relay seq to
~/.muse-mailbox/surfaced.json. This script never marks messages surfaced
itself — only a real surfacing does.
"""
import glob
import json
import os
import sys

CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CLIENT_DIR)
from mailbox_routing import coerce_seq_set, load_list, resolve_chat  # noqa: E402

HOME = os.path.expanduser("~/.muse-mailbox")
DELIVERED = os.path.join(CLIENT_DIR, "spool", "delivered")
SURFACED = os.path.join(HOME, "surfaced.json")


def main():
    surfaced = coerce_seq_set(load_list(SURFACED))

    pending = []
    for path in sorted(glob.glob(os.path.join(DELIVERED, "*.json"))):
        try:
            seq = int(os.path.splitext(os.path.basename(path))[0])
        except ValueError:
            continue
        if seq in surfaced:
            continue
        try:
            with open(path) as f:
                d = json.load(f)
        except Exception:
            continue
        # v2.2 acks are protocol signals, not user messages — never surface.
        if (d.get("type") or "message") == "ack":
            continue
        pending.append((seq, d))

    if not pending:
        print("ok: no unsurfaced mailbox messages")
        return

    print("UNSURFACED MAILBOX MESSAGES (%d) — relay these to Aman now, then "
          "append their seqs to %s" % (len(pending), SURFACED))
    for seq, d in pending:
        chat_id, reason = resolve_chat(d)
        print("=" * 60)
        print("relay_seq=%d sender=%s sent_at=%s type=%s" % (
            seq, d.get("sender"), d.get("sent_at"), d.get("type")))
        print("owning_chat=%s (via %s)" % (chat_id or "FALLBACK", reason))
        print("-" * 60)
        print(d.get("body") or "(no body)")
    print("=" * 60)


if __name__ == "__main__":
    main()
