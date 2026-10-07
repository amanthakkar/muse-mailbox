# Neutral Encrypted Muse-to-Muse Mailbox — Architecture & Capability Investigation

> **Post-review patches (2026-09-15):** three review findings were adopted after this
> investigation completed: (1) AAD now binds all six plaintext routing fields
> (`v, mailbox_id, message_id, from, to, eph`) — the old `AAD = domain ‖ eph_pub` language
> is superseded; (2) the naive `seq > last_seq` cursor is replaced by the two-cursor
> model (`fetch_cursor` over relay seq + contiguous `process_cursor` over per-sender
> `sender_seq`); (3) the connection-card protocol version is bumped to
> `muse-mailbox/2`. The authoritative spec is now **`PROTOCOL.md`** (frozen contract);
> this document remains the investigation record and rationale.

**Date:** 2026-09-15 · **Status:** investigation only — nothing implemented, no Pi/SSH/Cloudflare changes, no cloud accounts created, no secrets stored.
**Goal:** `Aman Muse ←→ encrypted neutral mailbox ←→ Gauri Muse`, where neither Muse depends on the other's infrastructure.
**Method:** four parallel workstreams — (1) runtime capability research from docs/schemas, (2) empirical network experiments from this VM, (3) cryptographic analysis, (4) protocol design. Detail lives in `parts/`; this document synthesizes.

---

## Headline verdict

