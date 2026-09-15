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
