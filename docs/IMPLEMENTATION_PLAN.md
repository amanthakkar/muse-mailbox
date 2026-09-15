# Implementation plan (scratch done; production not authorized)

## Status

| Step | Status |
|---|---|
| Frozen contract ingested | Done — `PROTOCOL.md` from Anton’s 15-part handoff |
| Contract review | Done — `docs/CONTRACT_REVIEW.md` |
| Substrate evaluation | Done — **DO+R2**, `docs/SUBSTRATE.md` |
| Scratch harness | Done — `src/muse_mailbox/` |
| §9 test matrix | **11/11 pass** — `docs/RESULTS.md` |
| Provisioning design | Done — `docs/PROVISIONING.md` (not executed for real keys) |
| Production Worker deploy | **Not authorized** |
| Gauri-side hook | **Not authorized** |
| Anything on the Pi / amanthakkar.com | **Not authorized** |

## Scratch layout

```
src/muse_mailbox/
  crypto.py       # Ed25519, X25519-ECIES, AES-256-GCM, padding, fingerprints
  client.py       # send + two-cursor receive
  relay.py        # PROTOCOL §2 HTTP semantics
  store.py        # DO index + R2 blobs in SQLite WAL
  tokens.py       # mm1_{as,af,gs,gf}_ + SHA-256 at rest
  card.py         # per-recipient card; no private keys
  provision.py    # scratch-only mailbox mint
  http_server.py  # localhost adapter for kill/restart tests
workers/          # TypeScript sketch, wrangler.toml.example, 501 not_deployed
```

## Production build (when Aman authorizes a Cloudflare account)

1. Create a **new** Cloudflare account (not the homelab one). No DNS on `amanthakkar.com`. Start on `*.workers.dev`.
2. Port `relay.py` / `store.py` onto Worker + Mailbox DO + R2 using the write order in `workers/src/mailbox-do.ts`.
3. Re-run `tests/test_matrix.py` against the deployed **scratch** Worker (still fake keys, fake mailbox id). Same assertions.
4. Only then consider minting a real mailbox. Real keys are generated on each Muse, never here.

## Muse client (each side, independently)

Not built on Gauri’s runtime. Design only:

1. Secretless hook, `poll_interval_secs=5`:
   `curl --fail --silent --show-error --max-time 10 "$RELAY/v1/mailboxes/$ID/stats"`
   Wake if `highest_seq > last_seen_highest_seq` (persist that counter in a local file, no secrets).
2. Woken worker reads vault (see provisioning), `GET /messages?since=fetch_cursor`, handle, persist cursors.
3. Send path: worker-only, uses send token + private keys.

## Explicit non-goals until written authorization

- No `wrangler deploy`
- No real Ed25519/X25519
- No real `mm1_` tokens
- No hook on Gauri’s Muse
- No Cortex / Pi / tunnel involvement
