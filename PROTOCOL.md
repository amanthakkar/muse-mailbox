# muse-mailbox/2 — Frozen Protocol Contract

**Status:** FROZEN for implementation (2026-09-15). This document is the single authoritative
spec. It supersedes the v1 shared-passphrase draft and the investigation's draft language on
AAD and cursor semantics. Rationale and rejected alternatives live in
`INVESTIGATION.md` and `parts/`; where this contract and those drafts disagree, **this
contract wins**.

**Handoff gates (both must be green before Cursor implements):**
1. Gauri's runtime verification passes — see `GAURI_RUNTIME_VERIFICATION.md`
   (5-second secretless hook → worker wake, proven on her actual Muse instance).
2. This contract reviewed and approved by Aman.

---

## 1. Parties, identifiers, trust model

- Exactly two participants per mailbox: `"aman"` and `"gauri"`. No broadcast, no third parties.
- Each participant's Muse is an **outbound-HTTPS-only client** of a neutral relay. Neither
  depends on the other's infrastructure (no Pi, no tunnels, no shared credentials).
- The relay is **untrusted/blind**: it stores ciphertext + plaintext routing metadata and
  enforces authorization, sequencing, and retention. It never sees plaintext content or
  private keys.
- Root of trust: an **in-person connection-card exchange** with a fingerprint ceremony (§7).
  No PKI.
- Accepted residual exposures (conscious, not oversights): relay-visible metadata (timing,
  size buckets, counts, direction, presence-oracle counter movement); no forward secrecy
  (mitigated by delete-after-fetch, ~7-day TTL, monthly encryption-key rollovers).

## 2. Wire format

### 2.1 POST — submit

`POST /v1/mailboxes/{mailbox_id}/messages`, `Authorization: Bearer <send-token>`,
`Content-Type: application/json`. Body ≤ 256 KiB.

```json
{
  "v": 2,
  "from": "aman",
  "to": "gauri",
  "message_id": "b3f1c2d4-…-uuid-v4",
  "sent_at": "2026-09-15T16:27:00Z",
  "eph": "<base64: 32-byte ephemeral X25519 public key>",
  "nonce": "<base64: 12-byte AES-GCM nonce>",
  "ct": "<base64: AES-256-GCM ciphertext of the padded sig ‖ inner payload>"
}
```

- `message_id`: UUID v4, the **idempotency key** (called `client_message_id` in the
  investigation drafts — same field).
- `sent_at`: RFC3339 UTC. Relay rejects `|relay_now − sent_at| > 300 s` with `422`.
- Decoded `ct` ≤ 200 KiB; `eph`/`nonce`/`ct` must be valid base64.

