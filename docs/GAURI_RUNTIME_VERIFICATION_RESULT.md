# Gauri Runtime Verification — Result (GREEN)

**Date run:** 2026-09-15, ~12:35–12:41 PDT. **Run by:** Gauri's Muse, in her own side chat.
**Reported to Aman:** 2026-09-16 (pasted in chat). Recorded here verbatim as the evidence
of record for the Cursor handoff.

## Purpose

Prove the frozen contract's load-bearing assumption: a secretless hook with
`poll_interval_secs = 5` can wake a full worker on Gauri's actual Muse runtime.

## Step 0 — Capability self-report: PASS

Event hooks supported via `hooks.add`. Minimum quoted from the tool schema:

```json
"poll_interval_secs": { "description": "Seconds between script polls. Defaults to 60.",
"maximum": 604800, "minimum": 5, "type": "integer" }
```

Hook scripts run without secrets — quoted from the runtime description: the script "runs
through the normal tracked shell executor with the same base command environment and
proxy setup as an ordinary shell command, but without secrets or connector credentials."

## Step 1 — Live wake test: PASS

Hook `hook-wake-test` installed with `poll_interval_secs: 5`, delivery defaulting to the
chat. The installed script contained no secrets, tokens, or credentials.

| Time (PDT) | Event |
|---|---|
| 12:35:53 | Dry run with no tripwire → silent, reason `no-tripwire` (correct) |
| 12:35:54 | Hook enabled, live polling at 5s |
| 12:35:55 | Tripwire `~/hook-test/GO` created |
| 12:36:04 | Woken worker appended timestamped line to `~/hook-test/WOKE.log` |
| 12:36:04 | "hook wake confirmed" relayed in chat |

**Wake latency: 9 seconds** (tripwire → worker-confirmed), inside the ~120s budget.
`WOKE.log` gained its timestamped line. Note: two poll cycles fired while the tripwire
existed (~12s window); the second worker verified after the file was already deleted and
logged anyway per its instructions — a late artifact of a pre-deletion wake, not a new wake.

## Step 2 — Silence control: PASS

Tripwire deleted 12:36:07 PDT. Over the next five minutes (observed until 12:41:11 PDT),
the hook's invocation log shows silent / `no-tripwire` on every 5-second poll — zero new
wakes. Wakes tracked the tripwire exactly: fired while it existed, silent after.

## Step 3 — Secretless shape confirmation: PASS

Script re-shown; it references no secret, token, password, API key, or credential.
Confirmed the same shape works against an unauthenticated HTTPS endpoint — the hook
runtime prescribes "bounded fetches such as `curl --fail --silent --show-error --max-time 10`
…" against public endpoints. A `GET /v1/mailboxes/{id}/stats → {"highest_seq": N}`
presence poll is the same shape.

## Step 4 — Cleanup: done

Hook definition removed (12:41:13 PDT); `~/hook-test/` and the hook script deleted. No
residue left on the runtime.

## Verdict: GREEN

All four steps pass with minimum interval = 5. The assumption is proven.

## Deviations from the protocol (recorded honestly by Gauri's Muse)

- Gauri pasted the whole protocol at once instead of step-by-step; her Muse executed every
  step — including creating and deleting the tripwire — on her behalf in one session.
- The protocol's "exit 0 (wake)" phrasing doesn't match the actual API: scripts must end
  with exactly one wake `reason 'payload'` or silent `reason 'payload'` call. Equivalent
  semantics were implemented.
- New hooks start disabled; per the runtime's required flow, her Muse dry-ran (silent, as
  expected) and then enabled before the live test.

None of these affect the contract — they are operational details of her runtime's hook API.

## Standing boundary (stated by Gauri's Muse, preserved)

> Verification passing is not authorization to build. The contract's next step — this
> runtime polling an external, unauthenticated mailbox URL and waking whenever it changes —
> means anyone able to write to that endpoint could wake Gauri's worker. I will not build
> the external wake leg without Gauri's explicit go-ahead, and it should carry
> authentication before anything real is built.

### Analyst note on the wake-authority concern (Aman's Muse, 2026-09-16)

The concern is real but bounded, and it does not require redesigning the oracle:

- **Wake authority == send authority.** The presence counter moves only when a message is
  POSTed, and POST requires a valid send token. The set of parties who can cause a wake is
  exactly the set who can already submit messages: Aman, Gauri, or a thief of a send token.
  No new privilege is created by the wake leg — an outsider with no token cannot move the
  counter at all.
- **The residual is wake-spam as compute DoS**, not a confidentiality or forgery issue: a
  send-token thief's garbage blobs fail signature verification and are quarantined; the
  cost is wasted worker wakes. This is already mitigated in the frozen contract by
  per-token rate limits (60 submits/min per send token) and alerting on authentication
  spikes (`PROTOCOL.md` §2.4, §4).
- **The oracle poll itself cannot carry authentication** — that is the point of the
  secretless hook (the runtime forbids secrets in hook scripts). The security boundary
  sits at the POST (send token), the fetch (fetch token), and the signature verification
  (pinned Ed25519 key). Authenticating the *read* of a public counter would add nothing:
  the counter's movement is already gated by authenticated writes.
- **Agreed unconditionally:** nothing is built on Gauri's side without Gauri's explicit
  go-ahead. Her runtime, her call. This verification was proof of capability, not consent
  to deploy.
