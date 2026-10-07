# Invite mechanism — design study

**Status:** proposal. The current implementation plan (relay-issued single-use
codes) is specified in [`INVITE_ROADMAP.md`](INVITE_ROADMAP.md); this document
walks through the design space, compares three concrete options, and records
the recommendation. No relay endpoints are deployed yet — design and
client-side prototype only.

## Why this matters

Setup today is fully manual: the operator deploys the relay, provisions a
mailbox, both sides generate keypairs on-device, and connection cards plus
fingerprints are exchanged in person and hand-written into config files. That
worked for the first pair (same household, built together). It does not scale
to "a few more people are interested in my setup." The invite mechanism is the
one piece standing between the protocol and seamless onboarding.

Design constraints, carried over from the frozen contract:

- Private keys are generated on-device and **never leave the device** — no
  option may move key generation server-side.
- The relay stays blind: it never sees plaintext, private keys, or anything
  beyond routing metadata and the presence counter.
- The human fingerprint check is the weakest link and must survive in every
  option. An invite removes file-shuffling, never the ceremony.
- Pairwise mailboxes only (group mailboxes are deferred).

## Option A — Relay-issued single-use invite codes (recommended)

An **invite** is a single-use, short-lived bearer code that bundles everything
the invitee's Muse needs to join: `inv_<32 random bytes, base64url>`.

**Step by step:**

