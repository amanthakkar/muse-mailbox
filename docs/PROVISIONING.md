# Connection-card provisioning + file-based key custody

Design only. No real keys, tokens, or mailbox ids are minted by this document.

Updated 2026-09-16 after Aman/Anton: Hatch Secure Vault is website logins and API
credential *cards*, consumed through their own flows. **There is no worker-readable
named-secret KV.** Provisioning is therefore files, not vault items.

## Principle

Three ceremonies, three places, no shared infrastructure:

1. **Relay operator** mints `mailbox_id` + four directional tokens. Never sees keypairs.
2. **Each Muse** generates its own Ed25519 + X25519 on-device. Private keys never leave that runtime. Never go in the card.
3. **Humans in person** swap public keys + fingerprints, then each human writes **their own** card into **their own** Muse home directory.

```
        in person (fingerprints aloud)
   Aman -------------------------------+----------------> Gauri
     |                                 |                   |
     | own files on Aman's VM          |                   | own files on Gauri's runtime
     v                                 v                   v
 Aman Muse                         Relay                Gauri Muse
  (0600 identity+card)         (tokens + ciphertext)    (0600 identity+card)
```

## What is on a card (PROTOCOL §7)

Per recipient, owner’s **own** token pair only:

- `relay_url`, `mailbox_id`, `owner`, `peer`
- `credentials.send` / `credentials.fetch` for the owner
- **both** participants’ public encryption + signing keys + fingerprints

Never: private keys, the peer’s tokens, passphrases, Pi hostnames, Cortex URLs.

## Fingerprint ceremony

`SHA-256( "muse-mailbox/2" ‖ 0x00 ‖ mailbox_id ‖ 0x00 ‖ participant_id ‖ 0x00 ‖ enc_pub ‖ 0x00 ‖ sig_pub )`

Display: 16 groups of 4 uppercase hex. Giver reads the **first 8 groups** aloud. Receiver recomputes from the card bytes (catches corruption) and compares the spoken prefix (catches whole-card swap).

Then a non-destructive liveness probe: `GET /messages?since=999999999&limit=1` with the fetch token, expect empty 200. Never send a test message.

## File layout (the load-bearing read path)

Hook scripts **cannot** hold secrets. The woken worker **must**. Hatch cannot hand the worker a vault KV, so:

`$MUSE_MAILBOX_HOME/` (directory mode `0700`), default `~/.muse-mailbox/`:

| File | Mode | Who opens it | Contents |
|---|---|---|---|
| `identity.json` | `0600` | woken worker only | Ed25519 seed + X25519 seed, raw 32-byte, base64 |
| `card.json` | `0600` | woken worker only | this Muse’s connection card (own tokens + both public keys) |
| `state.json` | `0600` | woken worker only | `fetch_cursor`, `process_cursor`, `next_sender_seq` |
| `last_seq` | `0644` | **hook and worker** | decimal `highest_seq` last observed. Not a secret. |

Scratch helpers: `src/muse_mailbox/custody.py` (`init_home`, `load_home`, `hook_script_is_safe`).

### Secretless hook (never names the 0600 files)

```sh
# hook: poll_interval_secs=5. No tokens. No identity.json. No card.json.
seq=$(curl --fail --silent --show-error --max-time 10 \
  "$RELAY_URL/v1/mailboxes/$MAILBOX_ID/stats" | python3 -c 'import sys,json;print(json.load(sys.stdin)["highest_seq"])')
last=$(cat "$MUSE_MAILBOX_HOME/last_seq" 2>/dev/null || echo 0)
if [ "$seq" -gt "$last" ]; then
  wake "mailbox" "$seq"     # runtime-specific wake call
else
  silent "no-change" "$seq"
fi
```

`$RELAY_URL` and `$MAILBOX_ID` in the hook are **not secrets** (mailbox id is unguessable but the oracle is public by design). They can live in the hook source or in `last_seq`’s sibling only if that sibling is also 0644 and contains no tokens. Prefer baking the public URL+id into the hook; keep tokens exclusively in `card.json`.

`hook_script_is_safe()` rejects any hook source that mentions `identity.json`, `card.json`, or `state.json`.

### Woken worker

1. `load_home($MUSE_MAILBOX_HOME)` — reads 0600 files, verifies card fingerprints.
2. Authenticated `GET /messages?since=fetch_cursor`.
3. Decrypt / verify / two-cursor dispatch.
4. Persist `state.json` and update `last_seq` (0644) so the hook stops waking.

### Accepted caveat (Anton, 2026-09-16)

Keys at rest on the VM disk are readable by **any worker in that runtime**. The boundary is hook-script integrity (secretless, never opens 0600 files), not a process-isolation vault. Bearer tokens could later ride a custom API-credential connector so the worker never sees the value; Ed25519/X25519 private keys need raw bytes, so files are unavoidable for those. v2 keeps tokens in `card.json` with the keys, one 0600 surface.

### Gauri Muse

Same layout, independently, on her runtime. Her go-ahead still closed — nothing is installed there. When she authorizes, she generates keys on her side, we do the in-person ceremony, she writes her own `~/.muse-mailbox/`.

## Token minting (operator — Anton on Hatch)

Production: `POST /v1/admin/provision` with `ADMIN_SECRET` (wrangler secret, never chat).
Returns `mailbox_id` + four plaintext tokens **once**. Redirect stdout to a Hatch file
mode `0600`. A 409 means already provisioned.

Aman’s two tokens stay on Hatch and are copied into `~/.muse-mailbox/card.json` after
the fingerprint ceremony (public keys filled in). Gauri’s two tokens go to a second
0600 file (`held-gauri-tokens.json`) and are **held** — nothing reaches her Muse until
she authorizes and the in-person ceremony happens.

Scratch: `provision_dev()` in `src/muse_mailbox/provision.py` is local-only and must
not be used for production tokens.

Do **not** deploy until Aman confirms. Cursor never sees production tokens.

Token rotation: operator replaces hashes inside the Durable Object (future admin
endpoint); the owner’s `card.json` is rewritten.

## Key replacement

- X25519: in-band `type: "key-rollover"` signed by the long-term Ed25519, 7-day grace. Monthly default.
- Ed25519: in-band announcement signed by the old key, **plus** in-person fingerprint at next meeting. Suspected compromise: **only** a fresh in-person card. Never auto-accept a signing-key rollover.

## What must never be exported

- Private keys (`identity.json`)
- The peer’s send/fetch tokens
- Hatch / Pi / Cloudflare homelab credentials
- The operator’s Cloudflare API token
- Any 0600 path in a hook script
