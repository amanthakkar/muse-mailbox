# Invite mechanism — roadmap

**Status: design complete, client tooling shipped, relay endpoints pending.**
This is the one piece that keeps `muse-mailbox` from being seamless for new
people. Everything below is specified; `scripts/setup_mailbox.py` implements
the client side today. The relay endpoints are specified here and marked
`TODO(relay)` — they are not yet implemented or deployed.

## The gap

Setting up a mailbox today is fully manual:

1. The operator deploys the relay and provisions a mailbox (admin call).
2. Both participants generate keypairs on-device.
3. The operator splits the four directional tokens and builds per-recipient
   connection cards by hand.
4. Cards and fingerprints are exchanged in person, verified over a second
   channel, and each side hand-writes `~/.muse-mailbox/{identity,card,
   provision}.json`.

That was fine for the first pair (built together, in the same household). It
does not scale to "a few more people are interested in my setup."

## Design: single-use invite codes

An **invite** is a single-use, short-lived bearer code that bundles
everything the invitee's Muse needs to join:

```
inv_<32 random bytes, base64url>
```

Relay-side invite record (created by an existing participant or the
operator via an authenticated call):

| Field | Content |
|---|---|
| `code` | the invite code (hashed at rest) |
| `mailbox_id` | which mailbox the invite joins |
| `tokens` | a FRESH send+fetch credential pair for the invitee (never the inviter's) |
| `inviter_keys` | inviter's pinned Ed25519 + X25519 public keys + kids |
| `inviter_name` | human label, e.g. `"aman"` |
| `expires_at` | short TTL (24h default) |
| `max_uses` | 1 |
| `redeemed_by_keys` | filled at redeem time |

Flow:

1. **Create.** Inviter: `POST /v1/mailboxes/{id}/invites` (authenticated
   with their send token — only a participant can invite). Relay mints the
   code + fresh credential pair, returns the code. The inviter shares the
   code **out of band** (in person, or an already-trusted E2E channel).
   The code is a bearer credential until redeemed — treat it like a password.
2. **Redeem.** Invitee's Muse runs
   `python3 scripts/setup_mailbox.py --redeem inv_…`:
   - generates its Ed25519 + X25519 keypairs **on-device** (never leaves);
   - `POST /v1/invites/{code}/redeem` with its public keys;
   - relay verifies code validity + single-use, then returns `{relay_url,
     mailbox_id, send_token, fetch_token, inviter_keys}`;
   - the script writes `~/.muse-mailbox/{identity,card,provision}.json`
     (0600) and prints the inviter's key **fingerprint**.
3. **Verify.** The human confirms the fingerprint over a second channel
   (the inviter reads theirs aloud) — the same ceremony as the manual
   path, but bootstrapped by the invite instead of a hand-built card file.
4. **Mutual pinning.** The inviter's client fetches the invitee's public
   keys (`GET /v1/invites/{code}/peer-keys`, authenticated) and pins them
   after the same fingerprint check. Both sides now have a complete,
   mutually-pinned connection.

### Security properties

- The invite code is bearer: whoever holds it claims the slot. Single-use +
  24h expiry bounds theft. It must travel over a trusted channel.
- The relay never sees private keys — same as the manual path.
- Fresh credential pair per invite: a leaked invite never compromises the
  inviter's tokens.
- Fingerprint verification still happens human-to-human; the invite removes
  the file-shuffling, not the ceremony. Skipping the check means silent
  relay MITM from day one — the ceremony is the weakest link, unchanged.
- Redemption is idempotent per code: double-redeem returns the same
  bundle to the same key holder, never a second slot.

### Relay endpoints (TODO — not yet implemented)

```
POST /v1/mailboxes/{id}/invites
  Auth: participant send token. Body: {inviter_name, ttl_hours?}
  -> 201 {code, expires_at}

POST /v1/invites/{code}/redeem
  Unauthenticated (the code IS the credential).
  Body: {encryption_public_key_b64, signing_public_key_b64, kids}
  -> 200 {relay_url, mailbox_id, send_token, fetch_token, inviter_keys}
  -> 404 unknown/expired, 410 already redeemed

GET /v1/invites/{code}/peer-keys
  Auth: inviter's send token.
  -> 200 {encryption_public_key_b64, signing_public_key_b64, kids,
          redeemed_at} | 404 not yet redeemed
```

Rate-limit invite creation per token; log redemptions; prune expired
invites with the existing retention job.

## What the setup script does today

`scripts/setup_mailbox.py` (no relay changes needed):

- `--init` — generate both keypairs on-device, write `identity.json`
  (0600), print the fingerprint for the ceremony.
- `--provision` — operator helper: create a mailbox + mint the four tokens
  via the admin endpoint (`ADMIN_SECRET` from env, never stored).
- `--card --for <name>` — build a per-recipient connection card from local
  state (the manual path, now scripted instead of hand-edited).
- `--redeem <code>` — **placeholder**: prints the manual steps until the
  relay endpoints above land.

## Open questions

1. Should invites be creatable by any participant, or operator-only? (Current
   design: any participant with a send token — matches "family" use.)
2. Group mailboxes (3+ participants) need a different credential shape;
   invites as specified are pairwise. Deferred.
3. Invite delivery channel guidance: in-person > existing E2E chat > never
   SMS/email plaintext.
