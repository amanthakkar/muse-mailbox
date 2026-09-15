# Connection-card provisioning + worker key read path

Design only. No real keys, tokens, or mailbox ids are minted by this document.

## Principle

Three ceremonies, three places, no shared infrastructure:

1. **Relay operator** mints `mailbox_id` + four directional tokens. Never sees keypairs.
2. **Each Muse** generates its own Ed25519 + X25519 on-device. Private keys never leave that runtime’s secret store. Never go in the card.
3. **Humans in person** swap public keys + fingerprints, then each human pastes **their own** card into **their own** Muse. Aman does not hand Gauri’s Muse anything through his Muse, and vice versa.

```
        in person (fingerprints aloud)
   Aman -------------------------------+----------------> Gauri
     |                                 |                   |
     | own card into Aman's vault      |                   | own card into Gauri's vault
     v                                 v                   v
 Aman Muse                         Relay                Gauri Muse
  (private keys)              (tokens + ciphertext)     (private keys)
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

## Where private keys live (the load-bearing read path)

Hook scripts **cannot** hold secrets. The woken worker **must**.

### Aman Muse (Hatch)

Proposed vault layout (names, not an API claim — Hatch’s Secure Vault should be verified at build time):

| Secret name | Contents | Readable by |
|---|---|---|
| `muse-mailbox/identity` | Ed25519 seed + X25519 seed, 0600 equivalent | woken worker only |
| `muse-mailbox/card-gauri` | Aman’s connection card JSON (public keys + Aman’s tokens) | woken worker only |
| `muse-mailbox/cursors` | `fetch_cursor`, `process_cursor`, `last_seen_highest_seq` | woken worker (durable state; not a key) |

Hook: a local file `~/muse-mailbox/last_seq` **or** a comparison against the last observed public counter stored without credentials. Prefer: hook curls `/stats`, compares to a world-readable file the worker updates after drain. The file is not a secret.

If Hatch workers read named vault items the way other Hatch skills do, this is the path. **Need from Aman:** confirm a woken Hatch worker can `vault.read` those two secrets. If not, we need another Hatch-native secret surface before go-live.

### Gauri Muse

Same shape, independently:

| Secret name | Contents |
|---|---|
| `muse-mailbox/identity` | her key seeds |
| `muse-mailbox/card-aman` | her card (her tokens + both public keys) |

**Need from Gauri’s Muse (via Aman, after her go-ahead):** confirm a woken worker can read those secrets, and that she will install the 5s presence hook herself.

There is no shared vault. Copy-pasting a card through WhatsApp/Muse chat is forbidden — in person, or a passphrase-protected file on a USB stick handed over.

## Token minting (operator)

Scratch: `provision_dev()` in `src/muse_mailbox/provision.py`.

Production: a one-shot `mailboxctl init` run by the **neutral Cloudflare account** holder, outputting two card templates with empty `keys` blocks. Each Muse fills in public keys, humans do the fingerprint ceremony, each side writes the completed card into their vault.

Token rotation: operator replaces one directional token atomically in DO storage; the owner’s card is re-issued (in person if signing keys also change; over a trusted channel if only the token changed — prefer in person anyway).

## Key replacement

- X25519: in-band `type: "key-rollover"` signed by the long-term Ed25519, 7-day grace. Monthly default.
- Ed25519: in-band announcement signed by the old key, **plus** in-person fingerprint at next meeting. Suspected compromise: **only** a fresh in-person card. Never auto-accept a signing-key rollover.

## What must never be exported

- Private keys
- The peer’s send/fetch tokens
- Hatch / Pi / Cloudflare homelab credentials
- The operator’s Cloudflare API token (lives in the operator account, not in either Muse)
