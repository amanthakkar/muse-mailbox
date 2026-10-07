# Invite mechanism — implementation plan

**Status: design complete, client tooling shipped, relay endpoints pending.**
The design study with alternatives is in [`invite-design.md`](invite-design.md) —
three options compared, **Option A (relay-issued single-use codes)** recommended.
This document is the implementation plan for Option A: the relay endpoints
(`TODO(relay)` — specified, not yet implemented or deployed) and what
`scripts/setup_mailbox.py` already does client-side.

## Invite record

Relay-side, created by an existing participant (authenticated with their send token):

| Field | Content |
|---|---|
| `code` | the invite code, `inv_<32 random bytes, base64url>`, hashed at rest |
| `mailbox_id` | which mailbox the invite joins |
| `tokens` | a FRESH send+fetch credential pair for the invitee (never the inviter's) |
| `inviter_keys` | inviter's pinned Ed25519 + X25519 public keys + kids |
| `inviter_name` | human label, e.g. `"aman"` |
| `expires_at` | short TTL (24h default) |
| `max_uses` | 1 |
| `redeemed_by_keys` | filled at redeem time |

Full flow, security properties, and why the alternatives lost:
[`invite-design.md`](invite-design.md) §Option A.

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
