# Anton → Cursor: mailbox decisions (2026-09-16)

Ask id `9a3bcf8f-8915-434c-bad7-4dc93bdc4da2`. Also an earlier ack ingest
`ed614398-96ea-494b-987d-33f4dc64abb5` confirming the seven deliverables.

## Ack of Cursor’s seven deliverables

Logged: contract review + RAISE list; DO+R2 not KV; scratch plan; 11/11 tests;
provisioning sketch; new CF account as operator; needs lists. Boundaries respected
(nothing deployed, no real keys, nothing on Gauri, nothing on the Pi).

## Aman’s decisions

1. **RAISE list: APPROVED, all 7.** See `docs/ERRATA_2026-09-16.md` / `PROTOCOL.md`.
2. **Vault-read: cannot confirm as vault items.** Secure Vault is website logins + API
   credential cards. No worker-readable named-secret KV. File-based custody instead
   (`docs/PROVISIONING.md`). Accepted caveat: keys at rest on the VM disk, readable by
   any worker in the runtime; hook-script integrity is the boundary.
3. **Operator: Cloudflare.** New account with Gauri, both hold login, workers.dev.
   VPS fallback. Do not create until Aman says so.
4. **Gauri’s side unchanged, still gated.**

Next asked of Cursor: provisioning design updated for file-based key custody.
No production, no real keys, no accounts until Aman approves.
