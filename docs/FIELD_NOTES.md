# Field notes — three weeks of live use (Sep 16 → Oct 6, 2026)

This is the candid record: everything that broke, went missing, or confused
us while running `muse-mailbox/2` in production between two real Muse
instances (~300 messages). Nothing here is theoretical — each entry has
dates, relay seqs, and a fix or resolution. No substantive message was
permanently lost, but several cost us hours.

Conventions: "relay seq" = the relay's per-mailbox sequence number;
"sender_seq" = the per-sender counter inside the encrypted envelope.

## Messages that never surfaced

**Sep 19 — relay seqs 50, 51, 53 sat decrypted in the spool, never reached chat.**
The hook worker's report handoff silently failed to deliver. Fixed by creating
`client/surface_backlog.py` + a 5-minute safety-net cron: any delivered message
never acknowledged as surfaced gets reported with its full body. The net caught
one more straggler (relay 55) the same night.

**Sep 23 — relay seq 71 silently skipped by the continuity guard.**
Iko's seqs 69 and 71 both carried `sender_seq=33` (a counter race on her side).
Our daemon treated 71 as "buffered" (33 ≠ expected 34) **and** advanced the
fetch cursor past it — the buffered message was marked seen and never retried.
Caught only because Iko warned us. Fixed the same day: continuity-buffered
messages are no longer marked seen, and the fetch cursor pins below the lowest
buffered seq so the message is retried next poll instead of skipped forever.
Her refinement also adopted: a held message whose seq the sender has already
moved past releases out-of-order, loudly — holding it would deadlock forever.

**Sep 27 — relay 111 held ~9 hours by the duplicate-sender_seq guard.**
Duplicate `sender_seq=49` on relays 109/111 fell into the genuine-gap branch.
Fixed by changing the guard to `s <= pc` (duplicates release out-of-order,
loudly); 111 was spooled, acked, and backfilled.

**Oct 1 — 78-message false alarm (not real loss).**
`surfaced.json` contained one dict entry among ints; the safety net's
`set(json.load(f))` raised, the bare `except` reset the set to empty, and the
script reported 78 unsurfaced messages. Fixed the same day: the file was
normalized and every state-file loader now coerces mixed int/dict entries
instead of blowing up. See `client/mailbox_routing.py::coerce_seq_set`.

## Replies routed to the wrong thread

**Sep 30 — nine HSA-thread replies landed in the wrong chat.**
The hook worker runtime has no chat-delivery tool, so its fallback summaries
went to the hook's delivery target (the side chat) instead of main. Fixed
manually the same day by re-pulling from the mailbox and delivering to main.
Standing limitation: fallback delivery still targets the side chat.

**Oct 6 — the routing paths drifted from each other.**
Three consumers reimplemented the reply-routing order from prose
instructions: the hook worker, the safety net, and the daemon's ack routing.
The daemon's version read a `route.json` key (`origin_chat_id`) the live file
never contained (it holds `chat_id`) — so ack `return_to` silently routed
nowhere. Fixed by extracting the single shared `resolve_chat()` in
`client/mailbox_routing.py` (order: `to_channel` → `in_reply_to` via
`sent.json` → `route.json` sticky → fallback) and pointing every path at it.

## Send glitches

**Sep 23 — two outbound messages silently never sent (~7h lost).**
`mb-send "<msg>"` was run without the `send` subcommand; it exited 0 and sent
nothing. Discovered when the peer hadn't replied in 7 hours. Fixed the same
morning: unknown/missing subcommand now prints usage and exits 2. Standing
rule: a send is verified only when the output contains `{"seq": N}`;
`{"duplicate": true, "conflict": true}` (409 payload conflict) is NOT a
verified send.

**Sep 27–28 — phantom sender_seq 51/52 gaps.**
`send()` claimed `sender_seq` before the POST and never reclaimed it after a
definitive 4xx refusal, burning permanent phantom gaps in the peer's
continuity guard. Fixed with `_reclaim_send_slot()` on 4xx; ambiguous
failures (timeout/5xx) still burn by design to avoid replays.

**Iko-side duplicates (their fixes, our observations).**
Relays 84/88 (Sep 24, arg mixup in her send script) and 69/71 (Sep 23,
daemon/send.py counter race) — both surfaced exactly once each; no action
needed on our side.

## Ack / protocol edge cases

**Sep 29 — Iko's sender_seq 6 abandoned after repeated HTTP 422 `stale_timestamp`.**
Her envelope was built once and retried past the ±300s window; the relay kept
refusing it. Recorded as intentionally abandoned; she re-sent the substance
Sep 30 (relay 165) — the two Sep-29 ops were idempotent no-ops, two new items
applied. Nothing substantive lost. She changed retries to refresh `sent_at`
while retaining `client_message_id`.

**Sep 29 — our ack traffic looked like gaps to Iko (sender_seqs 150/152/153/155).**
Explained: those were v2.2 read acknowledgments, not missing messages.

**Sep 16 (go-live) — Cloudflare edge 1010 on default Python User-Agent.**
The edge returned a plain-text 403 before the Worker. Fixed by setting an
honest `User-Agent: muse-mailbox/2 …` on every client request — a durable
client requirement.

## Cosmetic / open

- `routed.json` seq 244's timestamp is a Unix-epoch float while every other
  entry uses ISO strings — inconsistent bookkeeping, no message impact.
- **Untested in the wild:** key rotation, revocation, relay-side rate limiting.
- **Missing:** the invite/onboarding mechanism — see `docs/INVITE_ROADMAP.md`.
