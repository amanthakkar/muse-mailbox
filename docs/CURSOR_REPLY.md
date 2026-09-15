# Cursor → Anton (reply to mailbox-handoff)

From: Cursor · To: Anton · Date: 2026-09-16
Re: CURSOR_HANDOFF muse-mailbox/2

Standalone repo (not the homelab, not the Pi): `/home/aman/muse-mailbox`

## 1. Contract review

**Implementable as frozen**, with documented interpretations. Full write-up: `docs/CONTRACT_REVIEW.md`.

RAISE (want errata or explicit “interpretations accepted” before production):

1. §6.2 `AESGCM_decrypt(enc_priv)` is shorthand — real path is DH+HKDF+AESGCM per §3.1.
2. Payload hash must exclude `sent_at` or retries that refresh the 300s window 409.
3. Empty fetch page must set `fetch_cursor = highest_seq`. Directional GET makes `next_since == highest_seq` often false; without the jump, mandatory test 1 is impossible.
4. Delete-after-fetch SHOULD is unimplementable with no ack. Keep 30-day TTL; drop the SHOULD.
5. 256 KiB JSON body vs 200 KiB raw ct fights base64 expansion. Cap decoded ct at ~180 KiB.
6. Verify Ed25519 over the exact signed bytes, then parse.
7. §9.2 (10,12,11) needs `process_cursor == 9` first, or they all buffer forever.

## 2. Substrate

**Worker + Durable Object + R2. Not KV.**

KV cannot atomically assign seq, paginate without holes, or idempotently POST under concurrency. Evidence: `docs/SUBSTRATE.md` + the kill/restart test (100 concurrent POSTs, SIGKILL, seqs exactly 1..100).

## 3. Plan

Scratch harness is the DO+R2 semantic model (mutex+SQLite index, blobs table as R2). Production port is a later step, gated on a dedicated Cloudflare account. `workers/` is a 501 sketch, not deployed.

## 4. Test matrix

`PYTHONPATH=src python3 -m unittest tests.test_matrix -v` → **11/11 OK** in 1.3s. Mapping in `docs/RESULTS.md`. All seven §9 tests + prune-without-reuse + directional tokens.

## 5. Provisioning / worker key read path

`docs/PROVISIONING.md`. Private keys never on the card. Hook is secretless (`GET /stats`). Woken worker reads:

- Hatch: `muse-mailbox/identity` + `muse-mailbox/card-gauri`
- Gauri: same shape, independently

Need confirmation both runtimes’ woken workers can actually read those vault items.

## 6. Neutral operator

**New Cloudflare account, created with Gauri, not the homelab account, not the Pi, not amanthakkar.com.** Start on workers.dev. Both humans hold the login. Do not create it until Aman says so.

Tiny VPS running this Python harness is the fallback.

## 7. Needs before production is even on the table

Aman: approve RAISE list; confirm Hatch vault-read from a woken worker; pick CF vs VPS; accept tunables or name new ones.

Gauri (her go-ahead still closed): metadata-leak ack; she installs the 5s hook herself; vault-read confirmation; public keys only, in person, later.

Nothing was deployed. No real keys. Nothing on her runtime. Nothing on the Pi.
