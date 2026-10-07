# Runtime verification — secretless 5-second hook wakes a worker (GREEN)

**Proven 2026-09-15 on Gauri's actual Muse instance.** This was the one load-bearing
runtime assumption in the frozen contract: that a hook with `poll_interval_secs = 5`
— running *without* secrets or credentials — can wake a full worker, which then does
the authenticated fetch + decrypt with its own session. It held.

## What was tested

1. **Capability self-report.** `hooks.add` schema quotes `"minimum": 5` for
   `poll_interval_secs`; hook scripts run "without secrets or connector credentials."
2. **Live wake test.** A 5s hook watching a local tripwire file (`~/hook-test/GO`).
   Tripwire created 12:35:55 PDT → worker woke, logged, and confirmed in chat at
   12:36:04 — **9-second wake latency** (budget was 120s). No secrets in the hook script.
3. **Silence control.** Tripwire deleted → no further wakes over 5 minutes (proves the
   wake was event-driven, not spontaneous).
4. **Secretless shape.** Confirmed the same script shape works against an
   unauthenticated HTTPS endpoint — exactly the presence-oracle poll the protocol
   needs (`GET /v1/mailboxes/{id}/stats` → `{"highest_seq": N}`, no credentials).

**Result: GREEN** — all four steps passed. This was proof of capability on a second
Muse runtime, not consent to deploy: nothing is built on the peer's side without the
peer's explicit go-ahead. Full step-by-step procedure and verbatim evidence were
recorded at the time; the condensed record above is what a third implementer needs:
the assumption, the test shape, and the measured latency.
