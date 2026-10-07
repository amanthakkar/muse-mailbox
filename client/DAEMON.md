# Mailbox daemon (Aman side)

Deployed 2026-09-16; adaptive polling added 2026-09-19. Persistent local
daemon, cutting per-message latency from ~60–150s to tens of seconds.
Layer 1 (`muse-mailbox/2`) and all crypto untouched — this is a purely
local runtime change.

## Adaptive polling (2026-09-19)

Cloudflare Workers' free tier allows 100k requests/day; a flat 5s poll on
both sides costs ~35k/day. The daemon now polls fast (5s) for 120s after
any activity, then backs off to 30s when idle — cutting our share ~6x to
~6k/day. Activity = inbound counter movement, our own outbound sends
(sender_seq advance in state.json), or daemon start. Worst-case
first-message latency after a long silence is ~30s; active conversations
keep the fast feel. Mode transitions are logged ("poll mode -> fast/idle").

## Delivery safety net (2026-09-19)

The hook worker is the fast path, but on 2026-09-19 its report handoff
silently failed three times: relay seq 50, 51 (Iko replies, 10:54am) and
53 (Iko reply, 6:48pm) all reached spool/delivered/ without ever surfacing
in chat. Root cause undetermined — the workers ran (files moved
pending→delivered) but their summaries never arrived as handoffs.

Backstop: cron `mailbox-surface-safety-net` (every 5 min) runs
`hatch-client/surface_backlog.py`, which lists delivered messages whose
relay seq is not in `~/.muse-mailbox/surfaced.json` and prints their full
bodies for the main agent to relay.

ACK CONVENTION (main agent must follow): whenever you relay an Iko mailbox
message to Aman in chat — via either path — append its relay seq to
`~/.muse-mailbox/surfaced.json` (JSON list of ints, 0600). The backlog
script never marks anything surfaced itself.

Historical note: seqs 29–47 (Sept 15–16 game-era traffic) were marked
surfaced when the net was created; that week's messages were being
live-relayed at the time. Reliable per-message tracking starts at seq 48.

## How it works

```
relay /stats (public, adaptive 5s/30s) ──> mailbox_daemon.py ──> spool/new/*.json
      (secretless poll,      (fetch+decrypt+verify     (0600)
       honest UA)             inline, same cursors)
                                                        │
muse-mailbox-watch hook (5s, secretless):               │
  1. daemon alive? if not, restart it                   │
  2. move spool/new/* -> spool/pending/*, wake worker ──┘
                                                        │
thin worker: read pending/*.json -> report full body    │
to main agent -> move to spool/delivered/               │
```

- The daemon owns `~/.muse-mailbox/state.json` cursors/seen_ids. Nothing else
  fetches anymore, so no double-processing.
- The 5s hook never touches secrets or message bodies (filenames only).
- Heartbeats and game messages all flow through; the main agent decides
  what to surface (existing policy).

## Files

- `hatch-client/mailbox_daemon.py` — the daemon (`--once` = single cycle)
- `hatch-client/spool/{new,pending,delivered}/` — message spool (0600)
- `hatch-client/daemon.log` — log (auto-truncated)
- `~/.muse-mailbox/daemon.pid` — pidfile for the supervisor
- `~/hooks/scripts/muse-mailbox-stats.sh` — supervisor + spool watcher
  (old poller backed up at `.bak`)
- `~/hooks/definitions/muse-mailbox-watch.json` — worker prompt now
  "thin delivery" (old prompt backed up at `.bak`)

## Ops

- Status: `cat ~/.muse-mailbox/daemon.pid && ps -p $(cat ~/.muse-mailbox/daemon.pid) -o args=`
- Logs: `tail ~/workspace/secure-mailbox/hatch-client/daemon.log`
- Restart: `pkill -f mailbox_daemon.py` — the next 5s hook run restarts it.
- Health: if `spool/new/` grows without bound, the hook/worker path is stuck;
  check hook logs.

## Rollback

1. `pkill -f mailbox_daemon.py`
2. `cp ~/hooks/scripts/muse-mailbox-stats.sh.bak ~/hooks/scripts/muse-mailbox-stats.sh`
3. `cp ~/hooks/definitions/muse-mailbox-watch.json.bak ~/hooks/definitions/muse-mailbox-watch.json`
4. Old behavior (hook polls /stats, worker fetches) resumes on next 5s tick.
