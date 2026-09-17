# Contract review — muse-mailbox/2

**Verdict: implementable as frozen.** The seven RAISE items were **approved by Aman on 2026-09-16** and folded into `PROTOCOL.md` (`docs/ERRATA_2026-09-16.md`).

Scratch harness + full §9 matrix: `tests/test_matrix.py`. See `docs/RESULTS.md`.

## What is solid

- Sign-then-encrypt, per-Muse Ed25519 + X25519, fresh ephemeral per message, HKDF-SHA256 → AES-256-GCM.
- AAD canonical JSON of `{eph, from, mailbox_id, message_id, to, v}` (sorted keys). Tamper matrix for all six fields plus `ct` fails decrypt.
- Four directional tokens, token (not envelope) is authority.
- Two-cursor receive: `fetch_cursor` vs per-peer `process_cursor`. Out-of-order `10,12,11` buffers then dispatches contiguously.
- Presence oracle is unauthenticated `{"highest_seq": N}` only.
- Idempotent POST by `message_id`; `409` on payload conflict.
- Prune never rewinds `highest_seq`.

## Interpretations applied in the scratch implementation

These are the only workable readings of underspecified sentences. They are **not** protocol changes.

| Spec text | Interpretation | Why |
|---|---|---|
| §6.2 `AESGCM_decrypt(k, …)` over X25519 private keys | DH(`enc_priv`, `eph`) → HKDF → AES-GCM, trying active then grace keys | §3.1 is the exact construction; §6.2 is shorthand. Decrypting with a DH private key is not defined. **RAISE:** reword §6.2. |
| “identical payload hash” | SHA-256 of canonical JSON of `{v,from,to,message_id,eph,nonce,ct}` — **`sent_at` excluded** | If `sent_at` is in the hash, a retry that refreshes the 300s freshness window `409`s a legitimate retry. Idempotency check already runs before freshness, but a first-time retry after 300s still needs a new timestamp. **RAISE:** name the hashed fields. |
| §6.2 empty fetch page | set `fetch_cursor = highest_seq` and stop | Directional GET omits the other direction, so `next_since == highest_seq` is often false (last mailbox seq may be outbound). Without the jump, `fetch_cursor == highest_seq` (mandatory test 1) is impossible and the hook re-wakes forever on the peer’s outbound traffic. **RAISE:** write this into §6.2. |
| §6.2 verify `canonical(inner_bytes)` after `json.loads` | verify Ed25519 over the **exact extracted bytes**, then require `canonical_json(parsed) == those bytes` | Re-dumping after parse is not guaranteed identical for all Unicode. **RAISE:** “verify the signed bytes, then parse.” |
| Unknown mailbox `GET …/stats` | `200 {"highest_seq": 0}` | Avoids an existence oracle. Contract unspecified. |
| Padding | `len_be32(sig‖inner) ‖ sig ‖ inner ‖ zero-pad` to `{1024,4096,16384}` | Matches “prefix 4-byte big-endian length.” |
| Receiver AAD `mailbox_id` | the client’s **configured** id (card), not a per-row field | Fetch rows do not carry `mailbox_id`. Serving a blob under a different mailbox id changes the configured id used for AAD → `InvalidTag`. |

## Holes that must not be papered over (RAISE to Anton)

1. **Delete-after-fetch is unimplementable with the current API.** There is no ack. `GET ?since=N` is not a durable receipt — the client may crash before persisting `fetch_cursor`. The 30-day / 100k cap is the real retention. Either drop the SHOULD, or add a signed cursor ack (which the contract explicitly refused). Recommend: drop the SHOULD, keep 30-day TTL, clients fetch promptly.

2. **256 KiB HTTP body vs 200 KiB raw `ct`.** Base64 expands 4/3, so 200 KiB raw `ct` is ~267 KiB of JSON field and overflows the 256 KiB body cap. Practical max `ct` is ~190 KiB. **RAISE:** set one number (suggest: HTTP body 256 KiB, decoded `ct` ≤ 180 KiB).

3. **No mailbox provisioning API.** Cards, tokens, and mailbox ids have to come from an out-of-band operator tool. That is fine if we admit it. See `docs/PROVISIONING.md`.

4. **Inner payload does not bind `mailbox_id`.** AAD does. Defense-in-depth would duplicate `mailbox_id` inside the signed inner. Not required to freeze; worth v2.1.

5. **§9 test 2 (`sender_seq` 10,12,11 → cursor ends at 12) is only true if `process_cursor` is already 9.** From 0, 10/12/11 all buffer and never dispatch (gap policy: never auto-skip). The scratch test sends 1..9 first, then 10,12,11. **RAISE:** state the precondition.

6. **Hook vs worker cursor.** The secretless hook must persist `last_seen_highest_seq` independently of `fetch_cursor`. Otherwise every outbound message of the peer wakes the worker for an empty fetch. Harmless but wasteful; specify it in the client runbook.

7. **Token hashing / storage.** Contract says opaque 256-bit `mm1_` values but not whether the relay stores them hashed. Scratch stores SHA-256 of the token. Recommend that as errata.

## Not holes

- Wake-spam from a stolen send token: already bounded by 60 POSTs/min and signature quarantine. Agreed with Anton’s analysis.
- Presence-oracle metadata: accepted, mailbox ids ≥128-bit random (`mb_` + 32 hex chars = 128 bits).
- No forward secrecy: accepted, with rotation + TTL.
- Gauri runtime 5s hook: GREEN, not consent to deploy.
