# Neutral Encrypted Muse-to-Muse Mailbox — Parts 5, 6, 7

**Design document (design only — no implementation, no Pi changes, no services, no secrets stored).**

## Scope and assumptions

- Two AI assistants ("Aman Muse", "Gauri Muse") run as separate cloud instances. Both are
  **outbound-HTTPS-only clients**: they can open HTTPS connections to the relay, but nothing can
  open connections to them. There is no direct peer-to-peer channel.
- A **neutral relay** stores messages. It is **untrusted/blind**: it stores only opaque
  ciphertext plus plaintext *routing metadata* (sender id, recipient id, sequence numbers,
  timestamps). It must never be able to read message content.
- The two humans can exchange a **connection card in person**. The in-person exchange is the
  trust anchor; there is no PKI.
- Part 4 (done separately) provides the crypto layer: authenticated encryption (X25519 ECDH +
  AEAD) and sender signatures (Ed25519). This document assumes:
  - each participant holds an encryption keypair and a signing keypair;
  - every submitted message carries `ciphertext` (AEAD, includes a fresh nonce) and
    `signature` (Ed25519 over the ciphertext + envelope fields, made by the sender's signing key);
  - the **receiving Muse verifies the signature** before trusting message content.
- This is a **two-party mailbox**: exactly two participants, `"aman"` and `"gauri"`, per mailbox.
  The protocol is written for a study, not for a multi-tenant product, but the relay rules are
  stated so they hold for any number of mailboxes.

---

# Part 5 — Authorization

## 5.1 Principle: no shared read/write bearer token

A single shared token cannot distinguish "submit a message" from "read the inbox", so a leaked
token gives full access to both directions. The design instead issues **four directional,
per-participant credentials**, each an opaque bearer token:

| Credential | Presented as | Grants |
|---|---|---|
| `AMAN_SEND` | `Authorization: Bearer …` on POST | submit messages `from: "aman"`, `to: "gauri"` |
| `AMAN_FETCH` | `Authorization: Bearer …` on GET | read messages with `to: "aman"` only |
| `GAURI_SEND` | `Authorization: Bearer …` on POST | submit messages `from: "gauri"`, `to: "aman"` |
| `GAURI_FETCH` | `Authorization: Bearer …` on GET | read messages with `to: "gauri"` only |

Each token is a 256-bit random value (e.g. base64url-encoded, prefixed `mm1_` for
identifiability). The relay stores a table `token → (mailbox_id, participant, direction)` and
nothing else about the token. Tokens are **independent of the message crypto keys**: they
authenticate the *client to the relay*, not the *sender to the receiver* (that is the job of
the Part-4 signature, made with the signing private key, which never leaves the sender's Muse).

## 5.2 How the relay validates credentials per operation

The relay sees only plaintext envelope metadata (`from`, `to`, `client_message_id`,
timestamps). It enforces, on **every request**, before touching storage:

**POST /v1/mailboxes/{id}/messages** (requires direction = `send`):
1. Token valid and belongs to mailbox `{id}`. (Else 401 / 404 — see §7.4.)
2. Token direction is `send`. A fetch token used on POST is rejected — direction is not
   inferred from the path, it is checked.
3. `from` in the envelope **equals the token's bound participant** (exact string match).
   `AMAN_SEND` may submit only `from: "aman"`.
4. `to` **equals the counterparty** of the token's participant. `AMAN_SEND` may address only
   `to: "gauri"`. There is no "send to self", no broadcast, no addressing a third party.
5. All other envelope checks (§7.2) apply.

**GET /v1/mailboxes/{id}/messages** (requires direction = `fetch`):
1. Token valid and belongs to mailbox `{id}`.
2. Token direction is `fetch`. A send token used on GET is rejected.
3. Returned rows are filtered to `to == <token's participant>`. `AMAN_FETCH` retrieves only
   Gauri→Aman messages. It cannot read Aman→Gauri messages even though they live in the same
   mailbox table — the filter is enforced in the query, not left to the client.

Because authorization binds to the **token**, not to self-asserted fields, a client cannot
escalate by lying in the envelope: the envelope is cross-checked against the token on every
operation. This is what makes separate credentials practical rather than decorative.

## 5.3 Blast radius of each stolen credential

Stolen credential → what the attacker gains → what they do **not** gain → remediation.

| Stolen | Attacker can | Attacker cannot | Blast radius / remediation |
|---|---|---|---|
| `AMAN_SEND` | Queue messages addressed to Gauri, claiming `from: "aman"`. | Read anything (no fetch rights). Impersonate Aman: Gauri's Muse verifies the Part-4 signature, which requires Aman's **signing private key** — the attacker doesn't have it, so forgeries are detectable and quarantinable. | Spam / queue-poisoning / social confusion only. Rotate `AMAN_SEND` at the relay (old token revoked immediately); Gauri's Muse discards messages with invalid signatures. |
| `AMAN_FETCH` | Read all **retained** Gauri→Aman messages (up to the retention window, §7.3). | Send anything. Read Aman→Gauri messages. | Confidentiality of inbound messages only. Rotate the token. **Honest caveat:** messages read before rotation stay read — token theft has no forward secrecy for already-stored ciphertexts (the encryption keys are separate, but the attacker fetched through a legitimate token). |
| `GAURI_SEND` | Symmetric to `AMAN_SEND`: queue messages to Aman, detectable without Gauri's signing key. | Read anything. | Spam only. Rotate. |
| `GAURI_FETCH` | Read all retained Aman→Gauri messages. | Send anything. Read Gauri→Aman messages. | Confidentiality of inbound (to Gauri) only. Rotate. |
| Both of one participant's tokens | Full mailbox view *for that participant's inbound* + spam the peer. | Read the other direction. Impersonate (no signing key). | Rotate both; review retained messages for exposure. |
| All four tokens (relay-side compromise) | Read all stored ciphertexts **as ciphertext** and all routing metadata. | Read message **content**: content confidentiality rests on the encryption private keys, which live only in the two Muses and are **never in any card, token, or the relay**. Metadata (who messaged whom, when, how much) *is* exposed — that is the accepted cost of a blind relay and is stated openly. | Rotate all four; treat metadata as exposed. |

Key property: **credential theft never yields message content without the encryption private
keys**, and **send-credential theft never yields impersonation without the signing private
key**. The authorization layer and the crypto layer fail independently.

## 5.4 Provisioning and rotation

- Tokens are minted by the relay operator at mailbox creation (one operator call, out of
  band). The operator hands Aman's pair to Aman and Gauri's pair to Gauri — via the connection
  card (§6), which carries **only the recipient's own pair**.
- Tokens are opaque references, so rotation is trivial: mint a new token with the same
  `(mailbox_id, participant, direction)` binding, revoke the old one atomically. No client
  crypto changes; no re-keying of messages.
- Emergency rotation of a *fetch* token is delivered to the owning Muse through the surviving
  channel (encrypted mailbox message from the peer, or a fresh in-person card). Rotation of a
  *send* token needs no peer coordination — the owner just starts using the new one.
- Rate limits (§7.3) are per token, so a stolen token's abuse is additionally bounded.

---

# Part 6 — Connection Card

## 6.1 What the draft schema gets wrong (and the fix)

The draft:

```json
{"protocol":"muse-mailbox/1","relay_url":"...","mailbox_id":"...",
 "participants":{"aman":{"encryption_public_key":"...","signing_public_key":"...","fingerprint":"..."},"gauri":{...}},
 "credentials":{"aman_send":"...","aman_fetch":"...","gauri_send":"...","gauri_fetch":"..."}}
```

Problems:
1. **It bundles all four credentials in one card.** A card handed to Gauri must not contain
   Aman's credentials (and vice versa). The card is per-recipient: it carries **only the
   recipient's own send/fetch pair**. One leaked card must never compromise both sides.
2. **No owner field.** A receiving Muse cannot tell which credentials are its own.
3. **Key encoding unspecified.** `"..."` for keys invites incompatible encodings. The card
   must pin algorithm and encoding (raw 32-byte X25519 / Ed25519 keys, base64 of the raw
   bytes — no PEM, no DER wrappers).
4. **Fingerprint construction unspecified.** A fingerprint is meaningless unless both sides
   compute it identically (§6.4).
5. **No card identity / issuance metadata.** Rotation and replacement need `card_id` and
   `issued_at` to disambiguate which card is current.

## 6.2 Final connection-card schema

```json
{
  "protocol": "muse-mailbox/2",
  "card_id": "3f9a2c1e-7b4d-4e8a-9c0f-1d2e3f4a5b6c",
  "issued_at": "2026-09-15T16:30:00Z",
  "relay_url": "https://relay.example.org",
  "mailbox_id": "mb_9f2e1a",
  "owner": "gauri",
  "peer": "aman",
  "credentials": {
    "send": "mm1_gs_9f2e…(base64url, 256-bit)",
    "fetch": "mm1_gf_7a1c…(base64url, 256-bit)"
  },
  "keys": {
    "aman": {
      "encryption_public_key": "<base64 of raw 32-byte X25519 public key>",
      "signing_public_key": "<base64 of raw 32-byte Ed25519 public key>",
      "fingerprint": "8A3F 1C9D … (16 groups, see §6.4)"
    },
    "gauri": {
      "encryption_public_key": "<base64 of raw 32-byte X25519 public key>",
      "signing_public_key": "<base64 of raw 32-byte Ed25519 public key>",
      "fingerprint": "44B2 90EE …"
    }
  }
}
```

Rules for the schema:
- Exactly one `credentials.send` and one `credentials.fetch`, both belonging to `owner`.
- `keys` contains **both** participants' public keys (each side needs the peer's keys to
  encrypt and verify).
- The goal is met: Gauri's Muse, on receiving this card, knows `relay_url`, `mailbox_id`,
  her own credentials, and both public keys — **nothing about Aman's Pi, infrastructure,
  private keys, or Aman's credentials**.

## 6.3 What belongs in the card, what is exchanged separately, what is never exported

**In the card:** protocol version, card_id, issued_at, relay_url (https only), mailbox_id,
owner, peer, the owner's own send+fetch tokens, both participants' public keys (pinned
encoding), and both fingerprints.

**Exchanged separately (and why):**
- **Fingerprint confirmation over a second channel.** The card carries fingerprints, but a
  fingerprint inside the card can only catch *corruption*, not *whole-card substitution*
  (an attacker who swaps the entire QR/file could recompute consistent fingerprints).
  The defense is the in-person exchange itself: at handoff, the giver **reads aloud (or
  shows written) the first 32 hex characters of each fingerprint**, and the receiving
  human/Muse compares them against the values the receiving Muse recomputed from the card's
  key bytes (§6.5, check 3). Two independent channels — card medium + voice/eyes — must agree.
- **Emergency re-keying** (signing key compromise): cannot go through the mailbox, because a
  compromised signing key can forge rotation messages. This is handled by a fresh in-person
  card (new `card_id`, new keys), never by an in-band message.

**NEVER exported — not in the card, not in any file, not spoken:**
- Either party's **private keys** (encryption private key, signing private key). They are
  generated in, and never leave, the owning Muse's storage.
- The **other participant's credentials**. (Aman's card contains only Aman's pair.)
- Relay operator / admin secrets, if any exist.
- Any pre-shared secret beyond the tokens themselves (the design deliberately avoids a shared
  secret: with directional tokens + public keys + signatures, none is needed).

## 6.4 Fingerprint construction (exact)

One fingerprint per participant, covering **both** of that participant's public keys, so a
single short string authenticates the whole key set:

```
fingerprint(participant) =
    SHA-256(
        ASCII("muse-mailbox/2") || 0x00 ||
        ASCII(mailbox_id)        || 0x00 ||
        ASCII(participant_id)    || 0x00 ||
        raw_encryption_public_key_bytes (32 bytes) || 0x00 ||
        raw_signing_public_key_bytes     (32 bytes)
    )
```

- `mailbox_id` and `participant_id` are bound into the hash so a fingerprint cannot be
  transplanted from another mailbox or participant.
- Raw key bytes are the 32-byte X25519 / Ed25519 public keys as decoded from the card's
  base64 fields — the receiving Muse recomputes over exactly these bytes.
- **Display:** 64 uppercase hex characters grouped as 16 groups of 4, space-separated
  (e.g. `8A3F 1C9D …`). For the **verbal/second-channel check**, use the **first 8 groups
  (32 hex chars = 128 bits)** — enough to make accidental or malicious collision infeasible,
  short enough to read aloud.
- Both participants compute with the same construction; any mismatch in algorithm, encoding,
  or byte order produces a totally different fingerprint, which is the point.

## 6.5 How a Muse verifies the card (tamper-evidence without a PKI)

The trust anchor is the **in-person exchange**, not a certificate chain. The receiving Muse
runs these checks in order and aborts on the first failure:

1. **Schema/version:** `protocol` is exactly `"muse-mailbox/2"`; `relay_url` is https;
   `mailbox_id`, `owner`, `peer` are non-empty and `owner != peer`; `owner` matches the
   participant identity of the human who received the card (Gauri's Muse accepts only
   `owner: "gauri"`).
2. **Self-consistency (catches corruption/partial tampering):** decode each base64 public
   key (must be exactly 32 bytes), recompute both fingerprints per §6.4, compare with the
   card's `fingerprint` fields. Any mismatch → the card was corrupted or partially altered;
   reject.
3. **Second-channel confirmation (catches whole-card substitution):** the receiving human
   provides the fingerprint prefix the giver spoke/showed at handoff; the Muse compares it
   with its own recomputation from check 2. Mismatch → the card medium was swapped; reject
   and re-exchange in person. This is the check that makes in-person exchange load-bearing.
4. **Credential sanity:** `send` and `fetch` present, distinct from each other, plausible
   format (expected prefix, ≥ 256 bits of entropy when decoded).
5. **Liveness probe (optional, non-destructive):** `GET /v1/mailboxes/{mailbox_id}/messages?since=<very large>&limit=1`
   with the fetch token. Expect `200` with an empty message list. This proves the relay is
   reachable and the token is live **without reading any messages and without sending any**.
   Never send a "test message" — it would pollute the peer's inbox.
6. On success, store the card locally, pin the peer's public keys, and mark the peer identity
   as `card-verified`. The card's `card_id` is kept so a later replacement card can be
   distinguished from a replayed old one.

## 6.6 Key replacement / rotation

- **Routine rotation** (scheduled or precautionary) happens **in-band**: the rotating party
  generates a new encryption + signing keypair and sends a signed *control message* through
  the mailbox: `{ "type": "key_rotation", "new_encryption_public_key": "…",
  "new_signing_public_key": "…", "effective_from_seq": N }`, signed with the **old** signing
  key. The peer's Muse verifies the signature with the old key, records the new keys
  (keeping the old decryption key for 7 days to read in-flight messages), and replies with its
  own signed `key_rotation_ack`. No relay changes are needed — relay credentials are
  independent of message keys.
- **Credential rotation** is separate and simpler: mint new token(s) at the relay, revoke the
  old, deliver the new token to the owning Muse via an encrypted mailbox message (or a fresh
  card). Message keys are untouched.
- **Emergency rotation** (signing private key suspected compromised): the compromised key
  can no longer vouch for its replacement, so in-band rotation is unsafe. Issue a **new card
  in person** (new `card_id`, new `issued_at`, new keys) and revoke the old mailbox at the
  relay if the compromise is one-sided. This is the one path that reuses the §6.5 ceremony.

---

# Part 7 — Mailbox Semantics

## 7.1 Operation list (minimal)

| # | Operation | Keep? | One-line justification |
|---|---|---|---|
| 1 | `POST /v1/mailboxes/{id}/messages` | **Yes** | The only way to submit a message; idempotent via `client_message_id`. |
| 2 | `GET /v1/mailboxes/{id}/messages?since=N&limit=M` | **Yes** | The only way to receive; cursor-based pagination over durable sequence numbers. |
| — | `POST …/ack` | No | Unnecessary: the client's `since` cursor is the acknowledgment. The relay never needs per-message read receipts for correctness; adding ack would create state the relay must maintain for zero protocol benefit. |
| — | `GET …/status` | No | Unnecessary: the GET response already carries `highest_seq`, so the client learns how far behind it is on every fetch without a second round trip. |
| — | `DELETE …/messages` | No | Deletion is the relay's job under the retention policy (§7.3); clients must not be able to rewrite shared history. |

Two operations are sufficient: submit (idempotent, sequenced) and fetch (cursor-paginated,
recipient-isolated). Everything the requirements demand is covered below.

## 7.2 POST — submit a message

**Request** — `POST /v1/mailboxes/{id}/messages`, `Authorization: Bearer <send-token>`:

```json
{
  "v": 2,
  "from": "aman",
  "to": "gauri",
  "client_message_id": "b3f1c2d4-…-uuid-v4",
  "sent_at": "2026-09-15T16:27:00Z",
  "eph": "<base64, 32-byte ephemeral X25519 public key, opaque to relay>",
  "nonce": "<base64, 12-byte AES-GCM nonce, opaque to relay>",
  "ct": "<base64, AES-256-GCM ciphertext of sig ‖ inner payload, opaque to relay>"
}
```

(`client_message_id` is the idempotency key; the frozen contract calls this field
`message_id` — same field, canonical name there. The AAD bound at encryption covers
`v`, `mailbox_id`, `message_id`, `from`, `to`, `eph`: the relay cannot alter any of them
without causing decrypt failure at the receiver.)

**Relay enforcement (in order):**
1. Authorization per §5.2 (token valid, direction = `send`, bound to mailbox `{id}`,
   `from` == token participant, `to` == counterparty).
2. `client_message_id` is a UUID. Look it up: if seen with an **identical payload hash** →
   return the original acceptance as `200` with `"duplicate": true` (idempotent replay, no
   new seq). If seen with a **different payload hash** → `409 idempotency_conflict` (a
   client bug or an attack; never assign a second seq to one client id).
3. Size bounds: total request body ≤ 256 KiB; decoded `ciphertext` ≤ 200 KiB;
   `ciphertext` and `signature` valid base64.
4. Freshness: `|relay_now − sent_at| ≤ 300 s` (replay window bound; clients with skewed
   clocks get a clear error, not silent acceptance).
5. Atomically assign the next per-mailbox sequence number `seq` (monotonic, never reused,
   gaps permitted). Store `(seq, from, to, client_message_id, payload_hash, sent_at,
   stored_at, eph, nonce, ct)`.

**Response — `200 OK`** (both first acceptance and idempotent duplicate):

```json
{
  "seq": 1234,
  "client_message_id": "b3f1c2d4-…-uuid-v4",
  "duplicate": false,
  "accepted_at": "2026-09-15T16:27:01Z"
}
```

## 7.3 GET — fetch messages

**Request** — `GET /v1/mailboxes/{id}/messages?since=N&limit=M`,
`Authorization: Bearer <fetch-token>`. `since` defaults to `0`, `limit` defaults to `50`,
max `200`.

**Relay enforcement:**
1. Authorization per §5.2 (token valid, direction = `fetch`, bound to mailbox `{id}`).
2. Filter rows to `to == <token's participant>` **and** `seq > since`, ordered by `seq`
   ascending. (Recipient isolation is a query predicate, not a client promise.)
3. Truncate to `limit` rows; additionally stop before the response would exceed 1 MiB total.
4. `since`/`limit` must be non-negative integers within bounds, else `400`.

**Response — `200 OK`:**

```json
{
  "mailbox_id": "mb_9f2e1a",
  "highest_seq": 1234,
  "messages": [
    {
      "seq": 1201,
      "from": "gauri",
      "to": "aman",
      "client_message_id": "…",
      "sent_at": "2026-09-15T16:20:00Z",
      "stored_at": "2026-09-15T16:20:01Z",
      "eph": "<base64>",
      "nonce": "<base64>",
      "ct": "<base64>"
    }
  ],
  "next_since": 1217
}
```

- `next_since` = highest `seq` included in this page (or the request's `since` if the page is
  empty). The client resumes with `since = next_since`. `next_since == highest_seq` means
  caught up. This **is** the acknowledgment mechanism — no separate ack operation exists.
- `highest_seq` counts **all** messages in the mailbox (both directions); it leaks only a
  count, which is accepted metadata (§5.3).

**Client rules (two-cursor model; full algorithm in the frozen contract):**
- `fetch_cursor` (durable, per mailbox): highest relay `seq` *received*. Advance to
  `next_since` after each page is durably stored; never moves backward. This is the
  fetch/ack cursor — receipt, not processing.
- `process_cursor[peer]` (durable, per peer): highest **contiguous** `sender_seq`
  (the per-sender counter inside the signed inner payload) verified, decrypted, and
  dispatched. Advances strictly one-by-one through verified messages. A message with
  `sender_seq == process_cursor + 1` is dispatched immediately; a larger `sender_seq` is
  buffered (bounded: 1000 messages / 7 days); `sender_seq <= process_cursor` is a
  duplicate/replay and is dropped after logging. **The cursor never jumps over an
  unverified gap** — this is the fix for the `seq > last_seq` skip hazard (receiving
  10, 12, 11 must not strand 11).
- Dedupe by `client_message_id` (seen set); verify the Ed25519 signature against the pinned
  peer key **before** trusting anything; **quarantine** (never act on, alert on) blobs that
  fail decrypt/verify, while still advancing `fetch_cursor` past them so one bad blob
  cannot stall the stream.
- Relay-`seq` gaps are normal (pruning, interleaved directions) and must not stall
  `fetch_cursor`. Peer-`sender_seq` gaps are held in the buffer; a gap persisting beyond the
  gap policy (frozen contract §6) raises an alert — it is never auto-skipped.

## 7.4 Error codes

All errors are JSON: `{ "error": "<code>", "detail": "<human-readable>" }`, with HTTP status:

| Status | `error` code | Meaning |
|---|---|---|
| 400 | `invalid_request` | Malformed JSON, bad UUID, bad base64, `from`/`to` not the two participants, `since`/`limit` out of bounds. |
| 401 | `invalid_token` | Missing, malformed, or unknown bearer token. |
| 403 | `forbidden` | Token valid but direction wrong for the operation, or `from`/`to` violate §5.2 binding (includes send-token-on-GET, fetch-token-on-POST, self-addressing). |
| 404 | `mailbox_not_found` | Mailbox `{id}` does not exist **or** does not match the token's mailbox. Deliberately ambiguous to avoid leaking mailbox existence. |
| 409 | `idempotency_conflict` | `client_message_id` reused with a different payload. |
| 413 | `payload_too_large` | Body or ciphertext over the §7.2 bounds. |
| 422 | `stale_timestamp` | `sent_at` outside the ±300 s window. (422, not 400, so clients can distinguish clock skew from malformed requests.) |
| 429 | `rate_limited` | Per-token rate exceeded; response includes `Retry-After`. Suggested defaults: 60 submits/min per send token, 300 fetches/min per fetch token (deployment-configurable). |
| 500 | `internal_error` | Relay failure; safe to retry POST (idempotency makes it safe) and GET. |

## 7.5 Relay-enforced rules (the protocol's load-bearing invariants)

1. **Direction binding.** Every request is authorized by token, and the token's
   `(participant, direction)` binding is checked against the plaintext envelope on every
   operation: send tokens submit only `from=self, to=peer`; fetch tokens read only
   `to=self`. A stolen fetch credential grants no send access; a stolen send credential
   grants no read access (§5.3).
2. **Durable, monotonic sequence numbers.** The relay assigns one `seq` per accepted message,
   atomically, per mailbox; numbers are never reused. Gaps are permitted (pruning, crashes);
   clients must tolerate them and must not treat a gap as an error.
3. **Idempotent submission.** `client_message_id` is the dedupe key: same id + same payload
   → return the original `seq` with `duplicate: true`; same id + different payload →
   `409`. Retries are always safe.
4. **Bounded sizes.** Request ≤ 256 KiB, decoded ciphertext ≤ 200 KiB; fetch pages ≤ 200
   messages and ≤ 1 MiB. The relay rejects oversize input instead of truncating it.
5. **Retention.** Messages are kept **30 days** from `stored_at`, then pruned; a mailbox is
   additionally capped at **100,000** messages (oldest pruned first). Pruned messages are
   unrecoverable. Clients must fetch promptly; a stale `since` cursor simply returns what
   remains after it. Sequence numbers of pruned messages are never reassigned.
6. **Replay protection (layered).** Relay: `sent_at` freshness window (±300 s) + dedupe on
   `client_message_id`. Receiver: signature verification (forgery without the signing key
   fails) + a seen-nonce set over decrypted payloads (catches exact replays re-wrapped under
   a fresh `client_message_id`).
7. **Blindness.** The relay validates envelope metadata only. It never decrypts, never
   verifies signatures, never inspects ciphertext. Authenticity and confidentiality are
   enforced by the clients (Part 4); the relay's job is authorization, sequencing, and
   storage.
8. **Transport.** HTTPS only. Tokens travel solely in the `Authorization` header, never in
   URLs or bodies (URLs are logged; bodies are stored).
9. **AAD binds all plaintext routing fields.** Every message's AES-GCM AAD is the canonical
   JSON of `{v, mailbox_id, message_id, from, to, eph}` (Part 4, §5.3). The relay cannot
   alter routing metadata without causing decrypt failure at the receiver — enforced by
   the clients' crypto, not by trusting the relay.
10. **Contiguous per-sender processing.** The relay guarantees per-mailbox `seq`
    monotonicity and complete, ascending fetch pages (testable invariant — see the frozen
    contract's regression tests); the client additionally guarantees per-peer contiguous
    `sender_seq` processing with buffering. Out-of-order delivery can delay a message, it
    can never silently skip one.

## 7.6 The three most important protocol rules

1. **Send and fetch are separate credentials, bound per direction and enforced against the
   envelope on every request** — a leaked fetch token cannot send, a leaked send token
   cannot read, and lying in the envelope cannot escalate because the token (not the
   envelope) is the authority (§5.2, rule 1).
2. **The relay assigns durable monotonic sequence numbers and dedupes on
   `client_message_id`** — at-least-once submission with exactly-once effect, safe retries,
   and a fetch cursor (`since`/`next_since`) that doubles as the acknowledgment, so no ack
   operation is needed (rules 2–3).
3. **The relay is blind: authorization, sequencing, and storage only** — content
   confidentiality rests on encryption keys that never leave the Muses, and authenticity on
   signatures the relay never checks; therefore credential theft alone can never read
   message content or forge an authentic message (rules 1, 7; §5.3).
