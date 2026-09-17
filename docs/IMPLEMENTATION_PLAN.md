# Implementation plan

## Status

| Step | Status |
|---|---|
| Frozen contract + 2026-09-16 errata | Done — `PROTOCOL.md` |
| Scratch harness + tests | Done — 12/12 |
| File-based custody | Done — `docs/PROVISIONING.md` |
| Operator | Aman’s existing CF account (guest model) |
| Production Worker + DO + R2 | Written — `workers/` (not deployed) |
| Deploy | **Anton from Hatch, after Aman confirms.** Cursor does not deploy. |
| Gauri-side hook | **Not authorized** |

## Production mapping

| Cloudflare | Code |
|---|---|
| Worker | `workers/src/index.ts` |
| Durable Object `MailboxDO` | `workers/src/mailbox-do.ts` |
| R2 `muse-mailbox-blobs` | ciphertext at `{mailbox_id}/mid/{message_id}` |
| wrangler | `workers/wrangler.toml` (account `REDACTED_CLOUDFLARE_ACCOUNT_ID`) |

Write order: R2 PUT first, then DO seq. Runbook: `docs/ANTON_DEPLOY.md`.
