# Part 4 — Cryptographic Design Analysis

**Status:** analysis only — no implementation, no Pi changes, no services, no secrets stored.
**Date:** 2026-09-15
**Scope:** choose the message-layer cryptography for the neutral blind-relay mailbox between
"Aman Muse" and "Gauri Muse" (separate cloud instances, outbound-HTTPS-only, no inbound connections).
The relay stores ciphertext and is untrusted. The two humans can exchange secrets in person.

## 1. System and trust model (what the crypto must survive)

- **Relay:** untrusted / blind. May read everything it stores, and may tamper actively
  (modify, drop, replay, reorder, withhold, partition). Must learn nothing about message contents.
- **Bearer tokens:** separate send-token and fetch-token per Muse. Either may be stolen independently
  of message keys.
- **Endpoints:** each Muse instance is trusted while uncompromised; a compromised instance is assumed
  to leak its keys and its plaintext. (No crypto fixes endpoint compromise — stated so the analysis
  doesn't pretend otherwise.)
- **Humans:** Aman and Gauri meet in person and can verify short fingerprints / scan QR codes honestly.
  This in-person channel is the **sole root of trust** for bootstrapping.
- **Traffic:** low volume, asynchronous, store-and-forward polling. No interactive session exists,
  so anything resembling an interactive handshake (Signal-style session setup) must be emulated over
  a polling mailbox — expensive and fragile.

## 2. The three options, defined precisely

### Option A — status quo: shared human passphrase → HKDF-SHA256 → AES-256-GCM

One symmetric key for everything, derived from a human-memorable passphrase via HKDF-SHA256
(a **fast** KDF — this matters, see §3). Random 96-bit nonce per message, JSON envelope v1.
Round-trip works; wrong key and tampering are rejected; nonces are unique. All true — and all
insufficient, as shown below.

### Option B — per-Muse asymmetric identity keys (naive construction)

Each Muse holds its own long-term keypairs (X25519 for encryption, Ed25519 for signing); public
keys are exchanged in person. The naive construction this usually implies: **static–static**
X25519 ECDH between the two long-term encryption keys → KDF → AES-GCM, plus a detached Ed25519
signature for authenticity. No per-message ephemerals, no specified construction details.

### Option C — hybrid: sign-then-encrypt with per-message ephemeral keys (recommended)

Each Muse holds a long-term **Ed25519 identity/signing keypair** and a long-term **X25519
encryption keypair** (separate key material — never reuse one keypair across DH and signing).
Per message:

1. Sender signs the canonical inner envelope with its Ed25519 key (**sign**).
2. Sender generates a fresh ephemeral X25519 keypair, ECDH's against the recipient's long-term
   X25519 public key, runs HKDF-SHA256 over the shared secret, and AES-256-GCM-encrypts
   `signature ‖ inner envelope` under the derived key (**encrypt**, ECIES-style).
3. The outer envelope on the relay contains **only** `{v, ephemeral_pubkey, nonce, ciphertext}` —
   no sender, recipient, or key identifiers in plaintext.

C is "B done properly": B's identities plus a fully specified construction with per-message
ephemeral encryption keys. C strictly dominates B (see §3); B's only edge is marginally less code.

## 3. Head-to-head: the twelve questions

| # | Question | A (shared passphrase) | B (naive asymmetric) | C (hybrid, recommended) |
|---|----------|----------------------|---------------------|------------------------|
| 1 | Sender authenticity | ❌ None between parties — either key holder could have written it; proves only "a key holder" | ✅ Strong *if* signatures included (verify vs pinned peer key); none if encryption-only | ✅ Strong — Ed25519 over canonical inner envelope, verified after decrypt |
| 2 | Impersonation resistance | ❌ Anyone with the shared key (either Muse, anyone who learns the passphrase) can forge as **either** party | ✅ Forgery needs the victim's private signing key; relay / token thief / peer cannot forge as you | ✅ Same as B, plus: a compromised peer can forge only as *itself*, never as you |
| 3 | Compromised relay (read + active) | Reads ciphertext only (safe **iff** key has real entropy — a passphrase doesn't). Active: drops/replays/reorders undetectable; modification caught by GCM | Reads ciphertext only (safe: 256-bit keys). Active: modification caught (AEAD+sig); replay/drop mitigated by signed seq nos.; total suppression still possible | Same as B; per-message ephemerals additionally bound nonce-misuse damage structurally |
| 4 | Compromised send token | Attacker submits blobs; without the key they fail authentication → dropped. DoS/quota only. No forgery, no decryption. (Same in all three options — tokens are orthogonal to message keys.) | Same as A | Same as A |
| 5 | Compromised fetch token | Attacker downloads all stored ciphertext → **offline passphrase brute-force** (see §3.5). Likely total break. | Attacker downloads ciphertext; 256-bit keys → offline attack infeasible. Metadata only. | Same as B |
| 6 | Key rotation | ❌ Painful: shared secret must flip atomically on both sides; in-flight messages break; humans must coordinate; cannot rotate one party without touching the other | ✅ Per-party: encryption keys rotate via signed in-band rollover; signing keys via signed rollover + human confirm | ✅ Same as B; encryption keys additionally cheap to rotate often (even per-message ephemerals already) |
| 7 | Muse replacement (new VM, fresh state) | Humans re-provision the shared passphrase (simple, but re-exposes the weak secret every time) | New keypairs + in-person fingerprint ceremony; peer pins new keys (pin replacement) | Same as B; old keys die with the old instance — clean break |
| 8 | Revocation | ❌ Impossible per-party: revoking one Muse = rotating the shared secret for **both** | ✅ Pin replacement, human-mediated, per-party; no CRL/OCSP theater needed at this scale | Same as B |
| 9 | Offline attacks | ❌ **Fatal:** fast HKDF on a human passphrase = GPU brute-force/dictionary in seconds-to-hours | ✅ 128-bit security level; offline attack infeasible | ✅ Same as B |
| 10 | Metadata confidentiality | Relay sees timing/size/counts; **plus** direction/identities if envelope v1 keeps plaintext sender/recipient fields | Same timing/size/counts; direction hidden only if construction hides it (B as specified doesn't) | ✅ Timing/size-bucket/counts only — single shared mailbox, opaque outer envelope, sign-then-encrypt hides authorship; residual timing correlation remains (out of scope: cover traffic) |
| 11 | Forward secrecy | ❌ None | ❌ None (static keys) | ❌ None against recipient long-term-key compromise (see §4 — deliberate, with substitutes) |
| 12 | Is FS worth it here? | — | — | **No.** Position argued in §4. |

### 3.1 Sender authenticity (1) and impersonation (2) — A's structural ceiling

Symmetric AEAD is symmetric: anyone who can verify a message can also forge one. In A, "authentic"
means "someone holding the shared key said this," and **both** Muses (plus anyone who ever learns
the passphrase) hold it. There is no construction within pure symmetric crypto that lets Gauri's
Muse distinguish "Aman's Muse wrote this" from "someone with our shared key wrote this" — because
those are the same capability. If inter-Muse messages can trigger actions (and they will — that's
the point of the mailbox), per-sender authenticity is load-bearing, not cosmetic. Only asymmetric
signatures (B/C) provide it.

Blast-radius view: compromise **one** Muse in A and the attacker can forge as *either* party and
read all traffic. Compromise one Muse in C and the attacker can forge only as the compromised
party and read only traffic *addressed to* it — the honest party's identity stays unforgeable and
messages to the honest party stay confidential. C degrades gracefully; A collapses totally.

### 3.2 The passphrase problem (5, 9) — A's fatal flaw, and a general rule

HKDF is a fast KDF: it is designed to *extract* from already-high-entropy input, not to *stretch*
a human passphrase. Running a human-memorable passphrase through HKDF-SHA256 hands any holder of
the ciphertext (compromised relay, stolen fetch token, leaked backup) an offline brute-force
oracle — try candidate passphrase → HKDF → AES-GCM decrypt → valid tag means success — at
billions of guesses per second on a GPU. A typical human passphrase (~20–40 bits of real entropy)
falls in seconds to hours.

**Rule: never feed a human passphrase to a fast KDF.** Two acceptable fixes: (a) make the secret
high-entropy (256-bit random, exchanged in person via QR/written form — humans don't memorize it),
or (b) run the passphrase through a memory-hard KDF (Argon2id) with a random salt. Option C takes
route (a) structurally: there is **no passphrase anywhere in the message crypto path** — keys are
256-bit random, generated on-device. (If a passphrase survives anywhere, e.g. to unlock local key
storage, it must go through Argon2id, never HKDF.)

### 3.3 Compromised relay, active (3) — what signatures + sequence numbers buy

All three options detect *modification* (AEAD tags; C also has signatures). None can prevent the
relay from *dropping, replaying, or reordering* stored blobs — that's inherent to an untrusted
store-and-forward relay. What B/C add over A:

- **Replay detection:** a per-sender monotonic sequence number (`sender_seq`) inside the
  *signed* payload. The recipient keeps, per peer, a **contiguous cursor**
  (`process_cursor`): it dispatches only `sender_seq == process_cursor + 1` and advances
  strictly contiguously; out-of-order arrivals are buffered, never skipped;
  `sender_seq ≤ process_cursor` is a duplicate/replay and is dropped after logging. A naive
  `seq > last_seq` high-water mark is **rejected** — under out-of-order delivery it can
  permanently skip a message (e.g. receiving 10, 12, 11 and advancing the cursor past 11).
  Gaps that persist beyond the gap policy (frozen contract §6) raise an alert; they are
  never auto-skipped.
- **Freshness bounds:** a signed timestamp; reject absurd skew (e.g. >48h future, >30d past).
- **Suppression detection:** optional daily heartbeat message in each direction when idle; alert
  if silence exceeds ~48h. Total suppression is otherwise indistinguishable from "no messages."

A cannot do any of the above meaningfully: without per-sender authenticity, a sequence number is
just a number anyone with the shared key can write.

### 3.4 Tokens (4, 5) — orthogonal by design, keep them that way

In all three options, bearer tokens authenticate *to the relay*, message keys authenticate *to the
peer*. A stolen send token lets the attacker submit blobs that fail authentication and get dropped
(real residual: storage-filling DoS → mitigate with per-token quotas/rate limits and alerting on
auth-failure spikes). A stolen fetch token leaks stored ciphertext — which is exactly why the
ciphertext must resist offline attack (A fails here, C holds). Token rotation happens on the relay
control plane and never touches message keys; message-key rotation never touches tokens. Keep this
separation explicit in the design.

### 3.5 Rotation, replacement, revocation (6, 7, 8) — where asymmetric earns its keep

- **A:** rotation is a coordinated flag day. Both Muses must switch the shared secret atomically;
  in-flight messages under the old key fail; the humans must rendezvous. Revocation of one party
  is impossible without re-keying the other. Replacement re-exposes the passphrase.
- **B/C:** each party rotates independently. Encryption (X25519) keys: rotate freely and often —
  announce the new public key in a message **signed** by the long-term Ed25519 identity key
  (`key-rollover` type); the peer verifies the signature against the pinned signing key and pins
  the new encryption key, keeping the old one for a short grace window (e.g. 7 days) for in-flight
  messages. No human involvement needed when no compromise is suspected. Signing (Ed25519) keys:
  rotate rarely; the new signing key is announced signed by the old one, and the humans confirm
  the new fingerprint at the next in-person contact (or via a trusted call). **If compromise of
  the old key is suspected, never auto-accept a rollover** — require a fresh in-person ceremony.
- **Revocation** at this scale is not PKI: the trust model is *pinned peer keys*, so revocation =
  **human-mediated pin replacement**. The humans agree out-of-band that key K is dead; each updates
  their Muse's pinned config; relay bearer tokens are rotated at the same time. A signed
  `revoke` notice may also be published through the mailbox as an audit trail, but the pin
  replacement is what enforces it.
- **Replacement:** new instance → fresh keypairs → in-person fingerprint ceremony → peer pins the
  new keys. In-flight messages to the old keys are lost (acceptable at this volume; for a *planned*
  migration, keep the old private key briefly — but default to a clean break).

## 4. Forward secrecy — the verdict: **not worth a ratchet here**

**Position: do not build forward secrecy (no double ratchet, no Signal-for-two-people).**
This is a deliberate omission, not an oversight. Reasons:

1. **Endpoint compromise defeats FS anyway.** The threat FS addresses is *future* compromise of a
   long-term key exposing *past* ciphertext. But anyone who can exfiltrate a Muse's long-term
   private key from its cloud instance can almost certainly also read that instance's memory,
   logs, and plaintext. The marginal scenario FS protects — key stolen cleanly, with zero endpoint
   access, then applied to a relay archive — is narrow. Crypto cannot save you from an endpoint
   you don't control; don't pay Signal-level complexity for a property whose attacker model barely
   exists here.
2. **Async store-and-forward ratchets are genuinely complex.** A double ratchet assumes an
   interactive session. Over a polling mailbox you must additionally handle out-of-order delivery,
   skipped message keys, state loss on instance replacement, and session healing — all for
   low-volume traffic between two people who meet in person. That is textbook overengineering.
3. **Cheaper substitutes bound the same risk:**
   - **Delete-after-fetch + short server TTL** (e.g. 7 days): the relay barely holds an archive at
     all; there is little *stored* history for a future key thief to decrypt.
   - **Periodic encryption-key rotation** (monthly, or every few hundred messages, via the signed
     rollover in §3.5): bounds any key-compromise exposure to one rotation window — ~95% of FS's
     practical benefit at ~5% of the complexity.
   - The offline archive is already safe against everyone *except* a future private-key thief
     (256-bit keys; §3.2).

**Residual risk, stated honestly:** a *persistent* passive collector (stolen fetch token, or the
relay itself archiving everything for a year) who *later* steals a recipient's long-term X25519
private key can decrypt everything collected within the then-current rotation window — and, if
keys were never rotated, everything ever. Rotation + deletion shrink this; only a ratchet would
eliminate it. We accept this: the countermeasure's cost (async double ratchet) exceeds the
threat's likelihood × impact *in this deployment* (two humans, low volume, in-person re-keying
available). Revisit only if the threat model changes (high-value traffic, untrusted endpoints,
no in-person channel).

## 5. Recommendation: Option C — concrete specification

**Adopt Option C**: per-Muse Ed25519 identity/signing keys + per-Muse X25519 encryption keys,
per-message ephemeral-static ECIES, sign-then-encrypt, opaque outer envelope.

### 5.1 Algorithms and libraries (Python `cryptography` package — all available)

| Purpose | Algorithm | `cryptography` API |
|---|---|---|
| Identity / signing | **Ed25519** (deterministic nonces — no nonce management) | `cryptography.hazmat.primitives.asymmetric.ed25519` |
| Key agreement | **X25519**, ephemeral sender × long-term recipient | `...asymmetric.x25519` |
| KDF over ECDH output | **HKDF-SHA256**(salt=`eph_pub` (32 B), ikm=`shared` (32 B), info=`b"muse-mailbox/v2/ecies"`, L=32) | `cryptography.hazmat.primitives.kdf.hkdf.HKDF` |
| Bulk encryption | **AES-256-GCM**, 96-bit random nonce per message (`secrets.token_bytes(12)`); AAD = canonical-JSON UTF-8 bytes of the routing header `{v, mailbox_id, message_id, from, to, eph}` — binds every plaintext routing field the relay may see (§5.3) | `cryptography.hazmat.primitives.ciphers.aead.AESGCM` |

Notes: never use an X25519 shared secret directly — always through the KDF. Never reuse one
keypair for both DH and signing (separate Ed25519 and X25519 keypairs per Muse). Because each
message uses a fresh ephemeral key, even an accidental nonce reuse across messages is harmless
(different keys) — nonce-misuse resistance is structural, not just procedural.

### 5.2 Key inventory (whole system: 4 keypairs, 2 humans)

Per Muse: one long-term Ed25519 signing keypair (identity — rotate rarely), one long-term X25519
encryption keypair (rotate periodically). Each human's Muse config pins the **peer's two public
keys**. Private keys: generated on-device, file mode `0600` (or the platform secret store),
**never logged, never transmitted, never backed up insecurely**. Key IDs: first 8 bytes of
`SHA256(public_key)`, hex — used inside the signed payload for diagnostics.

### 5.3 Envelope v2

### Wire format (frozen)

Plaintext routing header — visible to the relay, required for directional token enforcement
(protocol doc §5.2): `v`, `from`, `to`, `message_id` (UUID v4, the idempotency key),
`sent_at` (RFC3339 UTC). Opaque crypto fields: `eph` (base64 of the 32-byte ephemeral
X25519 public key), `nonce` (base64, 12 bytes), `ct` (base64 AES-256-GCM ciphertext).

POST body:
```json
{"v": 2, "from": "aman", "to": "gauri",
 "message_id": "b3f1c2d4-…", "sent_at": "2026-09-15T16:27:00Z",
 "eph": "base64…", "nonce": "base64…", "ct": "base64…"}
```

Reconciliation note: this supersedes the earlier "fully opaque outer envelope /
trial-decrypt" sketch. Direction (`from`/`to`) is plaintext **by design** — it is what
makes server-side recipient isolation and directional credentials enforceable. Direction
confidentiality is deliberately traded away; content confidentiality and sender
authenticity are unaffected.

### AAD construction (exact) — binds every plaintext routing field

```python
aad_header = {
    "eph":        <base64 of 32-byte ephemeral X25519 public key>,
    "from":       <sender participant id, e.g. "aman">,
    "mailbox_id": <mailbox id, taken from the request path>,
    "message_id": <idempotency key, UUID v4 string>,
    "to":         <recipient participant id, e.g. "gauri">,
    "v":          2,
}
aad = json.dumps(aad_header, sort_keys=True, separators=(",",":"),
                 ensure_ascii=True).encode("utf-8")
```

The sender computes AAD over the values it submits. The receiver recomputes it from its
configured `mailbox_id` plus the received `v / from / to / message_id / eph`. Any relay
tampering with any of these six fields → AES-GCM `InvalidTag` on decrypt. The relay can
still drop or reorder (detectable via `sender_seq` gaps and heartbeats) but cannot silently
rewrite routing — this closes the unauthenticated-routing-metadata hole.

### Inner payload (signed, then encrypted, then padded)

- `inner` (JSON): `{"body": "…", "from": "aman", "from_sign_kid": "…", "to": "gauri",
  "to_enc_kid": "…", "message_id": "…", "sender_seq": 42, "sent_at": "…",
  "type": "message", "v": 2}` — `sender_seq` is per-sender monotonic from 1, independent
  of the relay's per-mailbox `seq`; `type` ∈ {`message`, `key-rollover`, `revoke`,
  `heartbeat`}; no floats (use ints/strings). Routing fields are duplicated inside the
  signed payload (defense in depth: end-to-end binding even if a future transport ever
  omitted AAD).
- **Canonical bytes for signing:** `json.dumps(inner, sort_keys=True, separators=(",",":"),
  ensure_ascii=True).encode("utf-8")` — byte-exact on both sides, specified once, never varied.
- `sig = Ed25519_sign(sender_sign_priv, canonical_bytes)` (64 B).
- `payload = sig ‖ canonical_bytes`; prefix 4-byte big-endian length; zero-pad to the smallest
  bucket in `[1024, 4096, 16384]`; reject bodies that exceed the top bucket.
- Encrypt the padded payload with AES-256-GCM (§5.1) under the AAD above.

### Receive path (two-cursor model; full algorithm in the frozen contract)

1. Trial-decrypt `ct` with the active and grace-window X25519 private keys. `InvalidTag` on
   all keys → quarantine the blob, alert, advance the fetch cursor past it (durable receipt)
   — never silently skip, never stall the stream on one bad blob.
2. Strip padding → split `sig ‖ inner` → verify Ed25519 against the **pinned** peer signing key.
3. Check inner `from`/`to`/`message_id` match the outer header; enforce timestamp skew bounds
   (reject >48h future, >30d past).
4. Dedupe by `message_id`; dispatch **only** in contiguous `sender_seq` order per peer —
   out-of-order arrivals are buffered, never skipped.

### 5.4 Provisioning ceremony (the root of trust — do it honestly)

1. Each Muse generates its two keypairs locally and displays the public keys + fingerprints
   (`SHA256(pubkey)` as grouped hex, or a word list).
2. The humans meet. Each hands the other their Muse's public keys — QR code preferred (encodes
   full keys; the in-person handoff is itself authenticated), with a spot-check of the fingerprint
   characters. **A rushed or skipped check = silent relay MITM from day one, undetectable after.**
3. Each human pastes the verified peer public keys into their Muse's pinned config.
4. Exchange test messages both directions; confirm receipt and signature verification.

### 5.5 Relay-protocol hygiene (separate from message crypto, still required)

- Separate send/fetch bearer tokens per Muse, TLS 1.2+ with normal CA validation (the relay is
  untrusted for *content*, not unauthenticated at the transport layer).
- Per-token rate limits + storage quotas; alert on authentication-failure spikes (someone is
  submitting garbage with a stolen send token).
- **Delete-after-fetch** (relay deletes a blob once the recipient acknowledges) + **server-side
  TTL** (~7 days) as backstop.
- Fixed-interval polling with jitter (e.g. every 5 min ± 60 s), decoupled from send timing;
  padding buckets (§5.3) hide message sizes. Residual metadata: timing/size-bucket/counts and
  send→fetch timing correlation — accepted (cover traffic is out of scope).
- Heartbeat `type` message per direction daily when idle; alert after ~48h silence (suppression
  detection).

### 5.6 Why not just "fix A"?

Two of A's deficits are unfixable within symmetric crypto no matter how strong the key is:
(1) **no sender authenticity** between the key holders (§3.1) — a stronger passphrase doesn't
create it; (2) **no per-party rotation/revocation** (§3.5). Fixing the passphrase (high-entropy
secret or Argon2id) repairs A's offline-attack weakness but leaves a system where either party
can forge as the other and one compromise is total. The step from "fixed A" to C is exactly the
asymmetric identities — which is the whole point.

### 5.7 Why not full Signal?

Deniable authentication, sealed sender, and the double ratchet solve problems this deployment
doesn't have (untrusted endpoints, no out-of-band channel, high volume, metadata-hiding from
active network observers). They would add interactive-session machinery atop an async polling
mailbox for no matched threat. Rejected as overengineering — see §4.

