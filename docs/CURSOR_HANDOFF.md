# CURSOR HANDOFF — neutral encrypted Muse↔Muse mailbox (muse-mailbox/2)

**From:** Anton (Aman's Muse) · **To:** Cursor (Pi ops lead)
**Date:** 2026-09-16 · **Authorized by:** Aman, 2026-09-16 ("hand the frozen contract to Cursor")
**Both handoff gates are GREEN:**
1. Gauri's runtime verification PASSED (2026-09-15): secretless 5s hook wakes a full
   worker on her actual Muse runtime; measured wake latency 9s; silence control clean;
   no residue. Full result: `GAURI_RUNTIME_VERIFICATION.md` (in this repo).
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

## Delivery log (2026-09-16)

Delivered via relay `POST /ask` (`source=hatch-anton`, `audience=cursor`): the relay
rejects prompts over ~2990 chars, so the handoff went as 15 numbered parts + manifest.
Part 1 = cover/scope above; parts 2–12 ≈ frozen contract full text; parts 13–15 ≈ Gauri
verification result; manifest lists all part IDs for reassembly.

- Part IDs: 1987f8b4, 8f6ba86d, 8777a11d, 4296ee5b, 0b902106, ebd81d39, e5401550,
  7201cf17, 38cbd3bf, 488d4134, ae83b45b, 73c69d6e, 823801d2, 76e32c8f, e8d5eabb
  (titles `mailbox-handoff [1/15]` … `[15/15]`)
- Manifest ask: 3f249254 (`mailbox-handoff [manifest]`)
- Schema-probe asks created during delivery were answered/resolved; none left pending.
- Cursor replies arrive via `cursor_chat` ingest → cursor-watch hook → "Wit & Pi Homelab"
  side chat → Aman notified.

## Delivery receipt — production Worker code (2026-09-16, ~02:20 IST poll)

Cursor sent 10 `cursor_chat` items: one ack (`b5876fb7-…`) + 9 code parts
(`[workers 1/9]`…`[workers 9/9]`, item ids `e746af56-…` … `96e9e3c4-…`).

**Ack content (alignment now confirmed, resolving the earlier "Cloudflare with Gauri"
conflict):** operator = **Aman's existing CF account** (guest model, not joint —
account `<your-cloudflare-account-id>`); deploy path = Cursor does NOT deploy
from the Pi, Anton deploys from Hatch after Aman confirms; custody = file-based
0600, provision tokens redirected to Hatch files, only URL + mailbox_id reported
over the relay. Explicit: **do not deploy until Aman confirms; Gauri still gated.**

**Code verified:** 9 parts reassembled in order into `~/workspace/secure-mailbox/workers/`
(7 files: `.gitignore`, `package.json`, `package-lock.json`, `tsconfig.json`,
`wrangler.toml`, `src/index.ts`, `src/mailbox-do.ts`). All 6 chunk boundaries join
byte-exactly (`submit(mailboxId`, `await`, `stale_timestamp`, `kept.push`,
`encode(s))`, …). `npm install` + `tsc --noEmit` (workers-types 4.20240909.0):
exit 0. sha256: mailbox-do.ts `87146675…0e092`, index.ts `4810bec2…3a3ba`,
wrangler.toml `cb63263b…7ae9`. Protocol spot-checks: `muse-mailbox/2` in `/health`,
singleton DO `idFromName("muse-mailbox/2")`, 4 directional `mm1_*` token prefixes,
idempotency 409s (provision-already-exists + message_id reuse), 300s sent_at skew →
422, 30-day TTL prune + 100k surplus cap, blind relay (envelope metadata in DO,
ciphertext in R2 only), constant-time admin secret compare. `/stats`
unauthenticated by design (presence-oracle metadata, per handoff §2).

**NOT done / still owed:** `docs/ANTON_DEPLOY.md` was promised in the ack but not
delivered. Nothing deployed: no `wrangler deploy`, no R2 bucket created, no secrets
set. Receipt ack sent back to Cursor via relay ingest (`b19b8232-…`).

## Delivery receipt — ANTON_DEPLOY runbook (2026-09-16, ~02:33 IST poll)

Cursor sent 2 `cursor_chat` items: `[ANTON_DEPLOY 1/2]` (id `3974ed9e-…`) and
`[ANTON_DEPLOY 2/2]` (id `71f4c726-…`). This is the production Worker deploy
runbook that was "still owed" from the ~02:20 delivery — all 7 owed deliverables
(RAISE errata, substrate verdict, scratch tests, custody design, production port,
wrangler config, deploy steps) are now in.

**Runbook confirms alignment (operator FLAG resolved):** guest model — Aman's
existing CF account (`<your-cloudflare-account-id>`,
`<your-account>.workers.dev`); Cursor does NOT deploy from the Pi; Anton deploys
from Hatch **only after Aman confirms**; blind relay (E2E confidentiality
unaffected); Gauri never needs Cloudflare credentials; Gauri's hook/runtime still
gated.

**Setup order (Hatch, post-confirm):** (1) R2 subscription — DONE (free, active);
(2) create bucket `muse-mailbox-blobs`; (3) workers/ tree on Hatch — code already
received/verified above (`~/workspace/secure-mailbox/workers/`, tsc clean);
(4) `wrangler secret put ADMIN_SECRET` — 32-byte random, Hatch file 0600, never
chat/git; (5) `npx wrangler deploy` → expect
`https://muse-mailbox.<your-account>.workers.dev`; (6) `curl …/health` →
`{"ok":true,"protocol":"muse-mailbox/2"}`; (7) provision once →
`$HOME/.muse-mailbox/provision.json` 0600 (re-run → 409); (8) split tokens —
Aman's to `card.json`, Gauri's to `held-gauri-tokens.json` held until the
in-person ceremony, **never sent to Gauri's Muse yet**; (9) report over the relay
safe fields only (Worker URL + `mailbox_id`); (10) confirm with Cursor.

**wrangler.toml spec (as delivered):** `account_id = <your-cloudflare-account-id>`;
DO class `MailboxDO`, binding `MAILBOX`; R2 binding `BLOBS` →
`muse-mailbox-blobs`; singleton DO name `muse-mailbox/2`.

**NOT done:** nothing deployed yet — no bucket, no secrets, no `wrangler deploy`.
Deploy is gated on Aman's confirmation. Receipt ack sent back to Cursor via relay
ingest.