**Relay behavior (in order):** validate token (must be a send token bound to this mailbox;
`from` must equal the token's participant; `to` must equal the counterparty) → idempotency
check on `message_id` (same id + identical payload hash → return original `seq` with
`"duplicate": true`; same id + different payload hash → `409`) → size/freshness checks →
**atomically** assign the next per-mailbox `seq` (monotonic, never reused; gaps permitted)
→ store `(seq, from, to, message_id, payload_hash, sent_at, stored_at, eph, nonce, ct)` →
return `200 {"seq", "message_id", "duplicate", "accepted_at"}`.

### 2.2 GET — fetch

`GET /v1/mailboxes/{mailbox_id}/messages?since=N&limit=M`,
`Authorization: Bearer <fetch-token>`. `since` defaults 0, `limit` defaults 50, max 200.

Relay returns rows with `to == <token's participant>` **and** `seq > since`, ordered by
`seq` ascending, truncated to `limit` rows and ≤ 1 MiB total:

```json
{
  "mailbox_id": "mb_…",
  "highest_seq": 1234,
  "messages": [
    {"seq": 1201, "v": 2, "from": "gauri", "to": "aman",
     "message_id": "…", "sent_at": "…", "stored_at": "…",
     "eph": "<base64>", "nonce": "<base64>", "ct": "<base64>"}
  ],
  "next_since": 1217
}
```

- `next_since` = highest `seq` in this page (or the request's `since` if empty). The client
  resumes with `since = next_since`; `next_since == highest_seq` means caught up.
  **The `since`/`next_since` cursor is the acknowledgment mechanism.** No ack operation exists.
- `highest_seq` counts all messages in the mailbox (both directions) — count-only metadata.

### 2.3 Presence oracle (for the secretless 5-second hook)

`GET /v1/mailboxes/{mailbox_id}/stats` → `200 {"highest_seq": N}`. **Unauthenticated.**
Mailbox IDs are ≥128-bit random and unguessable; the counter alone leaks only activity
timing, which is accepted metadata. The hook polls this; it never touches credentials.

### 2.4 Error codes

JSON `{ "error": "<code>", "detail": "…" }`:
`400 invalid_request` · `401 invalid_token` · `403 forbidden` (wrong direction for the
operation, or `from`/`to` violating token binding) · `404 mailbox_not_found` (deliberately
ambiguous: nonexistent mailbox **or** token/mailbox mismatch) ·
`409 idempotency_conflict` · `413 payload_too_large` · `422 stale_timestamp` ·
`429 rate_limited` (with `Retry-After`; suggested: 60 submits/min per send token,
300 fetches/min per fetch token) · `500 internal_error` (safe to retry both operations).

## 3. Cryptographic construction (exact)

Per participant: one long-term **Ed25519** signing keypair (identity — rotate rarely) and one
long-term **X25519** encryption keypair (rotate periodically). Separate key material; never
reuse one keypair across DH and signing. Private keys are generated on-device, mode `0600`
(or platform secret store), never logged, never transmitted.

### 3.1 Per-message encryption (ECIES-style)

1. Generate a fresh ephemeral X25519 keypair; `eph` = its public key (32 B).
2. `shared = X25519(ephemeral_priv, recipient_enc_pub)`.
3. `key = HKDF-SHA256(salt = eph_pub_bytes, ikm = shared, info = b"muse-mailbox/v2/ecies", L = 32)`.
4. `nonce = secrets.token_bytes(12)` (fresh per message; reuse is structurally harmless
   because the key is fresh per message).
5. `aad` = UTF-8 bytes of canonical JSON of the routing header —
   `{"eph": <base64 eph>, "from": <str>, "mailbox_id": <str>, "message_id": <uuid str>,
   "to": <str>, "v": 2}` with `sort_keys=True, separators=(",",":"), ensure_ascii=True`.
6. `ct = AES-256-GCM(key, nonce, aad, padded_payload)`.

The sender computes AAD over the submitted values. The receiver recomputes it from its
configured `mailbox_id` plus the received `v/from/to/message_id/eph`. **Any tampering with
any of the six AAD fields by the relay (or anyone) causes `InvalidTag` on decrypt.**
This is the fix for the unauthenticated-routing-metadata hole: version, mailbox, message
identity, direction, and ephemeral key are all cryptographically bound.

### 3.2 Inner payload (signed, then encrypted, then padded)

```json
inner = {"body": "…", "from": "aman", "from_sign_kid": "…", "to": "gauri",
         "to_enc_kid": "…", "message_id": "…", "sender_seq": 42,
         "sent_at": "…", "type": "message", "v": 2}
```

- `sender_seq`: per-sender monotonic counter starting at 1, **independent** of the relay's
  per-mailbox `seq`. Every sent message — including heartbeats and control messages —
  consumes one `sender_seq`, keeping the per-sender stream contiguous.
- `type` ∈ `{"message", "key-rollover", "revoke", "heartbeat"}`. Key IDs are the first
  8 bytes of `SHA256(public_key)`, hex. No floats.
- Canonical bytes: `json.dumps(inner, sort_keys=True, separators=(",",":"),
  ensure_ascii=True).encode("utf-8")` — byte-exact on both sides.
- `sig = Ed25519_sign(sender_sign_priv, canonical_bytes)` (64 B).
- `payload = sig ‖ canonical_bytes`; prefix 4-byte big-endian length; zero-pad to the
  smallest bucket in `[1024, 4096, 16384]`; reject bodies exceeding the top bucket.
- Routing fields are duplicated inside the signed payload (defense in depth: end-to-end
  authenticity binding independent of the transport AAD).

### 3.3 Key rotation / replacement / revocation

- **Encryption keys** (X25519): rotate via in-band message of `type: "key-rollover"`,
  signed by the long-term Ed25519 identity key, carrying the new public keys; peer keeps
  the old decryption key for a 7-day grace window. Monthly rotation is the default cadence.
- **Signing keys** (Ed25519): rotate rarely; new key announced signed by the old key, with
  human fingerprint confirmation at next in-person contact.
- **Suspected compromise of a signing key**: never auto-accept an in-band rollover —
  require a fresh in-person card ceremony (§7).
- **Revocation** = human-mediated pin replacement (+ relay token rotation). No PKI.

## 4. Authorization — four directional tokens

| Token | On | Grants |
|---|---|---|
| `AMAN_SEND` | POST | submit `from: "aman"`, `to: "gauri"` only |
| `AMAN_FETCH` | GET | read rows with `to: "aman"` only |
| `GAURI_SEND` | POST | submit `from: "gauri"`, `to: "aman"` only |
| `GAURI_FETCH` | GET | read rows with `to: "gauri"` only |

Tokens are opaque 256-bit values (`mm1_` prefix). The relay checks the token's
`(participant, direction)` binding **against the envelope on every request** — the token,
not the envelope, is the authority, so lying in the envelope cannot escalate. Blast
radius: stolen fetch → retained inbound ciphertext only (content still safe: 256-bit
keys); stolen send → spam/queue-poisoning only (forgeries fail signature verification);
neither yields the other direction; all four → ciphertext + metadata, never content.
Token rotation is relay-side and atomic, independent of message keys. Per-token rate
limits bound abuse.

## 5. Retention

Messages are kept **30 days** from `stored_at`, then pruned; mailbox additionally capped at
**100,000** messages (oldest pruned first). Pruned messages are unrecoverable; pruned
sequence numbers are never reassigned. Clients must fetch promptly. (The investigation
drafts also describe delete-after-fetch + ~7-day TTL as the forward-secrecy substitute —
the relay SHOULD additionally delete a message once the recipient's `fetch_cursor` has
durably passed it; the 30-day TTL is the backstop.)

## 6. Client receive algorithm (two-cursor model — normative)

### 6.1 Durable state

Per mailbox: `fetch_cursor: int` (init 0) — highest relay `seq` *received*.
Per peer: `process_cursor: int` (init 0) — highest **contiguous** `sender_seq` verified,
decrypted, and dispatched; `buffer: map[sender_seq → message]` (bounded: 1000 entries or
7 days, whichever comes first); `seen_ids: set[message_id]` (bounded: last 10,000).

### 6.2 Poll cycle

```
on poll (5-second presence hook saw highest_seq move, or scheduled catch-up):
  loop:
    page = GET /messages?since=fetch_cursor&limit=200
    if page.messages is empty: break
    for msg in page.messages (already seq-ascending):
      handle(msg)
    fetch_cursor = page.next_since          # durable write; never moves backward
    if page.next_since == page.highest_seq: break
```

`handle(msg)`:
```
1. if msg.message_id in seen_ids: return                      # duplicate delivery
2. aad = canonical_json({v, mailbox_id, message_id, from, to, eph})   # §3.1
   pt = None
   for k in [active_enc_priv, *grace_enc_privs]:
     try: pt = AESGCM_decrypt(k, msg.nonce, aad, msg.ct); break
     except InvalidTag: continue
   if pt is None: quarantine(msg); alert("undecryptable blob"); seen_ids.add(id); return
      # fetch_cursor still advances past it (§6.2 loop): one bad blob never stalls the stream
3. strip length prefix + zero padding → split sig (64 B) ‖ inner_bytes
   inner = json.loads(inner_bytes)
   Ed25519_verify(pinned_peer_sign_pub, sig, canonical(inner_bytes))  # fail → quarantine + alert
4. require inner.from == msg.from, inner.to == msg.to,
           inner.message_id == msg.message_id                      # cross-check layers
   require inner.to == my_participant_id
   require skew bounds on inner.sent_at (reject >48h future, >30d past)
   require inner.sender_seq >= 1 integer
5. seen_ids.add(msg.message_id)
6. peer = msg.from
   s = inner.sender_seq
   if s == process_cursor[peer] + 1:
       dispatch(inner); process_cursor[peer] = s
       while process_cursor[peer] + 1 in buffer:                   # drain newly-contiguous
           s2 = process_cursor[peer] + 1
           dispatch(buffer.pop(s2)); process_cursor[peer] = s2
   elif s > process_cursor[peer] + 1:
       buffer[s] = (msg, inner)                                    # out-of-order: hold, never skip
   else:
       log("duplicate/replay sender_seq", s)                       # s <= cursor: drop
```

### 6.3 Gap policy (alerts, never auto-skip)

- A peer-`sender_seq` gap held in `buffer` for **> 24h**, or any gap spanning **> 50**
  missing sequence numbers, → `gap_alert` (possible targeted suppression; investigate, do
  not skip).
- No verified message of **any** type from the peer for **> 48h** → `silence_alert`.
  (Each direction sends a `heartbeat` message at least daily when otherwise idle, so the
  per-sender stream stays contiguous and suppression stays detectable.)
- `fetch_cursor` gaps (relay-`seq`) are **normal** — pruning and interleaved directions —
  and never trigger alerts or stall processing.

### 6.4 Why not `seq > last_seq`

A single high-water mark over the relay's per-mailbox `seq` is rejected. Delivery can be
out of order (concurrent POSTs, relay restart, paginated fetch): receiving relay seqs for
sender messages 10, 12, 11 and advancing a mark past 12 permanently strands 11. The
two-cursor model separates *receipt* (`fetch_cursor` over relay seq) from *processing*
(`process_cursor` over per-sender `sender_seq`), and the processing cursor advances only
through contiguously verified messages.

## 7. Connection card

Per-recipient card, exchanged **in person**. Carries only the owner's own token pair —
never the peer's credentials, never any private key.

```json
{
  "protocol": "muse-mailbox/2",
  "card_id": "uuid-v4",
  "issued_at": "2026-09-15T16:30:00Z",
  "relay_url": "https://relay.example.org",
  "mailbox_id": "mb_… (≥128-bit random)",
  "owner": "gauri",
  "peer": "aman",
  "credentials": {"send": "mm1_gs_…", "fetch": "mm1_gf_…"},
  "keys": {
    "aman":  {"encryption_public_key": "<base64 raw 32B X25519>",
              "signing_public_key": "<base64 raw 32B Ed25519>",
              "fingerprint": "8A3F 1C9D …"},
    "gauri": {"encryption_public_key": "…",
              "signing_public_key": "…",
              "fingerprint": "44B2 90EE …"}
  }
}
```

Fingerprint: `SHA-256(ASCII("muse-mailbox/2") ‖ 0x00 ‖ ASCII(mailbox_id) ‖ 0x00 ‖
ASCII(participant_id) ‖ 0x00 ‖ raw_enc_pub_32B ‖ 0x00 ‖ raw_sig_pub_32B)`, displayed as
16 groups of 4 uppercase hex. Ceremony: receiver recomputes fingerprints from the card's
key bytes (catches corruption); the giver reads the **first 8 groups (32 hex chars)**
aloud over the in-person channel and the receiver compares (catches whole-card
substitution). Then credential sanity checks and an optional non-destructive liveness
probe (`GET …/messages?since=<huge>&limit=1` expecting empty `200` — never send a test
message). Emergency re-keying (suspected signing-key compromise) always uses a fresh
in-person card — never an in-band message.

## 8. Relay implementation guidance

The relay is Muse-agnostic: `POST` encrypted blob, `GET` encrypted blobs, `GET` counter.
If Meta later ships real event subscriptions, the 5-second presence hook is replaced
without touching this protocol.

**Recommended for Cursor to evaluate:** Cloudflare **Worker + Durable Object + R2**
(Durable Object as the authoritative per-mailbox coordinator for atomic `seq` assignment,
idempotency dedupe, and presence state; R2 for durable ciphertext) **versus**
Worker + KV. The choice is Cursor's — but whichever substrate is used, these invariants
are **mandatory and must be proven by the regression tests in §9**:

1. `seq` assignment is atomic per mailbox: strictly increasing, never reused.
2. `GET …?since=N` returns **every** surviving row with `seq > N`, ascending, complete
   across pages (`next_since` chaining loses nothing).
3. Idempotent POST: same `message_id` + identical payload hash → original `seq`,
   `"duplicate": true`; same `message_id` + different payload hash → `409`. Safe to retry.
4. Directional token binding enforced against the envelope on every request (§4).
5. `GET …/stats` is unauthenticated and returns only `{"highest_seq": N}`.
6. Retention pruning (30 days / 100k cap) never reassigns sequence numbers.

## 9. Mandatory regression tests (must pass before handoff is accepted)

1. **Concurrency + restart ordering:** 2 × 50 concurrent POSTs from both participants with
   random delays; kill the relay mid-stream and restart; client fetches with `limit=7`
   from `since=0`. Assert: every accepted message dispatched **exactly once**; per-peer
   `process_cursor` advanced contiguously `1..M` with no gaps; no `message_id` dispatched
   twice; `fetch_cursor == highest_seq` at the end; duplicate retries returned
   `"duplicate": true` with the original `seq`; conflicting `message_id` reuse → `409`.
2. **Out-of-order delivery:** deliver sender messages with `sender_seq` 10, 12, 11.
   Assert 11 is buffered then dispatched after 10, `process_cursor` ends at 12, and nothing
   was skipped or double-dispatched.
3. **AAD tamper matrix:** flip each of `v`, `mailbox_id`, `message_id`, `from`, `to`,
   `eph`, and one bit of `ct`, one at a time. Assert every tampered blob fails decrypt
   (`InvalidTag`) — the relay cannot rewrite routing undetected.
4. **Signature tamper:** valid `ct` with inner `from`/`to`/`sender_seq`/`body` modified and
   re-encrypted by an attacker holding no signing key. Assert signature verification fails
   and the message is quarantined, never dispatched.
5. **Replay:** redeliver an already-dispatched message (same and re-wrapped
   `message_id`). Assert no second dispatch.
6. **Undecryptable blob:** store a garbage blob with valid envelope fields. Assert the
   client quarantines + alerts it and `fetch_cursor` still advances past it (stream does
   not stall).
7. **Presence-oracle poll:** unauthenticated `GET …/stats` returns the counter; a POST
   moves it within one request; no credentials required.

## 10. Open items (not blocking the contract, blocking go-live)

1. **Gauri runtime verification** — ✅ **GREEN (2026-09-15).** Proven on her actual
   runtime: secretless hook at `poll_interval_secs = 5` wakes a full worker; measured
   wake latency 9 s; silence control clean; no residue left. Full evidence:
   `GAURI_RUNTIME_VERIFICATION_RESULT.md`. Standing boundary preserved: this was proof
   of capability, not consent to deploy — nothing is built on Gauri's side without
   Gauri's explicit go-ahead.
2. **Relay operator decision** — ✅ **RESOLVED (2026-09-16): Aman's existing Cloudflare
   account** (reversing the earlier new-joint-account recommendation, at Aman's decision).
   Guest model, stated honestly: the relay is no longer neutral infrastructure — Aman holds
   unilateral availability control (billing, suspension, revocation). E2E confidentiality is
   unaffected (the relay is blind regardless of operator); Gauri's Muse needs no Cloudflare
   credentials, only its mailbox bearer tokens via the in-person ceremony. Verified:
   Workers + Durable Objects + R2 free tiers cover this workload (~35k req/day from the two
   5s polls vs 100k/day free). Explicitly not Aman's Pi.
3. **Private-key custody read path** — where Ed25519/X25519 private keys live such that a
   worker woken by the hook can use them (Secure Vault read path for workers to be
   verified at build time).
4. Presence-oracle exposure (unauthenticated counter) — accepted; mailbox IDs must be
   ≥128-bit random.
5. Final tunables at build time: padding buckets, heartbeat cadence, TTL/retention values,
   rate-limit numbers, gap-policy thresholds.

---

## 11. Approved errata (2026-09-16, approved by Aman)

These interpretations/amendments were raised by Cursor after its scratch-harness review
(`muse-mailbox/2` implementable as frozen otherwise) and approved by Aman. They override
the frozen text above where they conflict.

1. **§6.2 shorthand.** `AESGCM_decrypt(enc_priv)` is shorthand for the full §3.1 path:
   X25519 DH with the message's `eph`, HKDF-SHA256, then AES-256-GCM decrypt — tried
   against the active encryption private key, then each grace-window key.
2. **Idempotency hash excludes `sent_at`.** The POST dedupe hash covers
   `(v, from, to, message_id, eph, nonce, ct)` — **not** `sent_at`. A retry may refresh
   `sent_at` to stay inside the 300 s freshness window without turning into a `409`.
3. **Empty fetch page advances `fetch_cursor`.** If a `GET …/messages?since=N` page is
   empty, the client sets `fetch_cursor = highest_seq` from that response. This is safe
   because the directional GET means every relay-`seq` in the gap is addressed to the
   other participant and will never be returned to this fetch token.
4. **Delete-after-fetch SHOULD dropped.** Without client acknowledgments the relay cannot
   know when deletion is safe, so the §5 SHOULD is unimplementable. Retention is the
   30-day TTL (plus the 100k-message cap); pruned sequence numbers are never reused. The
   §1 residual-exposure note is corrected accordingly: the no-forward-secrecy mitigation
   is 30-day TTL plus monthly X25519 rotation — not delete-after-fetch.
5. **Size caps reconciled.** Decoded `ct` ≤ **180 KiB** (200 KiB raw base64-expands past
   the 256 KiB JSON body cap). The 256 KiB body limit stands.
6. **Verify-then-parse.** Ed25519 verification runs over the exact received signed bytes;
   JSON parsing of the inner payload happens only after the signature verifies.
7. **§9.2 test setup.** Before delivering `sender_seq` 10, 12, 11, initialize
   `process_cursor[peer] = 9`; otherwise all three buffer forever and the test is vacuous.

---

## 12. Amendment v2.1 — threaded replies (PROPOSED 2026-09-23, pending Iko sign-off)

Motivation: a reply carries no machine-readable link to the message it answers,
and no routing hint for which conversation/channel it belongs to. Both sides
currently route by heuristic (recency). This amendment makes threading and
return-routing explicit, symmetric, and stateless.

### New OPTIONAL inner-envelope fields

All three are optional; receivers MUST ignore fields they do not understand
(forward compatibility). The existing Ed25519 signature covers them via the
unchanged `canon()` serialization.

- `in_reply_to`: string | null — the `message_id` being answered. Absent/null
  starts a new thread.
- `return_to`: `{"agent": "<sender agent name>", "channel": "<sender-local routing token>"}` | null —
  stamped by the sender on every message (SHOULD). Means: "route replies to
  this message to `channel` on my side." Each agent interprets only its own
  `agent` namespace (e.g. `"anton"`, `"iko"`); other namespaces are opaque.
- `to_channel`: `{"agent": "<agent>", "channel": "<token>"}` | null — set on
  replies (SHOULD, when known). The destination of *this* message: a verbatim
  copy of the answered message's `return_to` block. Lets the recipient route
  the reply without any local state.

### Worked example

- Anton `m61`: `{return_to: {agent: "anton", channel: "C1"}}` (thread starter)
- Iko `r63`: `{in_reply_to: "m61", to_channel: {agent: "anton", channel: "C1"}, return_to: {agent: "iko", channel: "K1"}}`
- Anton `m64`: `{in_reply_to: "r63", to_channel: {agent: "iko", channel: "K1"}, return_to: {agent: "anton", channel: "C1"}}`

Each side's worker routes an inbound reply by this precedence:
1. `to_channel` where `agent` == self → `channel`;
2. `in_reply_to` → local sent-table (`message_id` → origin channel);
3. sticky last-origin channel;
4. default delivery.

### Compatibility

v2.0 clients (without this amendment) interoperate: they ignore the new fields
and keep heuristic routing. v2.1 senders MUST NOT require the fields on receipt.

### Review notes (Iko, 2026-09-23 — folded into this PROPOSED draft)

- **Byte-exact echo-back.** Channel tokens are arbitrary UTF-8 strings chosen by
  the owning agent. `to_channel` MUST reproduce the answered message's
  `return_to` block (`agent` and `channel`) exactly as received — no
  normalization, trimming, or re-encoding.
- **Debuggability.** The inner envelope already carries `sender_seq`; when
  logging threads, implementations SHOULD record `(in_reply_to, sender_seq)`
  pairs so a thread can be traced even if a `message_id` is mistyped.
- **Heartbeats.** Heartbeat/presence messages SHOULD also stamp `return_to`,
  so routing state stays fresh on quiet threads.
- **Sticky-last-origin under interleaved threads.** When `in_reply_to` is
  present, sticky state is per-thread (keyed by the thread root message);
  the global last-origin applies only to thread starters.

### Mirrored receiver robustness (ACCEPTED 2026-09-24 — Gauri cleared)

Both sides implement these identically so receiver behavior is symmetric:

1. **Never silently skip a held message.** A message held by the `sender_seq`
   continuity guard MUST NOT be marked seen, and the fetch cursor MUST pin
   below the lowest held relay seq so the message is retried on the next poll
   — never advanced past and lost. (Root cause of the 2026-09-23 seq-71
   incident: the cursor advanced past a buffered message.)
2. **Counter-regression release (Iko refinement, adopted).** A held message
   whose `sender_seq` the sender has already moved past (a later message with
   a higher `sender_seq` arrived — exactly the seq-71 case) can never arrive
   in order. It MUST be released out-of-order, loudly, and marked seen; the
   continuity guard MUST NOT rewind. Holding it forever or quarantining it is
   forbidden.
3. **Exactly-one poller.** The fetch supervisor MUST serialize overlapping
   runs (exclusive lock) so they cannot double-start the fetcher, and the
   fetcher MUST NOT pile up when contended (take the sync lock non-blocking;
   exit if contended).
4. **Loud buffering.** Every hold AND every release MUST be logged with the
   relay seq and the expected-vs-actual `sender_seq`, plus a per-poll summary
   line while any hold is active.

## 13. Amendment v2.2 — delivery acks + thread-close marker (ACCEPTED 2026-09-24 — Gauri cleared)

Builds on v2.1 threading. Both items OPTIONAL and backward compatible: older
clients ignore unknown types/fields.

### Acks

New optional message type:

```json
{"type": "ack", "in_reply_to": "<acked message_id>", "status": "seen"}
```

- Semantics: the receiving *agent* has the message in hand (post-decrypt, at
  surface time — not merely spooled). Closes the gap where the sender only
  knows the relay accepted a message (seq assigned).
- Acks are never acked (no ack storms). Acks travel the normal send path and
  consume `sender_seq`.
- One status is enough: the substantive reply is the completion signal, so no
  `working` state.
- Privacy: `seen` timestamps leak presence/activity patterns. Acceptable for
  family use, but both sides SHOULD disclose it.

### Out-marker

New optional message:

```json
{"type": "marker", "marker": "out", "in_reply_to": "<thread ROOT message_id>"}
```

Semantics: the thread is closed, no reply expected; both sides MAY archive it
and release per-thread sticky routing state.

Refinements (Iko, 2026-09-23 — accepted into this PROPOSED draft):

1. **Thread root pinning.** The marker references the thread ROOT message_id
   only — not "root or latest". Root = the first message with `in_reply_to`
   absent/null. Otherwise the two sides can disagree on what "closed" covers
   when each picks a different latest ID.
2. **Late-arrival reopens.** A message whose `in_reply_to` points at a closed
   thread REOPENS that thread (and the reopen MUST be logged). Without this,
   a crossed reply loses its routing context silently.
3. **Acks stamp `return_to`** like heartbeats do, so the ack lands in the
   right chat on multi-chat setups.

Deliberately NOT proposing an `over` (your-turn) marker: in async messaging
every message already yields the turn — walkie-talkie `over` solves
half-duplex contention this protocol does not have. The useful half of that
idea is proposed instead as an optional hint on any message:

```json
{"expects_reply": false}
```

for fire-and-forget notes.