## 6. Residual risks of C (what the recommendation does NOT fix)

1. **No forward secrecy** — deliberate (§4). Mitigated by 30-day TTL and monthly rotation.
   (The investigation draft also named delete-after-fetch; that was later dropped as
   unimplementable — see `PROTOCOL.md` §5.)
2. **Endpoint compromise is total for that endpoint** — key theft ⇒ forge-as-me + read-my-inbound.
   Contained to the compromised party (§3.1); still, private-key custody is critical.
3. **Provisioning ceremony failure** — skipped fingerprint check ⇒ undetectable MITM from day one.
   The human step is the weakest link in practice.
4. **Relay suppression** — detectable only via seq gaps and heartbeats, not preventable.
5. **Transferable signatures** — Ed25519 gives the recipient cryptographic proof of authorship
   (non-repudiation). Between spouses: negligible concern; noted for completeness. (Deniable
   variants exist — rejected as unneeded complexity.)
6. **Implementation bugs** — canonicalization drift, nonce reuse, AAD mismatch, padding errors.
   The spec in §5.3 is written to be implemented literally; add known-answer tests and the
   existing round-trip / wrong-key / tamper / nonce-uniqueness tests, plus replay and rollover tests.

## 7. Migration from the v1 draft

Volume is low, so prefer a clean cutover: run the §5.4 ceremony, exchange test messages on v2,
drain any in-flight v1 messages, then disable the v1 passphrase path entirely and delete the
passphrase-derived key material. Do not run v1 and v2 indefinitely in parallel (two crypto
systems = two attack surfaces; and the v1 passphrase must not linger as a fallback decryption
path once v2 is live).