1. **Create.** An existing participant (or the operator) calls
   `POST /v1/mailboxes/{id}/invites`, authenticated with their send token.
   The relay mints the code plus a **fresh** send+fetch credential pair for
   the invitee (never the inviter's), stores `{code_hash, mailbox_id,
   tokens, inviter_keys, inviter_name, expires_at (24h default), max_uses: 1}`,
   and returns the code.
2. **Share.** The inviter passes the code **out of band** — in person, or an
   already-trusted E2E channel. The code is a bearer credential until
   redeemed; treat it like a password.
3. **Redeem.** The invitee's Muse runs
   `python3 scripts/setup_mailbox.py --redeem inv_…`, which generates its
   Ed25519 + X25519 keypairs on-device and calls
   `POST /v1/invites/{code}/redeem` with its public keys. The relay verifies
   validity + single-use, burns the code, and returns `{relay_url,
   mailbox_id, send_token, fetch_token, inviter_keys}`. The script writes
   `~/.muse-mailbox/{identity,card,provision}.json` (0600) and prints the
   inviter's key fingerprint.
4. **Verify.** The human confirms the fingerprint over a second channel (the
   inviter reads theirs aloud) — same ceremony as the manual path.
5. **Mutual pinning.** The inviter's client fetches the invitee's public keys
   (`GET /v1/invites/{code}/peer-keys`, authenticated) and pins them after
   the same fingerprint check. Both sides now hold a complete, mutually-pinned
   connection.

**Security properties:**

- Bearer code, single-use, 24h expiry: theft is bounded in time and in
  effect (one slot, then burned). Double-redeem returns the same bundle to
  the same key holder — never a second slot.
- Fresh credential pair per invite: a leaked invite never compromises the
  inviter's tokens.
- The relay never sees private keys — identical to the manual path.
- Fingerprint verification still happens human-to-human; skipping it means
  silent relay MITM from day one, unchanged.

**What the relay learns:** that an invite was minted/redeemed, timing, and the
invitee's public keys (already part of the protocol's pinned-key model).
Nothing new beyond the manual path.

**UX friction:** medium-low. The inviter runs one command and passes a code
through a channel they already trust; the newcomer runs one script and does
one fingerprint check. The code is 47 characters (`inv_` + 43 base64url) —
copy/paste, not memorization.

**Code changes:** relay — three endpoints (`POST …/invites`, `POST
…/invites/{code}/redeem`, `GET …/invites/{code}/peer-keys`) plus expiry
pruning in the retention job and per-token rate limits on minting; client —
implement `--redeem` in `scripts/setup_mailbox.py` (placeholder today);
docs — this file plus the roadmap.

## Option B — Connection-card / QR ceremony (one-tap manual)

Keep the manual card exchange, but make it one-tap: the inviter's client
generates a card file (or QR code) the newcomer's Muse scans or pastes. The
card bundles `{relay_url, mailbox_id, send_token, fetch_token,
inviter_keys, fingerprint}` — everything the manual path hand-writes, in one
blob.

**Step by step:**

1. **Build.** Inviter (or operator): `setup_mailbox.py --card --for <name>
   --qr` produces a card file and/or QR.
2. **Transfer.** The card travels out of band (in person, trusted E2E
   channel). The card file **is** the credential — whoever holds it can join.
3. **Import.** Newcomer's client ingests the card, generates its keypairs
   on-device, writes config, prints the fingerprint.
4. **Verify.** Same human fingerprint ceremony as every other option.

**Security properties:**

- The card is a bearer credential with **no burn and no expiry** unless the
  relay is taught to track cards — a copied card means two devices sharing
  one token pair (signatures still distinguish senders, but a shared fetch
  token means both see all inbound; messy and hard to revoke cleanly).
- No new relay trust: if the operator pre-provisions the token pair, the
  relay needs **zero** changes. If participants must mint their own pairs,
  B converges on A's mint endpoint and loses its simplicity advantage.

**What the relay learns:** nothing new (with operator provisioning).

**UX friction:** lowest of the three — one scan, one check. But it does not
scale: every new person still needs the operator (or someone with admin) in
the loop to provision the token pair, which is exactly the bottleneck the
invite is meant to remove.

**Code changes:** client only — card builder + QR render + importer. Relay:
none (operator-provisioned) or A's mint endpoint (self-serve).

**Honest assessment:** B is the right fallback for air-gapped or
operator-run setups, and the QR rendering is worth building regardless (it
also improves the manual path). As the *primary* invite mechanism it keeps
the human operator as the scaling bottleneck and has the weakest theft
semantics.

## Option C — Relay-mediated pairing handshake

The inviter posts an **open pairing offer**; the joiner claims it with a
pre-shared word; the relay binds a fresh credential pair at claim time.

**Step by step:**

1. **Offer.** Inviter (authenticated): `POST /v1/mailboxes/{id}/pairing`
   `{inviter_name, word_hash, ttl_hours}`. The relay stores the offer
   (word stored as a salted hash, never plaintext).
2. **Share.** The inviter tells the newcomer a short word **in person**
   (e.g. spoken). The word is low-entropy by design — memorable, not a
   password.
3. **Claim.** Newcomer's client generates keypairs on-device and calls
   `POST /v1/pairing/{offer_id}/claim {word, pubkeys}`. The relay verifies
   the word (constant-time compare against the hash), enforces attempt caps,
   mints a fresh credential pair, binds it, burns the offer, and returns
   the bundle — same shape as A's redeem response.
4. **Verify.** Same human fingerprint ceremony.

**Security properties:**

- The word is brute-forceable in principle: this option **requires**
  strict rate limiting (e.g. 5 attempts per offer, then lockout), short
  TTLs (hours, not days), and single-use burn. With those, the practical
  risk is bounded — but it is real attack surface A does not have.
- Fresh credential pair at claim time: same theft bounds as A *after* a
  successful claim.
- The word must travel over a trusted channel, same as A's code — but a
  spoken word leaks more easily (overheard, guessed from context) than a
  43-character random string.

**What the relay learns:** offer/claim timing, invitee public keys — same as A.

**UX friction:** lowest *transfer* friction (a spoken word, nothing to
copy), but the newcomer still runs the setup script and the fingerprint
check. The word must be communicated carefully ("say it once, in person").

**Code changes:** relay — offer create/claim endpoints, attempt counters,
lockout, expiry pruning; client — claim flow in the setup script; docs.

**Honest assessment:** C optimizes the transfer step at the cost of genuine
new attack surface (low-entropy secret on the relay). It is the right choice
only if human-memorable pairing words are a hard requirement. For the
"family and friends" threat model it is defensible with rate limits — but A
achieves the same UX with strictly better security, because copying a code
is not meaningfully harder than hearing a word when both travel over a
trusted channel anyway.

## Comparison

| | A: single-use codes | B: card / QR | C: pairing handshake |
|---|---|---|---|
| Transfer secret entropy | 256-bit bearer | bearer file (no burn) | human word (low) |
| Theft bound | single slot, 24h, burned | unbounded until revoked | single slot, rate-limited |
| Relay changes | 3 endpoints + pruning | none (operator path) | 2 endpoints + attempt caps |
| Scales past the operator | yes | no | yes |
| New attack surface | none beyond manual path | shared token pairs | brute-forceable word |
| UX friction | copy a code + 1 check | scan + 1 check | hear a word + 1 check |
| Ceremony preserved | yes | yes | yes |

## Recommendation

**Option A.** It is already specified end-to-end in
[`INVITE_ROADMAP.md`](INVITE_ROADMAP.md) (endpoints, setup-script
placeholder, open questions), it adds no attack surface beyond the manual
path, and it removes the operator bottleneck — the actual scaling problem.
Build B's QR rendering as a complement (it improves the manual and fallback
paths for free). Take C only if a future user insists on spoken-word
pairing; the rate-limiting machinery is not worth it today.

**Explicit non-goal:** no production relay deploy happens as part of this
design. Relay endpoints are specified, not shipped — deploying them needs
Aman's explicit approval per his standing rule on shared-infra changes.

## Open questions (carried)

1. Invites creatable by any participant, or operator-only? (Current: any
   participant with a send token — matches "family" use.)
2. Group mailboxes (3+ participants) need a different credential shape;
   invites as specified are pairwise. Deferred.
3. Invite delivery channel guidance: in person > existing E2E chat > never
   SMS/email plaintext.

## Peer input (Iko)

*Pending — consult sent 2026-10-06 (relay seq 472). To be filled in from her
reply: her pick among A/B/C for onboarding a non-technical second human and
why; what would make onboarding seamless in her setup.*
