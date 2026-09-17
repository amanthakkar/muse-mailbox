# muse-mailbox/2 — Frozen Protocol Contract

**Status:** FROZEN for implementation (2026-09-15). **Approved errata 2026-09-16**
(Aman, via Anton) are folded into the normative text below and listed in
`docs/ERRATA_2026-09-16.md`. This document is the single authoritative spec. It
supersedes the v1 shared-passphrase draft and the investigation's draft language on
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
  (mitigated by 30-day TTL, 100k cap, and monthly X25519 rollovers — not delete-after-fetch).

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
- Decoded `ct` ≤ 180 KiB; `eph`/`nonce`/`ct` must be valid base64. (180 KiB keeps the
  base64 JSON body under the 256 KiB cap.)

**Relay behavior (in order):** validate token (must be a send token bound to this mailbox;
`from` must equal the token's participant; `to` must equal the counterparty) → idempotency
check on `message_id` (same id + identical payload hash of
`{v,from,to,message_id,eph,nonce,ct}` — **`sent_at` excluded** → return original `seq` with
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
sequence numbers are never reassigned. Clients must fetch promptly. There is **no**
delete-after-fetch: the API has no durable ack, so a `GET ?since=N` is not a receipt.
Forward-secrecy mitigation is the 30-day TTL plus monthly X25519 rotation — weaker than
delete-after-fetch, and stated honestly.

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
    if page.messages is empty:
      fetch_cursor = page.highest_seq       # directional GET omits the other direction
      break
    for msg in page.messages (already seq-ascending):
      handle(msg)
    fetch_cursor = page.next_since          # durable write; never moves backward
    if page.next_since == page.highest_seq: break
```
The secretless hook persists `last_seen_highest_seq` independently (a non-secret file).
`handle(msg)`:
```
1. if msg.message_id in seen_ids: return                      # duplicate delivery
2. aad = canonical_json({v, mailbox_id, message_id, from, to, eph})   # §3.1
   # AESGCM_decrypt over X25519 privs in earlier drafts was shorthand for:
   #   shared = X25519(enc_priv, eph); key = HKDF(...); AES-256-GCM decrypt
   pt = None
   for k in [active_enc_priv, *grace_enc_privs]:
     try: pt = ECIES_decrypt(k, msg.eph, msg.nonce, aad, msg.ct); break
     except InvalidTag: continue
   if pt is None: quarantine(msg); alert("undecryptable blob"); seen_ids.add(id); return
      # fetch_cursor still advances past it (§6.2 loop): one bad blob never stalls the stream
3. strip length prefix + zero padding → split sig (64 B) ‖ inner_bytes
   Ed25519_verify(pinned_peer_sign_pub, sig, inner_bytes)  # exact signed bytes
   inner = json.loads(inner_bytes)
   require canonical_json(inner) == inner_bytes            # then parse; fail → quarantine
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
2. **Out-of-order delivery:** precondition: `process_cursor[peer] == 9` (send 1..9 first,
   or initialize the cursor). Then deliver sender messages with `sender_seq` 10, 12, 11.
   Assert 11 is buffered then dispatched after 10, `process_cursor` ends at 12, and nothing
   was skipped or double-dispatched. From cursor 0, 10/12/11 all buffer and never dispatch
   (gap policy: never auto-skip).
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
2. **Relay operator decision** — ✅ Cloudflare (2026-09-16): new account created with
   Gauri, both humans hold the login, start on `workers.dev`, VPS is fallback.
   **Do not create the account until Aman says so.**
3. **Private-key custody read path** — ✅ file-based (2026-09-16). Hatch Secure Vault has
   no worker-readable named-secret KV. Keys + card live as mode `0600` files in a
   dedicated directory the secretless hook never opens. See `docs/PROVISIONING.md`.
4. Presence-oracle exposure (unauthenticated counter) — accepted; mailbox IDs must be
   ≥128-bit random. Gauri's acknowledgement still pending her go-ahead.
5. Final tunables at build time: padding buckets, heartbeat cadence, TTL/retention values,
   rate-limit numbers, gap-policy thresholds.
