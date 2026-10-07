# Gauri Runtime Verification — 5-Second Hook → Worker Wake

**Purpose:** prove, on Gauri's *actual* Muse instance, the one load-bearing runtime
assumption in the frozen contract (`PROTOCOL.md`): that a **secretless hook with
`poll_interval_secs = 5` can wake a full worker**. Aman's side is already verified;
Gauri's side is assumed similar and **must not** ship on assumption.

**Who runs this:** Gauri, in her own chat with her own Muse. Nobody else can reach her
runtime — there is deliberately no channel yet (that is what we are building). Aman
forwards this file to Gauri; she pastes the quoted prompts verbatim and records the
results.

**What "green" means:** all four steps below pass with timings recorded. Send the results
back to Aman; he unblocks the Cursor handoff on green.

---

## Step 0 — Capability self-report

Gauri pastes:

> Do you support event hooks via a `hooks.add` tool with a `poll_interval_secs`
> parameter? If yes: (1) quote the minimum allowed value for `poll_interval_secs`
> from the tool schema, (2) state whether hook scripts run with or without access to
> secrets and connector credentials.

**Record:** the verbatim answer.
**Pass:** minimum is 5 (or lower), and hook scripts run **without** secrets — the
secretless shape the presence-oracle design requires.

## Step 1 — Live wake test (no external infrastructure)

Gauri pastes:

> Create a hook with `poll_interval_secs` set to 5. The hook script must do only this:
> exit 0 (wake) if the file `~/hook-test/GO` exists, and stay silent otherwise. It must
> use no secrets, no tokens, no credentials of any kind. When woken, the worker must
> append one timestamped line to `~/hook-test/WOKE.log` and tell me in chat "hook wake
> confirmed". Do not delete the hook yet. Show me the exact hook script you installed.

Then Gauri creates the tripwire. She pastes:

> Create the file `~/hook-test/GO` (empty file is fine).

**Record:** the exact time the `GO` file was created, and the exact time her Muse's
"hook wake confirmed" message arrived in chat. Also confirm `~/hook-test/WOKE.log`
gained a timestamped line.

**Pass:** wake message arrives within ~120 seconds of file creation, and the log line
exists. (Expected typical: 5–30 s.)

## Step 2 — Silence control (proves wakes are event-driven)

Gauri pastes:

> Delete the file `~/hook-test/GO`.

**Record:** whether any further wake messages arrive over the next 5 minutes.

**Pass:** no further wakes. (Proves the worker woke because of the tripwire, not on its
own.)

## Step 3 — Secretless shape confirmation

Gauri pastes:

> Show me the hook script from Step 1 again and confirm: does it reference any secret,
> token, password, API key, or credential? Could this same script shape poll an
> unauthenticated HTTPS URL like `GET https://example.com/stats` instead of checking a
> file?

**Record:** the verbatim answer.
**Pass:** no secrets referenced; yes, the same shape works against an unauthenticated
HTTPS endpoint. (This is exactly the presence-oracle poll the frozen contract needs:
`GET /v1/mailboxes/{id}/stats` → `{"highest_seq": N}`, no credentials.)

## Step 4 — Cleanup

Gauri pastes:

> Delete the test hook and remove `~/hook-test/`.

---

## Scoring

| Result | Meaning | Consequence |
|---|---|---|
| All 4 steps pass, min interval = 5 | **GREEN** | Assumption proven. Cursor handoff unblocked. |
| Steps pass but min interval > 5 (e.g. 30 or 60) | **YELLOW** | Architecture still works; end-to-end latency becomes *interval + fetch*. Record the number in `PROTOCOL.md` §10 and proceed. |
| Hooks unsupported, or wake never fires | **RED** | Assumption false. Do **not** hand to Cursor. Fallback design needed for Gauri's side (e.g. cron-based polling at whatever her runtime supports — latency becomes minutes). Report back to Aman. |

## Notes for Gauri

- Nothing in this test touches any real mailbox, relay, or key — it is purely a test of
  *your* Muse's hook machinery using a local file as the tripwire.
- The hook script never sees secrets by design; that is the point being proven.
- If any step behaves unexpectedly, copy the exact messages/errors back to Aman rather
  than improvising — unexpected behavior is itself a finding.
