# Substrate verdict: Durable Object + R2 (not Worker + KV)

## Recommendation

**Cloudflare Worker + Durable Object + R2.**

Do not use Worker + KV as the primary mailbox state. KV cannot prove the six contract invariants under concurrency.

The scratch harness models this mapping and the §9 matrix passes against it:

| Production | Scratch stand-in | Role |
|---|---|---|
| Worker (router, TLS) | `ThreadingHTTPServer` on localhost | HTTP |
| Durable Object (one per `mailbox_id`) | per-mailbox `threading.Lock` + SQLite `BEGIN IMMEDIATE` | seq, dedupe, auth, presence |
| R2 | `blobs` table (separate from the index) | ciphertext |
| DO storage | `mailboxes`, `messages`, `idempotency` | index |

Write order for production (see `workers/src/mailbox-do.ts`): **R2 PUT first** (key = `mailbox_id/message_id`), **then** DO transaction assigns `seq`. Never the reverse. A crash between the two leaves an orphan blob, not a seq pointing at missing bytes. Retry hits idempotency.

## Six proof points

### 1. Atomic monotonic seq — PASS (scratch)

DO input gate = one writer at a time per mailbox. Scratch: mutex + `BEGIN IMMEDIATE`, `highest_seq += 1`.

Evidence: `test_concurrent_posts_kill_restart_tiny_pages` — 100 concurrent POSTs (50 each direction), SIGKILL mid-stream, restart. Accepted seqs are exactly `{1..100}`, no duplicates, no reuse.

KV cannot do this. KV has no transaction across read-modify-write. Two Workers incrementing a counter race; `list()` is eventually consistent.

### 2. Complete ordered pagination — PASS (scratch)

`GET ?since=N&limit=7` returns every surviving inbound row with `seq > N`, ascending. `next_since` chaining plus the empty-page jump to `highest_seq` loses nothing.

Evidence: same test drains with `limit=7`; both clients dispatch 50 unique messages in contiguous `sender_seq` 1..50; `fetch_cursor == highest_seq`.

KV list + cursor can skip or duplicate keys under concurrent writes. Not acceptable for a mailbox.

### 3. Idempotent POST — PASS (scratch)

Same `message_id` + same payload hash → original `seq`, `"duplicate": true`. Different hash → `409`.

Evidence: `test_idempotent_retry_and_conflict` and the retry inside the kill/restart test.

KV: two retries in flight can both miss the cached key and insert twice.

### 4. Directional tokens — PASS (scratch)

Evidence: `test_directional_tokens` — fetch token cannot POST (`403`), send token cannot GET (`403`), lying `from`/`to` cannot escalate, Aman FETCH does not see Aman→Gauri ciphertext.

This is enforceable on any substrate. Not a KV-vs-DO differentiator, but required.

### 5. Counter-only unauthenticated stats — PASS (scratch)

Evidence: `test_presence_oracle` — `GET /stats` has no `Authorization`, body keys are exactly `{highest_seq}`, POST moves it by 1.

KV could serve a counter, but see (1): the counter itself would be racy.

### 6. Prune without seq reuse — PASS (scratch)

Evidence: `test_prune_does_not_reuse_seq` — expire the only row, `highest_seq` stays 1, next POST is seq 2.

KV key-expiry would also avoid reuse if seq were in the key, but combined with (1)–(3) it is still the wrong store.

## Why not KV (short)

Cloudflare KV is an eventually-consistent global cache. Mailbox sequencing is a **single-writer counter with durable read-your-writes**. Those are opposite jobs. Durable Objects exist specifically for this.

## Cost / ops (not a go-live decision)

- One DO per mailbox (we have exactly one mailbox for Aman↔Gauri).
- R2 class A/B ops are cheap at this volume (≪100k objects, 30-day TTL).
- Workers free/paid: well within a pair of humans sending a few hundred messages/day.

A tiny VPS running the Python harness would also satisfy the six points. It is a valid alternative if a Cloudflare account is politically the wrong operator. It is worse on TLS/DDoS/idle cost. See operator recommendation in `docs/PROVISIONING.md`.
