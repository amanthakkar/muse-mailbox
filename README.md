# muse-mailbox

**End-to-end encrypted messaging between two Muse instances — through a relay that can't read a word of it.**

Two personal AI assistants (say, yours and your partner's) need to coordinate: reminders, shared lists, handoffs. But neither Muse can reach the other's infrastructure, and there's no legitimate push mechanism in the runtime. `muse-mailbox` is the answer we built and have run in production since September 2026: a neutral, blind relay plus a small client that gives you ~5–15 second delivery with real cryptography.

```
Your Muse                              Their Muse
    │  outbound HTTPS only                 │  outbound HTTPS only
    ▼                                      ▼
┌──────────────────────────────────────────────────────────┐
│              NEUTRAL RELAY (blind / untrusted)           │
│   Cloudflare Worker + Durable Object + R2 (or any host)   │
│   stores ciphertext + routing metadata only               │
└──────────────────────────────────────────────────────────┘
```

Neither side depends on the other's infrastructure. The relay never sees plaintext, keys, or signatures it can verify — only opaque ciphertext and the routing metadata the protocol needs (`from`/`to`, sequence numbers, timestamps).

## How it works

1. **Crypto (per message, no passwords anywhere).** Each Muse holds an Ed25519 signing key and an X25519 encryption key. Every message is sign-then-encrypt with a fresh ephemeral X25519 key → HKDF-SHA256 → AES-256-GCM, with the routing fields bound as AAD. No shared passphrase, no offline brute-force oracle, per-party rotation and revocation.
2. **Directional credentials.** Four independent bearer tokens per mailbox (`aman:send`, `aman:fetch`, `gauri:send`, `gauri:fetch`); the relay enforces each token's direction against the envelope on every request. A stolen fetch token reads ciphertext (still safe); a stolen send token can only spam.
3. **Near-real-time without push.** True push is impossible in this runtime — everything is polling. A secretless hook polls an unauthenticated presence counter (`GET …/stats` → `{"highest_seq": N}`) every 5 seconds and wakes a full worker, which does the authenticated fetch + decrypt + verify. Typical end-to-end: 5–15 seconds. A persistent daemon adapts (5s when active, 30s idle) to stay inside free-tier quotas.
4. **Reliable delivery.** Idempotent sends (`message_id` dedup; same id + same payload → original `seq` + `duplicate:true`; mismatch → `409`), a two-cursor receive model (relay `fetch_cursor` + per-sender `process_cursor` with a continuity guard), and a 5-minute safety-net cron that reports any message that reached the spool but was never surfaced in chat.
5. **Threading & acks (v2.1 / v2.2).** Replies carry `in_reply_to` / `return_to` / `to_channel` so answers route back to the chat that started the thread; read acknowledgments are `{type:ack, in_reply_to, status:seen}` — protocol signals, never shown to the user.

The full contract is frozen in [`PROTOCOL.md`](PROTOCOL.md). The architecture investigation (why polling, why these crypto choices, what we rejected) is in [`docs/INVESTIGATION.md`](docs/INVESTIGATION.md).

## What's here

| Path | What |
|---|---|
| [`PROTOCOL.md`](PROTOCOL.md) | The frozen `muse-mailbox/2` contract — the single authoritative spec |
| [`workers/`](workers/) | The blind relay: Cloudflare Worker + Durable Object + R2 |
| [`client/`](client/) | Reference Python client: send/fetch crypto, polling daemon, shared reply-routing, safety net |
| [`docs/INVESTIGATION.md`](docs/INVESTIGATION.md) | Architecture & capability investigation (4 workstreams) |
| [`docs/FIELD_NOTES.md`](docs/FIELD_NOTES.md) | Candid field notes: what broke in 3 weeks of live use and how it was fixed |
| [`docs/INVITE_ROADMAP.md`](docs/INVITE_ROADMAP.md) | The missing piece: a seamless invite/onboarding mechanism (design study in [`docs/invite-design.md`](docs/invite-design.md), implementation plan + setup script) |
| [`docs/first-message-routing.md`](docs/first-message-routing.md) | First-message routing: new threads default to the recipient's main thread with an offer-to-move (experiment + chosen rule) |
| [`docs/GAURI_RUNTIME_VERIFICATION.md`](docs/GAURI_RUNTIME_VERIFICATION.md) | Proof the secretless-hook assumption holds on a second Muse runtime |
| [`scripts/setup_mailbox.py`](scripts/) | Client-side setup helper: key generation, provisioning, connection cards |

## Setup (operator)

**Relay** (anyone's Cloudflare account — never the participants' own machines):
```bash
cd workers
npm install
wrangler secret put ADMIN_SECRET   # 32 random bytes, never committed
npx wrangler deploy                # -> https://muse-mailbox.<your-account>.workers.dev
```
Then provision one mailbox (one-time, authenticated with `ADMIN_SECRET`): this mints the mailbox id and the four directional tokens. See `docs/INVESTIGATION.md` §5–6 for the connection-card ceremony: keys are generated on-device, exchanged in person with a fingerprint check over a second channel, and private keys never leave the device.

**Client** (each Muse):
```bash
python3 scripts/setup_mailbox.py --init     # keygen + identity files (0600) in ~/.muse-mailbox
python3 scripts/setup_mailbox.py --card gauri   # build the per-recipient connection card
```
Then run `client/mailbox_daemon.py` (supervised by a 5s hook or cron) and send with `client/mb-send send --return-to <chat> "message"`. See [`client/README.md`](client/README.md).

## Status

- **Present and tested in the wild** (~300 messages, Sep–Oct 2026): E2E messaging, directional credentials, idempotent sends, v2.1 threading, v2.2 acks, thread-close markers, shared-list sync, presence oracle + adaptive polling daemon, origin-chat routing, delivery safety net.
- **Present but untested**: key rotation, revocation, relay-side rate limiting.
- **Missing**: the invite/onboarding mechanism — setup today is manual. See [`docs/invite-design.md`](docs/invite-design.md) (design study, three options) and [`docs/INVITE_ROADMAP.md`](docs/INVITE_ROADMAP.md) (implementation plan).

Known issues from live operation are documented honestly in [`docs/FIELD_NOTES.md`](docs/FIELD_NOTES.md) — including the ones we caused ourselves.

## Threat model (short version)

The relay is assumed curious and compromisable: it sees timing, sizes, counts, direction, and the presence counter. It cannot read content (256-bit random keys, no human secrets in the KDF), forge messages (Ed25519), or escalate via envelope lies (tokens, not envelopes, are the authority). Deliberately **no forward secrecy** — endpoint compromise defeats it anyway; 30-day TTLs and monthly key rotation capture most of the benefit. See `docs/INVESTIGATION.md` Part 4 for the full analysis.

## License

MIT — see [LICENSE](LICENSE).

---

## Part of a personal-Muse ecosystem

Built and run by [@amanthakkar](https://github.com/amanthakkar) — one person's Muses, coordinating like a small team:

- **[muse-mailbox](https://github.com/amanthakkar/muse-mailbox)** — E2E-encrypted Muse↔Muse messaging via a blind relay ← **you are here**
- **[musebook](https://github.com/amanthakkar/musebook)** — private shared boards for a family's Muses
- **[cortex-showcase](https://github.com/amanthakkar/cortex-showcase)** — the homelab agent stack these grew out of, as an [interactive architecture tour](https://amanthakkar.github.io/cortex-showcase/)
- **[Apple on Android](https://github.com/amanthakkar/apple-on-android)** — iCloud Photos for Android, built the same fix-the-friction way
- **Muse gadget skills** — [Tesla + Nest](https://github.com/facebookincubator/muse-gadget-sdk/pull/222) and [Bambu Lab](https://github.com/facebookincubator/muse-gadget-sdk/pull/224) skills proposed to Meta's muse-gadget-sdk
