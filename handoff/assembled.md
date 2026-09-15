# CURSOR HANDOFF — neutral encrypted Muse↔Muse mailbox (muse-mailbox/2)

**From:** Anton (Aman's Muse) · **To:** Cursor (Pi ops lead)
**Date:** 2026-09-16 · **Authorized by:** Aman, 2026-09-16 ("hand the frozen contract to Cursor")
**Both handoff gates are GREEN:**
1. Gauri's runtime verification PASSED (2026-09-15): secretless 5s hook wakes a full
   worker on her actual Muse runtime; measured wake latency 9s; silence control clean;
   no residue. Full result: `GAURI_RUNTIME_VERIFICATION_RESULT.md` (Appendix B below).
2. Frozen contract reviewed and approved by Aman (his three findings — AAD binding all
   six routing fields, two-cursor receive model replacing `seq > last_seq`, relay
   substrate evaluation — are incorporated).

---

## 1. What you are being asked to do
**In scope (do these):**
1. **Review the frozen contract** (`PROTOCOL.md`, muse-mailbox/2, pasted in full below).
   Flag any hole, ambiguity, or deviation you need — but treat the contract as frozen:
   do not silently redesign; raise changes back to Anton.
2. **Evaluate the relay substrate.** Aman prefers you evaluate **Cloudflare Worker +
   Durable Object (seq/dedupe/presence/auth) + R2 (ciphertext)** rather than blindly
   using Worker + KV. Prove the six points from the contract: (1) atomic monotonic
   mailbox seq assignment, (2) complete ordered pagination, (3) idempotent POST,
   (4) directional token enforcement, (5) counter-only unauthenticated stats,
   (6) pruning without sequence reuse. Recommend DO+R2 or Worker+KV with evidence.
3. **Produce an implementation plan** against the frozen contract.
4. **Build and test in dev/scratch ONLY** (scratch Cloudflare account or local harness).
   Run the contract's mandatory test matrix: 2×50 concurrent POSTs with randomized
   delay; relay kill/restart mid-stream; tiny-page pagination; exactly-once dispatch;
   contiguous per-peer processing; original-sequence response for idempotent retries;
   409 for conflicting message-ID reuse; out-of-order 10,12,11; AAD tamper matrix for
   all six bound fields; signature tampering; replay; undecryptable blob quarantined
   without stalling; unauthenticated presence-counter update.
5. **Design (don't execute) the connection-card provisioning flow**: how the four
   directional tokens and the two keypairs get generated and handed to each Muse
   without either Muse depending on the other's infrastructure. Key insight from the
   contract: private keys must be readable by a woken worker — propose the read path
   for both runtimes.
6. **Recommend the neutral operator/account** for the relay (must NEVER be Aman's Pi;
   neither Muse may depend on the other person's infrastructure).
**Explicitly NOT authorized (do not do these):**
- No production deployment: no live Workers, Durable Objects, R2 buckets, KV
  namespaces, accounts, or routes for the real mailbox.
- No real keypairs, no real bearer tokens, no real mailbox IDs minted.
- Nothing on Gauri's side — no hook, no worker, no key material. Her Muse's standing
  boundary: verification was proof of capability, NOT consent to deploy. Nothing is
  built on her runtime without Gauri's explicit go-ahead, relayed by Aman.
- Do not host the relay on Aman's Pi, do not touch Pi/Cloudflare/tunnel/Cortex
  infrastructure for this project.
- Do not involve Gauri's Muse directly — there is no channel; Aman relays via Gauri.

---

## 2. Key architecture facts (so you don't re-derive them)

- True push is impossible on both runtimes. Proven design: secretless 5s hook polls
  unauthenticated `GET /v1/mailboxes/{id}/stats → {"highest_seq": N}`; on change, a
  full worker wakes and does the authenticated fetch + signature verify + decrypt.
  Practical latency 5–15s (9s measured on Gauri's side).
- Hook scripts are secretless: they hold no bearer tokens or private keys. Tokens and
  keys live only in the woken worker's read path (your item 5 above).
- Crypto: per-Muse Ed25519 identity + X25519 recipient key; fresh ephemeral X25519 per
  message; HKDF-SHA256 → AES-256-GCM; sign inner payload, then encrypt; AAD binds
  `{v, mailbox_id, message_id, from, to, eph}` (canonical JSON). No passphrase in the
  message path. No forward secrecy in v2 (mitigations: short retention, key rotation).
- Two cursors on receive: `fetch_cursor` (highest relay seq durably received) and
  `process_cursor[peer]` (highest contiguous per-sender `sender_seq` dispatched).
  Never jump an unresolved per-sender gap; relay-seq gaps don't stall fetching;
  undecryptable blobs quarantine without stalling.
- Four directional bearer tokens: AMAN_SEND, AMAN_FETCH, GAURI_SEND, GAURI_FETCH.
- Relay is a blind store: sees timing, counts, size buckets, direction — never content.
  Presence oracle exposes only `highest_seq` (accepted metadata leakage, pending
  Gauri's acknowledgment — see her boundary above).
- Wake authority == send authority: only send-token holders can move the counter, so
  the wake leg grants no new privilege. Residual wake-spam is bounded by per-token
  rate limits (contract §2.4, §4).

---

## 3. What Anton needs back from you
1. Contract review: holes/ambiguities, or explicit "contract is implementable as frozen".
2. Substrate recommendation (DO+R2 vs Worker+KV) with evidence against the six proof points.
3. Implementation plan.
4. Dev/scratch test-matrix results (all mandatory tests, pass/fail with evidence).
5. Connection-card provisioning design (both sides' worker key/token read paths).
6. Neutral operator/account recommendation.
7. Anything you need from Aman or from Gauri's Muse (via Aman) before production could
   be considered — e.g. retention/padding/heartbeat parameters, Gauri's key material
   handoff ceremony, her acknowledgment of the presence-oracle metadata leakage.

Reply via the relay (cursor_chat ingest). Anton's poller picks it up and notifies Aman.


---

# APPENDIX A — Frozen contract (muse-mailbox/2), full text

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
2. **Relay operator decision** — who provisions the neutral relay (Cloudflare account or
   tiny VPS). Explicitly not Aman's Pi. No accounts created until Aman approves.
3. **Private-key custody read path** — where Ed25519/X25519 private keys live such that a
   worker woken by the hook can use them (Secure Vault read path for workers to be
   verified at build time).
4. Presence-oracle exposure (unauthenticated counter) — accepted; mailbox IDs must be
   ≥128-bit random.
5. Final tunables at build time: padding buckets, heartbeat cadence, TTL/retention values,
   rate-limit numbers, gap-policy thresholds.


---

# APPENDIX B — Gauri runtime verification result, full text

# Gauri Runtime Verification — Result (GREEN)

**Date run:** 2026-09-15, ~12:35–12:41 PDT. **Run by:** Gauri's Muse, in her own side chat.
**Reported to Aman:** 2026-09-16 (pasted in chat). Recorded here verbatim as the evidence
of record for the Cursor handoff.

## Purpose

Prove the frozen contract's load-bearing assumption: a secretless hook with
`poll_interval_secs = 5` can wake a full worker on Gauri's actual Muse runtime.

## Step 0 — Capability self-report: PASS

Event hooks supported via `hooks.add`. Minimum quoted from the tool schema:

```json
"poll_interval_secs": { "description": "Seconds between script polls. Defaults to 60.",
"maximum": 604800, "minimum": 5, "type": "integer" }
```

Hook scripts run without secrets — quoted from the runtime description: the script "runs
through the normal tracked shell executor with the same base command environment and
proxy setup as an ordinary shell command, but without secrets or connector credentials."

## Step 1 — Live wake test: PASS

Hook `hook-wake-test` installed with `poll_interval_secs: 5`, delivery defaulting to the
chat. The installed script contained no secrets, tokens, or credentials.
| Time (PDT) | Event |
|---|---|
| 12:35:53 | Dry run with no tripwire → silent, reason `no-tripwire` (correct) |
| 12:35:54 | Hook enabled, live polling at 5s |
| 12:35:55 | Tripwire `~/hook-test/GO` created |
| 12:36:04 | Woken worker appended timestamped line to `~/hook-test/WOKE.log` |
| 12:36:04 | "hook wake confirmed" relayed in chat |

**Wake latency: 9 seconds** (tripwire → worker-confirmed), inside the ~120s budget.
`WOKE.log` gained its timestamped line. Note: two poll cycles fired while the tripwire
existed (~12s window); the second worker verified after the file was already deleted and
logged anyway per its instructions — a late artifact of a pre-deletion wake, not a new wake.

## Step 2 — Silence control: PASS

Tripwire deleted 12:36:07 PDT. Over the next five minutes (observed until 12:41:11 PDT),
the hook's invocation log shows silent / `no-tripwire` on every 5-second poll — zero new
wakes. Wakes tracked the tripwire exactly: fired while it existed, silent after.

## Step 3 — Secretless shape confirmation: PASS

Script re-shown; it references no secret, token, password, API key, or credential.
Confirmed the same shape works against an unauthenticated HTTPS endpoint — the hook
runtime prescribes "bounded fetches such as `curl --fail --silent --show-error --max-time 10`
…" against public endpoints. A `GET /v1/mailboxes/{id}/stats → {"highest_seq": N}`
presence poll is the same shape.

## Step 4 — Cleanup: done

Hook definition removed (12:41:13 PDT); `~/hook-test/` and the hook script deleted. No
residue left on the runtime.

## Verdict: GREEN

All four steps pass with minimum interval = 5. The assumption is proven.

## Deviations from the protocol (recorded honestly by Gauri's Muse)

- Gauri pasted the whole protocol at once instead of step-by-step; her Muse executed every
  step — including creating and deleting the tripwire — on her behalf in one session.
- The protocol's "exit 0 (wake)" phrasing doesn't match the actual API: scripts must end
  with exactly one wake `reason 'payload'` or silent `reason 'payload'` call. Equivalent
  semantics were implemented.
- New hooks start disabled; per the runtime's required flow, her Muse dry-ran (silent, as
  expected) and then enabled before the live test.

None of these affect the contract — they are operational details of her runtime's hook API.

## Standing boundary (stated by Gauri's Muse, preserved)
> Verification passing is not authorization to build. The contract's next step — this
> runtime polling an external, unauthenticated mailbox URL and waking whenever it changes —
> means anyone able to write to that endpoint could wake Gauri's worker. I will not build
> the external wake leg without Gauri's explicit go-ahead, and it should carry
> authentication before anything real is built.

### Analyst note on the wake-authority concern (Aman's Muse, 2026-09-16)

The concern is real but bounded, and it does not require redesigning the oracle:

- **Wake authority == send authority.** The presence counter moves only when a message is
  POSTed, and POST requires a valid send token. The set of parties who can cause a wake is
  exactly the set who can already submit messages: Aman, Gauri, or a thief of a send token.
  No new privilege is created by the wake leg — an outsider with no token cannot move the
  counter at all.
- **The residual is wake-spam as compute DoS**, not a confidentiality or forgery issue: a
  send-token thief's garbage blobs fail signature verification and are quarantined; the
  cost is wasted worker wakes. This is already mitigated in the frozen contract by
  per-token rate limits (60 submits/min per send token) and alerting on authentication
  spikes (`PROTOCOL.md` §2.4, §4).
- **The oracle poll itself cannot carry authentication** — that is the point of the
  secretless hook (the runtime forbids secrets in hook scripts). The security boundary
  sits at the POST (send token), the fetch (fetch token), and the signature verification
  (pinned Ed25519 key). Authenticating the *read* of a public counter would add nothing:
  the counter's movement is already gated by authenticated writes.
- **Agreed unconditionally:** nothing is built on Gauri's side without Gauri's explicit
  go-ahead. Her runtime, her call. This verification was proof of capability, not consent
  to deploy.
