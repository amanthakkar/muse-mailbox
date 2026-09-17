# Neutral? No — guest-model operator + remaining needs

## Operator (decided 2026-09-16, then amended)

**Aman’s existing Cloudflare account** (`REDACTED_CLOUDFLARE_ACCOUNT_ID`,
`REDACTED.workers.dev`). Guest model: Aman holds availability. Blind relay still
hides content. Gauri never gets Cloudflare credentials.

Anton deploys from Hatch. Cursor does **not** deploy from the Pi and needs **no**
API token. See `docs/ANTON_DEPLOY.md`.

**Do not deploy until Aman confirms.**

## Done this round

- RAISE list folded in
- File-based custody designed
- Operator = Aman’s CF account (guest model)
- Production Worker port written (`workers/`)
- Deploy runbook for Anton
- Code-transfer options: Access SSH scp, or relay chunks

## Still gated

Gauri: go-ahead, metadata-leak ack, she installs the 5s hook, her 0600 home, in-person
public keys. Her tokens will be minted at provision and **held** on Hatch as 0600,
not delivered.

Aman: confirm deploy; after provision, generate Hatch identity keys; in-person ceremony.
