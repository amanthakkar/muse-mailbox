# Neutral operator + what we still need

## Operator recommendation

**A new Cloudflare account, created together, not the homelab account, not the Pi.**

- Product: Worker + one Durable Object + one R2 bucket.
- Hostname: `*.workers.dev` until there is a dedicated domain that is **not** `amanthakkar.com`.
- Billing: whoever is willing; both humans hold the login (shared password-manager item).
- API tokens: two scoped tokens if possible (deploy vs read-only), neither stored in a Muse vault.
- **Never** route this through the Pi tunnel, Cortex, or `anton.amanthakkar.com`.

A tiny VPS running the Python harness is a valid fallback if Cloudflare is the wrong political home. Worse TLS/DDoS story; same protocol.

Do **not** create the account until Aman says so. This repo does not contain credentials.

## Need from Aman before production could even be considered

1. Approve or reject the **RAISE** list in `docs/CONTRACT_REVIEW.md` (especially empty-page `fetch_cursor` jump, payload-hash fields, drop delete-after-fetch SHOULD, body-size numbers).
2. Confirm Hatch: a worker woken by a secretless hook can read named Secure Vault items (`muse-mailbox/identity`, `muse-mailbox/card-gauri`).
3. Choose operator: dedicated CF account (recommended) vs tiny VPS. If CF: create it with Gauri, not from this Pi.
4. Retention/heartbeat tunables, or accept defaults: 30-day TTL, 100k cap, daily heartbeat, padding buckets `[1024,4096,16384]`, 60 POSTs/min, 300 GETs/min, gap alert 24h / 50 seqs.
5. In-person availability for the fingerprint ceremony **after** both Muses have generated keys (not yet).

## Need from Gauri’s Muse (via Aman / Gauri — no direct channel)

1. **Explicit go-ahead to build on her runtime.** Still closed. Verification ≠ consent.
2. Acknowledgement of presence-oracle metadata leakage (timing, counts, size buckets, direction — never content).
3. Confirmation she will run the 5-second secretless `/stats` hook once she authorizes it.
4. Confirmation a woken worker on her runtime can read a secret store (same shape as Aman).
5. When — and only when — she authorizes: her Ed25519 + X25519 **public** keys, fingerprints confirmed in person. Private keys never transit Aman, Anton, Cursor, or the relay.

## Need from Anton

1. Errata on the RAISE items, or an explicit “interpretations accepted, freeze stands.”
2. Do not treat this scratch repo as a production relay.
