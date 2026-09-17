# Anton deploy runbook — muse-mailbox Worker + DO + R2

Cursor does **not** deploy from the Pi. Anton deploys from Hatch after Aman confirms.

Guest model: Aman’s existing Cloudflare account (`REDACTED_CLOUDFLARE_ACCOUNT_ID`,
`REDACTED.workers.dev`). Unilateral availability control. E2E confidentiality is
unaffected (blind relay). Gauri never needs Cloudflare credentials.

**Do not deploy until Aman confirms.** Tokens from `/v1/admin/provision` must never
appear in chat, ingest, or this repo.

## Code transfer (pick one)

1. **Preferred if Access SSH works:** from Hatch,
   `scp -r aman@homelab-ssh:muse-mailbox/workers ./muse-mailbox-workers`
2. **Reliable without SSH:** Cursor chunks `workers/` over the relay, parts well under
   2900 chars (`scripts/relay_chunk.py`). Ask Cursor to start the transfer.
3. Tree on the Pi stays at `/home/aman/muse-mailbox/workers`.

## Setup order

1. **R2 subscription** — enable free R2 if not already (Anton: in progress).
2. **Create bucket** `muse-mailbox-blobs` in the same account (dashboard or
   `wrangler r2 bucket create muse-mailbox-blobs`).
3. Put the `workers/` tree on Hatch. `cd workers && npm install` (or use wrangler’s
   bundled deploy; `wrangler.toml` is the spec).
4. **Admin secret** (Hatch, never chat):
   ```
   wrangler secret put ADMIN_SECRET
   ```
   Generate a 32-byte random value; store it in a Hatch file mode 0600, not in git.
5. **Deploy**
   ```
   npx wrangler deploy
   ```
   Expected URL: `https://muse-mailbox.REDACTED.workers.dev`
6. **Health**
   ```
   curl -sS https://muse-mailbox.REDACTED.workers.dev/health
   ```
   Expect `{"ok":true,"protocol":"muse-mailbox/2"}`.
7. **Provision once** (prints tokens to stdout — redirect to a 0600 file, not a log):
   ```
   umask 077
   curl -sS -X POST \
     -H "Authorization: Bearer $ADMIN_SECRET" \
     https://muse-mailbox.REDACTED.workers.dev/v1/admin/provision \
     -o $HOME/.muse-mailbox/provision.json
   chmod 600 $HOME/.muse-mailbox/provision.json
   ```
   Re-running returns `409` (already provisioned). Do not paste this file anywhere.
8. **Split tokens without chat**
   - Aman’s send+fetch → stay on Hatch as `~/.muse-mailbox/card.json` (after the
     fingerprint ceremony fills public keys). Until then, keep `provision.json` 0600.
   - Gauri’s send+fetch → copy into a second 0600 file `~/.muse-mailbox/held-gauri-tokens.json`
     and hold until the in-person ceremony. **Do not send them to Gauri’s Muse yet.**
9. **Report over the relay (safe fields only):** Worker URL + `mailbox_id`.
   Never tokens, never `ADMIN_SECRET`.
10. Confirm with Cursor. Gauri’s hook/runtime remains gated.

## wrangler.toml (already in tree)

- `account_id = REDACTED_CLOUDFLARE_ACCOUNT_ID`
- Durable Object class `MailboxDO`, binding `MAILBOX`
- R2 binding `BLOBS` → bucket `muse-mailbox-blobs`
- Singleton DO name `muse-mailbox/2` (avoids creating a DO per guessed mailbox id)

## After deploy, before first real message

Aman Muse generates Ed25519 + X25519 on Hatch → `identity.json` 0600.
Gauri generates hers on her runtime when she authorizes.
In-person fingerprint ceremony fills `card.json`.
Secretless hook may be installed on Hatch only after Aman says the Worker is live;
Gauri’s hook waits on her go-ahead.
