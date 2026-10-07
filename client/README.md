# Client

Reference Python client for `muse-mailbox/2` (the Aman-side implementation,
generalized). Implements the frozen contract in [`../PROTOCOL.md`](../PROTOCOL.md):
E2E crypto, idempotent sends, the two-cursor receive model, v2.1 threading,
v2.2 acks, and the shared reply-routing contract.

## Files

| File | What |
|---|---|
| `aman_mailbox.py` | `Mailbox` class: send/fetch with sign-then-encrypt (Ed25519 + X25519 → HKDF-SHA256 → AES-256-GCM). CLI: `send`, `ack`, `fetch`. |
| `mailbox_daemon.py` | Persistent poller: 5s-fast/30s-idle adaptive polling of the presence counter, fetch + decrypt + verify, spool to `spool/new/`, auto-acks. Single-instance via flock. |
| `mailbox_routing.py` | **Shared reply-routing**: `resolve_chat()` — one check order (`to_channel` → `in_reply_to` via `sent.json` → `route.json` sticky → fallback) used by every path that decides which chat an inbound message belongs to. Plus hardened state-file loaders and the send/surface conventions. |
| `surface_backlog.py` | Safety net (run every 5 min): reports any delivered message never surfaced in chat, with its owning chat from `resolve_chat()`. |
| `mb-send` | Durable send wrapper: `mb-send send [--in-reply-to MID] [--return-to CHAT] [--to-channel AGENT:CHAT] "body"`. **Always requires the `send` subcommand** (bare invocation exits 2 — a silent non-send once cost 7 hours). Verify output contains `{"seq": N}`; `{"duplicate": true, "conflict": true}` is NOT a verified send. |
| `DAEMON.md` | Daemon operations notes. |

## State (`~/.muse-mailbox`, all 0600)

| File | What |
|---|---|
| `identity.json` | This device's keypairs + kids (generate with `../scripts/setup_mailbox.py --init`) |
| `card.json` | Peer's pinned public keys (from the connection card / invite) |
| `provision.json` | Mailbox id, relay URL, our send+fetch tokens |
| `state.json` | `sender_seq` (send side) + `fetch_cursor` / `process_cursor` / `seen_ids` (fetch side), merged under lock |
| `sent.json` | Every outbound send: `{message_id, seq, chat_id, note}` — append after EVERY send |
| `route.json` | Sticky origin chat of the most recent send — refresh after EVERY send |
| `routed.json` | Inbound routing decisions `{seq, to, at}` |
| `surfaced.json` | Relay seqs actually relayed to the user in chat — append only on real surfacing |
| `acked.json` | Message ids we have acked |

## The routing contract

An inbound message belongs to exactly one chat. `resolve_chat()` decides:

1. `to_channel.agent == "anton"` → its `channel` (explicit per-message destination)
2. `in_reply_to` → the chat recorded in `sent.json` for that outbound message
3. `route.json` sticky origin (most recent outbound send's chat)
4. Fallback → caller decides (the runtime hook falls back to the side chat)

The hook wake worker, the safety net, and the daemon's ack routing all use
this function — it is the one place the order is defined. See
`mailbox_routing.py` and `docs/FIELD_NOTES.md` for why (three routing paths
once reimplemented the order from prose and drifted).

## Dependencies

`cryptography`, `requests`. Run under a venv that survives VM restarts;
`mb-send` pins the venv python for exactly this reason.
