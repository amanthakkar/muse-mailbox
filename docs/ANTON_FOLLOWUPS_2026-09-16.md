# Follow-up asks from Anton (2026-09-16), after mailbox decisions

Three asks landed after the RAISE/vault/operator note. Later messages override earlier ones.

## 1. Operator changed (20:42Z) — then immediately superseded

Use **Aman’s existing Cloudflare account**, not a new joint account.

Guest model, not neutral: Aman holds unilateral availability (billing, suspension,
revocation). E2E confidentiality unchanged (blind relay). Gauri needs no Cloudflare
credentials, only her mailbox tokens via the in-person ceremony.

Cost: Workers + DO + R2 free tiers cover this (~35k presence req/day vs 100k free).

This ask also said “Cursor deploys from the Pi with a scoped API token.” **That is
void.** See (2).

## 2. Deploy path (20:43Z) — current

Aman asked Anton to do the Cloudflare work.

- Cursor does **not** deploy from the Pi. No API token on the Pi.
- Cursor delivers: production Worker + DO + R2, wrangler config, ordered setup steps,
  a code-transfer proposal (multipart < ~2900 chars, or better).
- Anton creates the R2 bucket, deploys, runs setup, confirms with Cursor **before**
  deploy, reports relay URL + mailbox_id through the relay (not tokens).
- File-based custody stands. Gauri’s tokens minted but held. Her side gated.
- No production until Aman confirms the deploy.

## 3. Account inventory (20:44Z)

Dashboard verified:

- Account ID: `REDACTED_CLOUDFLARE_ACCOUNT_ID`
- workers.dev: `REDACTED.workers.dev`
- Plan: Free
- Existing Workers: none. Existing R2 buckets: none.
- R2 subscription being enabled ($0).

Use that account ID in wrangler (done in `workers/wrangler.toml`).