- **True push is NOT possible.** There is no legitimate mechanism — inbound webhook, event subscription, skill trigger, connector stream, WhatsApp injection, or OS push — by which an external event can wake a Muse instance. Every background path in this runtime is polling. The VM additionally has no public IP, so nothing can reach it inbound anyway.
- **Effectively immediate (≈5–15 s) IS possible, outbound-only.** A hook polling an *unauthenticated presence oracle* every 5 s wakes a worker, which then performs the authenticated fetch + decrypt with its own session. Seconds, not minutes — without any inbound networking.
- **Long-poll/SSE/WebSocket are network-viable but runtime-orphaned.** A ~101 s near-idle outbound HTTPS hold, SSE consumption, and a WebSocket echo were all verified through the egress proxy. But there is no supported *persistent holder* for such a connection (hook scripts can't hold secrets; no resident-worker primitive), so they are not the recommended path.
- **Smallest realistic latency: 5–15 seconds** (presence-oracle hook + worker fetch). Cron-only fallback: minutes.

---

## Part 1 — The most important question: can we avoid polling?

**Answer: no — but we can get to seconds without inbound.**

### Capability classifications (evidence-quoted)

| Capability | Verdict | Evidence |
|---|---|---|
| External HTTPS invokes a worker / skill / message injection / goal resume | NOT SUPPORTED | No inbound API in docs or schemas; `~/docs/channel-availability.md`: "You cannot push a one-off message into a provider conversation from any other chat." |
| Hook at 5 s poll interval | VERIFIED | `hooks.add` schema: `poll_interval_secs` `"minimum": 5`; "a Bash polling script… polls frequently… wakes an agent only when something relevant happens." |
| Hook script blocking/long-poll waiting | UNVERIFIED | `script_timeout_secs` allows up to 600 and outbound HTTPS works, but docs only describe "bounded fetches" (`curl … --max-time 10`) — no wait pattern documented or endorsed. |
| Authenticated mailbox polling from a hook script | NOT SUPPORTED | Hook scripts run "without secrets or connector credentials" — no decryption key or bearer token available in that environment. |
| Cron sub-minute interval | PARTIALLY VERIFIED | Schema accepts `55s`, but `~/docs/scheduling-and-watching.md`: "Timing is loose: the check fires around its scheduled time, give or take minutes." |
| Skills triggered by external events / resident skills | NOT SUPPORTED | Skills are `SKILL.md` files loaded inside an already-running session; no listener, registration, or scheduler exists. |
| Connectors: event streams, subscriptions, notify-on-new-data | NOT SUPPORTED | `~/docs/connectors.md`: "If a command is not documented, it does not exist." Grep of all bundled skills found no such mechanism. |
| WhatsApp: external app injects into Muse's WhatsApp conversation | NOT SUPPORTED | `~/docs/channels/whatsapp.md`: "only carries messages between the user and Muse", 1:1 only, no groups. |
| WhatsApp: external event notifies the user's normal WhatsApp in a Muse-consumable way | NOT SUPPORTED | No access to the user's personal WhatsApp account. (The 1 s inbound cadence exists but only for user↔Muse channel messages.) |
| Worker receiving a push that wakes the runtime | NOT SUPPORTED | `~/docs/calls-texts-notifications.md`: "There is no way to send an ad-hoc raw push." Pushes go to the user's phone only; no agent-wake path. |
| Goal "watching" by itself | NOT SUPPORTED | `~/docs/goals.md`: "A goal by itself does not run anything." |
| Goal monitoring cadence | VERIFIED (polling) | `scheduling-and-watching.md`: "How it works: **polling, not watching**… The same is true for event-based automations: they also work by polling, **not live streams**. Never promise detection 'the moment it happens.'" |
| Inbound internet reachability of this VM | NOT SUPPORTED | No public IP (127.0.0.1 + 198.19.0.2/30 CGNAT); cloudflared quick-tunnel blocked (verified twice). |
| Outbound HTTPS from background jobs | VERIFIED | Hook scripts run with "the same base command environment and proxy setup as an ordinary shell command." |

### The design consequence

The mailbox must be **pull-only from the Muse side**. The encrypted-mailbox design hits one hard wall: hook scripts have no secrets, so the 5 s hook **cannot** do the authenticated fetch itself. The supported shape is therefore two-tier:

1. **Hook (5 s):** polls an *unauthenticated presence oracle* — e.g. `GET /v1/mailboxes/{id}/stats` returning only `{"highest_seq": N}`. No secrets needed; leaks only a message counter.
2. **Worker (on wake):** performs the authenticated `GET …/messages?since=N` with the fetch token, decrypts, verifies, and acts. The worker runs in a full session and can reach approved secret storage.

Latency: poll interval (≤5 s) + fetch + worker launch ≈ **5–15 s typical**.

---

## Part 2 — Outbound-only options compared (A–G)

| Option | Expected latency | Resource cost | Reliability | Offline behavior | After VM restart | Complexity | Persistent worker? | Supported today? |
|---|---|---|---|---|---|---|---|---|
| A. Fixed polling (hook, 5 s, presence oracle) | 5–15 s | Negligible (tiny GET) | High (runtime-managed) | Cursor catch-up, nothing lost | Hook persists; resumes | Low | No | **Yes — recommended** |
| B. Adaptive polling | 5 s–minutes, variable | Low | Medium (more moving parts) | Same as A | Same as A | Medium | No | Yes, but unnecessary at this volume |
| C. HTTP long-polling | Seconds *while held* | Low per conn | Medium (reconnect logic) | Gaps between holders | No holder survives | Medium | **Required — none supported** | Network yes, holder no |
| D. SSE | Seconds *while held* | Low per conn | Medium | Same as C | Same as C | Medium | Required — none supported | Network yes, holder no |
| E. WebSocket | Seconds *while held* | Low per conn | Medium | Same as C | Same as C | Higher (no stock client on VM) | Required — none supported | Network yes (hand-rolled client), holder no |
| F. Cron background task | Minutes (±jitter) | Low | High | Catch-up on next run | Cron persists | Low | No | Yes — fallback only |
| G. Presence oracle + 5 s hook + worker fetch | **5–15 s** | Negligible | High | Cursor catch-up | Hook persists | Low | No | **Yes — recommended** |

**Why C/D/E lose despite verified networking:** the experiments proved the *network* path (101 s idle hold, SSE stream of 761 events/20 s, WS echo in 0.11 s, background processes surviving 5 min). What doesn't exist is a *runtime-legitimate persistent holder*: hook scripts are documented as bounded-fetch pollers without secrets, and there is no resident-worker primitive. A cron-spawned "holder agent" that long-polls for its lifetime is conceivable but fragile and unsupported — and it still couldn't hold the fetch token securely across the model the docs describe. **G gives the same seconds-scale latency with fully supported primitives.**

**Desired flow, end-to-end (seconds):** Aman sends → message durable on relay → Gauri's hook sees `highest_seq` move (≤5 s) → worker wakes → authenticated fetch → signature verify → decrypt → process → surfaces in her normal Muse experience. Symmetric in reverse.

---

## Part 3 — Neutral mailbox architecture

```
Aman Muse                    Gauri Muse
    |  outbound HTTPS only       |  outbound HTTPS only
    v                            v
+--------------------------------------------------+
|            NEUTRAL RELAY (untrusted/blind)        |
|  Cloudflare Worker + KV/R2, or tiny VPS, or equiv |
|  (operator: anyone — NOT Aman's Pi, NOT Gauri's)  |
+--------------------------------------------------+
```

- **Neither Muse depends on the other's infrastructure.** Each is a pure outbound client of the relay. Onboarding is via the in-person connection card (Part 6), not via anyone's Pi/tunnel/credentials.
- **Relay implementations are interchangeable** because the protocol (Parts 5–7) is standardized. Candidates: Cloudflare Worker + KV, small VPS, any public HTTPS service. Cloudflare is not mandatory.
- **The relay knows:** mailbox ID, sequence numbers, `from`/`to` routing metadata, ciphertext, signatures (opaque), timestamps, scoped bearer tokens. (Plaintext `from`/`to` is deliberate: it is what makes server-side recipient isolation and directional credentials enforceable.)
- **The relay never knows:** plaintext, encryption/signing private keys, passphrases, conversation context. It never decrypts or verifies signatures.
- **Metadata accepted as exposed:** timing, size buckets, counts, direction, send→fetch correlation. Cover traffic is out of scope.

---

## Part 4 — Cryptographic design: **Option C recommended**

**Adopt C: per-Muse Ed25519 identity/signing keys + per-Muse X25519 encryption keys, sign-then-encrypt per message with a fresh ephemeral X25519 key → HKDF-SHA256 → AES-256-GCM.** No passphrase anywhere in the message path.

### Why not A (shared passphrase → HKDF → AES-GCM)

A's fatal flaw is not AES-GCM — it's feeding a **human passphrase through a fast KDF**, handing any ciphertext holder (compromised relay, stolen fetch token) an offline GPU brute-force oracle. Worse, two deficits are *unfixable* in symmetric crypto: **no sender authenticity** between key holders (anyone who can verify can forge — either Muse can forge as the other), and **no per-party rotation/revocation** (one compromise is total; re-keying is a coordinated flag day).

### Why not B (naive static asymmetric)

B's identities are right but its usual construction (static–static ECDH, unspecified details) is underspecified. **C is B done properly** — B's keypairs plus a fully specified per-message ephemeral construction — and strictly dominates it.

### The twelve questions (C's answers)

- **Sender authenticity / impersonation:** Ed25519 over the canonical inner envelope, verified against the pinned peer key. Forgery requires the victim's private signing key; a compromised peer can forge only as itself.
- **Compromised relay (read + active):** reads ciphertext only (256-bit keys — offline attack infeasible). Modification caught by AEAD+signature. Drop/replay/reorder mitigated by signed per-sender sequence numbers + timestamps + heartbeats; total suppression detectable only via seq gaps/silence alerts.
- **Compromised send token:** attacker submits blobs that fail authentication → dropped. Spam/quota abuse only; **no forgery** (no signing key), **no decryption**.
- **Compromised fetch token:** attacker downloads retained ciphertext — safe under C (infeasible offline attack), fatal under A. Metadata only.
- **Rotation:** encryption keys rotate in-band via messages signed by the long-term Ed25519 identity key (7-day grace window for in-flight); signing keys rotate rarely with human fingerprint confirmation. Compromise-suspected rotation requires a fresh in-person ceremony — never auto-accept.
- **Replacement:** new instance → fresh keypairs → in-person fingerprint ceremony → peer pins new keys. Clean break.
- **Revocation:** pinned-key model — revocation = human-mediated pin replacement (+ token rotation). No PKI theater at this scale.
- **Offline attacks:** infeasible (256-bit random keys, never a human secret through a fast KDF).
- **Metadata confidentiality:** relay sees timing/size-bucket/counts/direction. Outer envelope carries only `{v, eph_pub, nonce, ct}` — no plaintext identities beyond the routing fields the protocol needs.
- **Forward secrecy:** deliberately **not** built (no double ratchet). Position: endpoint compromise defeats FS anyway; async ratchets over a polling mailbox are genuine complexity; cheaper substitutes — **30-day relay TTL and monthly encryption-key rotation** — capture most of the practical benefit. (The investigation draft also named delete-after-fetch; dropped as unimplementable — see `PROTOCOL.md` §5.) Residual risk (a persistent collector who later steals a private key decrypts that rotation window) is accepted consciously.

### Concrete construction (Python `cryptography`)

- Ed25519 signing; X25519 ECDH (ephemeral sender × long-term recipient); HKDF-SHA256(salt=`eph_pub`, info=`b"muse-mailbox/v2/ecies"`); AES-256-GCM with fresh 96-bit nonce per message, AAD binding the ephemeral key.
- Inner payload: canonical JSON (`sort_keys`, `separators=(",",":")`), `sig ‖ inner`, length-prefixed, zero-padded to buckets {1024, 4096, 16384}; per-sender monotonic `seq`; RFC3339 timestamps; types {message, key-rollover, revoke, heartbeat}.
- Receive: trial-decrypt → verify signature vs pinned key → check `to`/`from` → enforce `seq > last_seq` → skew bounds → dispatch.
- **Migration:** clean cutover from the v1 passphrase draft — ceremony, test messages, drain, then disable v1 entirely. Never run both in parallel.

---

## Part 5 — Authorization: directional credentials

Four opaque 256-bit bearer tokens per mailbox, **independent of the message crypto keys**:

| Credential | Grants |
|---|---|
| `AMAN_SEND` | POST only; envelope must be `from: "aman"`, `to: "gauri"` |
| `AMAN_FETCH` | GET only; rows filtered to `to: "aman"` in the query |
| `GAURI_SEND` | POST only; envelope must be `from: "gauri"`, `to: "aman"` |
| `GAURI_FETCH` | GET only; rows filtered to `to: "gauri"` in the query |

- The relay checks the token's `(participant, direction)` binding **against the envelope on every request** — lying in the envelope cannot escalate, because the token (not the envelope) is the authority.
- **Blast radius:** stolen fetch → reads retained inbound ciphertexts only (content still safe under C); stolen send → spam/queue-poisoning only, forgeries detectable via signatures; neither direction's theft yields the other's capability; all four stolen → ciphertext + metadata, never content.
- Rotation is relay-side and atomic; message keys untouched. Rate limits per token bound abuse.

---

## Part 6 — Connection card

Per-recipient card (never all four credentials in one card), exchanged **in person**:

```json
{
  "protocol": "muse-mailbox/1",
  "card_id": "uuid",
  "issued_at": "2026-09-15T16:30:00Z",
  "relay_url": "https://relay.example.org",
  "mailbox_id": "mb_9f2e1a",
  "owner": "gauri",
  "peer": "aman",
  "credentials": { "send": "mm1_gs_…", "fetch": "mm1_gf_…" },
  "keys": {
    "aman":  { "encryption_public_key": "<base64 raw 32B X25519>", "signing_public_key": "<base64 raw 32B Ed25519>", "fingerprint": "8A3F 1C9D …" },
    "gauri": { "encryption_public_key": "…", "signing_public_key": "…", "fingerprint": "44B2 90EE …" }
  }
}
```

- **In the card:** protocol version, card_id, issued_at, relay_url (https), mailbox_id, owner, peer, the owner's own send+fetch pair, both participants' public keys (pinned base64-of-raw-32B encoding), both fingerprints.
- **Exchanged separately:** fingerprint confirmation over a second channel (giver reads the first 32 hex chars aloud; receiver compares against its own recomputation) — the card alone catches corruption, the second channel catches whole-card substitution.
- **NEVER exported:** either party's private keys, the other participant's credentials, relay admin secrets. (No shared secret exists in this design at all.)
- **Fingerprint:** `SHA-256("muse-mailbox/1" ‖ 0x00 ‖ mailbox_id ‖ 0x00 ‖ participant_id ‖ 0x00 ‖ 32B enc key ‖ 0x00 ‖ 32B sig key)`, displayed as 16 groups of 4 uppercase hex; first 8 groups for the verbal check. Binding mailbox_id + participant_id prevents transplanting fingerprints across mailboxes.
- **Verification by the receiving Muse:** schema/version check → recompute fingerprints from card key bytes (catches corruption) → human confirms spoken prefix (catches substitution) → credential sanity → optional non-destructive liveness probe (`GET …?since=<huge>&limit=1` expecting empty 200; never send a test message).
- **Key replacement:** routine rotation in-band via signed `key_rotation` control messages (old signing key vouches for new); emergency (signing key compromised) requires a fresh in-person card — in-band rotation is unsafe then.

---

## Part 7 — Mailbox semantics (minimal: 2 operations)

| Operation | Verdict |
|---|---|
| `POST /v1/mailboxes/{id}/messages` | **Keep** — the only submission path; idempotent via `client_message_id` (same id + same payload → original `seq` + `duplicate:true`; same id + different payload → `409`) |
| `GET /v1/mailboxes/{id}/messages?since=N&limit=M` | **Keep** — the only receive path; cursor-paginated; relay filters `to == token participant`; response carries `highest_seq` + `next_since` (the cursor **is** the ack) |
| `POST …/ack` | Rejected — the `since` cursor is the acknowledgment |
| `GET …/status` | Rejected — `highest_seq` in every fetch response already shows lag |
| `DELETE …/messages` | Rejected — deletion is the relay's retention job (30 days / 100k messages, then prune); clients must not rewrite shared history |

Plus the **presence oracle** (Part 1 consequence): `GET /v1/mailboxes/{id}/stats` → `{"highest_seq": N}`, unauthenticated, for the 5 s hook. Mailbox IDs must be unguessable (≥128-bit random).

Relay-enforced invariants: direction binding per §5; durable monotonic per-mailbox `seq` (gaps permitted, never reused); idempotent submission; bounded sizes (request ≤256 KiB, ciphertext ≤200 KiB, pages ≤200 msgs / ≤1 MiB); retention 30 d / 100k; layered replay protection (relay: ±300 s `sent_at` window + `client_message_id` dedupe; receiver: signature verify + seen-nonce set); relay blindness (validates metadata only); HTTPS only, tokens in `Authorization` header only. Full error table (`400/401/403/404/409/413/422/429/500`) in `parts/part567-protocol.md`.

---

## Recommended architecture

Superseded by the frozen contract — see [`../PROTOCOL.md`](../PROTOCOL.md) (§1–§7).
This document remains the rationale record: *why* these choices, and what was rejected.

---

## Open questions

All six were resolved during the Sep 15–16 build (relay operator, runtime parity —
proven GREEN above, key custody, presence-oracle exposure accepted, v1 superseded,
tunables at build time). Nothing open.

---

## Top 3 risks

1. **The provisioning ceremony is the weakest link.** A rushed or skipped fingerprint check means silent relay MITM from day one, undetectable afterwards. The human step must be done honestly.
2. **Private-key custody on the Muse instances.** Endpoint compromise is total for that endpoint (forge-as-me, read-my-inbound) under any option — keys must be generated on-device and never leave it.
3. **Accepted residual exposures must stay conscious:** no forward secrecy (deliberate; mitigated by 30-day TTL and monthly encryption-key rotation) and relay-visible metadata (timing, counts, direction, plus the new presence oracle). Fine for two humans — revisit if the threat model changes.

---

## Evidence index

- [part1-runtime.md](parts/part1-runtime.md) — capability classifications with quoted doc/schema evidence
- [part2-network.md](parts/part2-network.md) — exact commands + outputs: 101 s idle hold, SSE, WebSocket echo, 5-min process survival
- [part4-crypto.md](parts/part4-crypto.md) — A vs B vs C across all 12 questions, v2 envelope spec, provisioning/rotation/revocation procedures
- [part567-protocol.md](parts/part567-protocol.md) — directional credentials, connection-card schema, mailbox semantics, error table
